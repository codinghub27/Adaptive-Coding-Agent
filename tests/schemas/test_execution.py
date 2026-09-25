"""Tests for the sandbox execution request/result Pydantic schemas.

Pure schema tests: no docker, no code execution. `HarnessReport` is exercised
against the exact JSON shape `docker/harness/run.py` emits on its report
line, to guarantee the two stay in lockstep.
"""

import pytest
from pydantic import ValidationError

from app.schemas import (
    MAX_CODE_CHARS,
    CaseResult,
    ExecutionRequest,
    ExecutionResult,
    HarnessReport,
    TestCase,
    TestSuite,
    Verdict,
)

# --------------------------------------------------------------------------
# ExecutionRequest / TestSuite limits
# --------------------------------------------------------------------------


def _suite(**overrides: object) -> TestSuite:
    params: dict[str, object] = {
        "entrypoint": "solve",
        "cases": [TestCase(name="case_1", args=[1, 2], expected=3)],
    }
    params.update(overrides)
    return TestSuite(**params)  # type: ignore[arg-type]


def test_execution_request_valid_defaults() -> None:
    req = ExecutionRequest(code="def solve(a, b):\n    return a + b\n")
    assert req.language == "python"
    assert req.tests is None
    assert req.timeout_s == 5.0


def test_execution_request_rejects_empty_code() -> None:
    with pytest.raises(ValidationError):
        ExecutionRequest(code="")


def test_execution_request_rejects_oversized_code() -> None:
    with pytest.raises(ValidationError):
        ExecutionRequest(code="x" * (MAX_CODE_CHARS + 1))


def test_execution_request_accepts_max_code_length() -> None:
    req = ExecutionRequest(code="x" * MAX_CODE_CHARS)
    assert len(req.code) == MAX_CODE_CHARS


@pytest.mark.parametrize("timeout_s", [0.0, 0.49, 30.01, 100.0, -1.0])
def test_execution_request_rejects_out_of_bounds_timeout(timeout_s: float) -> None:
    with pytest.raises(ValidationError):
        ExecutionRequest(code="pass", timeout_s=timeout_s)


@pytest.mark.parametrize("timeout_s", [0.5, 5.0, 30.0])
def test_execution_request_accepts_bounded_timeout(timeout_s: float) -> None:
    req = ExecutionRequest(code="pass", timeout_s=timeout_s)
    assert req.timeout_s == timeout_s


@pytest.mark.parametrize("entrypoint", ["1bad", "bad-name", "bad name", "", "x" * 129])
def test_test_suite_rejects_bad_entrypoint(entrypoint: str) -> None:
    with pytest.raises(ValidationError):
        _suite(entrypoint=entrypoint)


@pytest.mark.parametrize("entrypoint", ["solve", "_solve", "solve_2", "Solve"])
def test_test_suite_accepts_valid_entrypoint(entrypoint: str) -> None:
    suite = _suite(entrypoint=entrypoint)
    assert suite.entrypoint == entrypoint


def test_test_suite_rejects_duplicate_case_names() -> None:
    with pytest.raises(ValidationError):
        _suite(
            cases=[
                TestCase(name="dup", args=[1], expected=1),
                TestCase(name="dup", args=[2], expected=2),
            ]
        )


def test_test_suite_rejects_zero_cases() -> None:
    with pytest.raises(ValidationError):
        _suite(cases=[])


def test_test_suite_rejects_too_many_cases() -> None:
    cases = [TestCase(name=f"c{i}", args=[i], expected=i) for i in range(101)]
    with pytest.raises(ValidationError):
        _suite(cases=cases)


def test_test_suite_accepts_max_cases() -> None:
    cases = [TestCase(name=f"c{i}", args=[i], expected=i) for i in range(100)]
    suite = _suite(cases=cases)
    assert len(suite.cases) == 100


def test_execution_request_with_tests() -> None:
    req = ExecutionRequest(code="def solve(a, b):\n    return a + b\n", tests=_suite())
    assert req.tests is not None
    assert req.tests.entrypoint == "solve"


# --------------------------------------------------------------------------
# HarnessReport: must parse the exact harness wire shape
# --------------------------------------------------------------------------


def test_harness_report_parses_tests_phase_body() -> None:
    body: dict[str, object] = {
        "version": 1,
        "phase": "tests",
        "error": None,
        "stdout": "",
        "stderr": "",
        "cases": [
            {
                "name": "case_1",
                "passed": True,
                "actual": [1, 2, 3],
                "actual_repr": "[1, 2, 3]",
                "error": None,
                "stdout": "",
                "duration_ms": 0.123,
            },
            {
                "name": "case_2",
                "passed": False,
                "actual": None,
                "actual_repr": "",
                "error": {
                    "type": "ZeroDivisionError",
                    "message": "division by zero",
                    "lineno": 4,
                },
                "stdout": "partial output\n",
                "duration_ms": 1.5,
            },
        ],
    }
    report = HarnessReport.model_validate(body)
    assert report.phase == "tests"
    assert report.error is None
    assert len(report.cases) == 2
    assert report.cases[0].actual == [1, 2, 3]
    assert report.cases[1].error is not None
    assert report.cases[1].error.type == "ZeroDivisionError"
    assert report.cases[1].error.lineno == 4


def test_harness_report_parses_compile_error_body() -> None:
    body: dict[str, object] = {
        "version": 1,
        "phase": "compile",
        "error": {"type": "SyntaxError", "message": "invalid syntax", "lineno": 1},
        "stdout": "",
        "stderr": "",
        "cases": [],
    }
    report = HarnessReport.model_validate(body)
    assert report.phase == "compile"
    assert report.error is not None
    assert report.error.type == "SyntaxError"


def test_harness_report_rejects_unknown_top_level_key() -> None:
    body: dict[str, object] = {
        "version": 1,
        "phase": "script",
        "error": None,
        "stdout": "",
        "stderr": "",
        "cases": [],
        "bogus": "nope",
    }
    with pytest.raises(ValidationError):
        HarnessReport.model_validate(body)


def test_harness_report_rejects_unknown_case_key() -> None:
    body: dict[str, object] = {
        "version": 1,
        "phase": "tests",
        "error": None,
        "stdout": "",
        "stderr": "",
        "cases": [
            {
                "name": "case_1",
                "passed": True,
                "actual": 1,
                "actual_repr": "1",
                "error": None,
                "stdout": "",
                "duration_ms": 0.1,
                "bogus": "nope",
            }
        ],
    }
    with pytest.raises(ValidationError):
        HarnessReport.model_validate(body)


# --------------------------------------------------------------------------
# ExecutionResult
# --------------------------------------------------------------------------


def test_execution_result_first_failure_and_counts() -> None:
    result = ExecutionResult(
        status="failed",
        phase="tests",
        cases=[
            CaseResult(name="c1", passed=True, actual=1, duration_ms=0.1),
            CaseResult(name="c2", passed=False, actual=2, duration_ms=0.2),
            CaseResult(name="c3", passed=False, actual=3, duration_ms=0.3),
        ],
    )
    assert result.cases_total == 3
    assert result.cases_passed == 1
    first = result.first_failure
    assert first is not None
    assert first.name == "c2"


def test_execution_result_first_failure_none_when_all_pass() -> None:
    result = ExecutionResult(
        status="passed",
        phase="tests",
        cases=[CaseResult(name="c1", passed=True, actual=1, duration_ms=0.1)],
    )
    assert result.first_failure is None
    assert result.cases_passed == result.cases_total == 1


def test_case_result_actual_sha256_and_truncated_defaults() -> None:
    case = CaseResult(name="c1", passed=True, actual=1, duration_ms=0.1)
    assert case.actual_sha256 is None
    assert case.actual_truncated is False


def test_case_result_accepts_actual_sha256_and_truncated() -> None:
    case = CaseResult(
        name="c1",
        passed=False,
        actual=None,
        actual_sha256="a" * 64,
        actual_truncated=True,
        duration_ms=0.1,
    )
    assert case.actual_sha256 == "a" * 64
    assert case.actual_truncated is True


def test_execution_result_defaults() -> None:
    result = ExecutionResult(status="completed")
    assert result.cases == []
    assert result.cases_total == 0
    assert result.cases_passed == 0
    assert result.first_failure is None


# --------------------------------------------------------------------------
# Verdict invariants
# --------------------------------------------------------------------------


def test_verdict_pass_requires_no_category_and_matching_counts() -> None:
    verdict = Verdict(status="pass", summary="all cases passed", cases_passed=3, cases_total=3)
    assert verdict.category is None


def test_verdict_pass_rejects_category() -> None:
    with pytest.raises(ValidationError):
        Verdict(
            status="pass",
            category="wrong_answer",
            summary="bad",
            cases_passed=3,
            cases_total=3,
        )


def test_verdict_pass_rejects_mismatched_counts() -> None:
    with pytest.raises(ValidationError):
        Verdict(status="pass", summary="bad", cases_passed=2, cases_total=3)


def test_verdict_fail_requires_category() -> None:
    with pytest.raises(ValidationError):
        Verdict(status="fail", summary="bad", cases_passed=1, cases_total=3)


def test_verdict_fail_with_category_ok() -> None:
    verdict = Verdict(
        status="fail",
        category="wrong_answer",
        first_failing_case="case_2",
        expected=3,
        actual=4,
        summary="case_2 failed",
        cases_passed=1,
        cases_total=2,
    )
    assert verdict.category == "wrong_answer"
    assert verdict.first_failing_case == "case_2"


def test_verdict_inconclusive_and_skipped_do_not_require_category() -> None:
    Verdict(status="inconclusive", summary="sandbox unavailable")
    Verdict(status="skipped", summary="no tests provided")
