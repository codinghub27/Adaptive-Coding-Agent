"""Unit tests for `app.execution.verification.verify`.

Pure/table-driven -- no Docker, no LLM, no I/O. `verify` derives a `Verdict`
from an `ExecutionResult` plus an optional `ExecutionRequest`; every branch of
the mapping in `docs/phases/PHASE-06-sandbox-verification.md` (packet P5, plus
the P6 code-review fixes F2/F3/F4/F8/F10 making the HOST authoritative over
pass/fail) is covered here, plus an AST guard asserting the module never
imports an LLM/network client, and a parity test asserting the host's
`canonical()` agrees byte-for-byte with the harness's own copy.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import JsonValue

from app.execution.base import canonical
from app.execution.verification import verify
from app.schemas.execution import (
    CaseResult,
    ExecutionRequest,
    ExecutionResult,
    HarnessError,
    TestCase,
    TestSuite,
)

VERIFICATION_FILE = Path(__file__).resolve().parents[2] / "app" / "execution" / "verification.py"
HARNESS_FILE = Path(__file__).resolve().parents[2] / "docker" / "harness" / "run.py"

FORBIDDEN_IMPORT_ROOTS = {"app.llm", "langchain", "openai", "httpx", "requests"}


# --------------------------------------------------------------------------
# Import-graph guard: verification is deterministic, no LLM/network
# --------------------------------------------------------------------------


def test_verification_module_imports_no_llm_or_network_client() -> None:
    assert VERIFICATION_FILE.is_file(), f"expected {VERIFICATION_FILE} to exist"
    tree = ast.parse(VERIFICATION_FILE.read_text(encoding="utf-8"), filename=str(VERIFICATION_FILE))

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if any(
                    alias.name == root or alias.name.startswith(root + ".")
                    for root in FORBIDDEN_IMPORT_ROOTS
                ):
                    violations.append(f"line {node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if any(
                module == root or module.startswith(root + ".") for root in FORBIDDEN_IMPORT_ROOTS
            ):
                violations.append(f"line {node.lineno}: from {module} import ...")

    assert not violations, "forbidden imports in verification.py:\n" + "\n".join(violations)


# --------------------------------------------------------------------------
# Harness/host `canonical()` parity (F2/F3/F4): loaded from its file path via
# importlib so we exercise the harness's own pure `_canonical`/
# `_canonicalize_numbers` without ever executing user code.
# --------------------------------------------------------------------------


def _load_harness_module() -> Any:
    spec = importlib.util.spec_from_file_location("_aca_harness_run", HARNESS_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_HARNESS: Any = _load_harness_module()


@pytest.mark.parametrize(
    "value",
    [
        1,
        2.0,
        True,
        False,
        None,
        "hello",
        "unicode: café ☃",
        [1, 2, 3],
        [2.0, 3.0, {"a": 1.0}],
        {"b": 1, "a": 2},
        {"nested": {"z": 1, "a": [1, 2.0, None, True]}},
        [],
        {},
        [[1, 2], [3, 4]],
    ],
)
def test_harness_and_host_canonical_agree(value: object) -> None:
    host_text = canonical(value)  # type: ignore[arg-type]
    harness_text = _HARNESS._canonical(value)
    assert host_text == harness_text


def test_harness_and_host_canonical_agree_on_int_vs_float() -> None:
    assert canonical(2) == canonical(2.0) == _HARNESS._canonical(2) == _HARNESS._canonical(2.0)


def test_harness_and_host_canonical_distinguish_bool_from_int() -> None:
    assert canonical(True) != canonical(1)
    assert _HARNESS._canonical(True) != _HARNESS._canonical(1)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _hash(value: JsonValue) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def make_result(**overrides: object) -> ExecutionResult:
    params: dict[str, object] = {"status": "completed"}
    params.update(overrides)
    return ExecutionResult(**params)  # type: ignore[arg-type]


def make_request(**overrides: object) -> ExecutionRequest:
    params: dict[str, object] = {"language": "python", "code": "pass"}
    params.update(overrides)
    return ExecutionRequest(**params)  # type: ignore[arg-type]


def make_case(**overrides: object) -> CaseResult:
    """A harness case report. `actual_sha256` is derived from `actual` (as
    the real harness would compute it) unless the test overrides it directly
    (e.g. to simulate a forged/mismatched hash)."""
    params: dict[str, object] = {"name": "c1", "passed": True, "duration_ms": 1.0}
    params.update(overrides)
    if params.get("error") is None and "actual_sha256" not in overrides:
        params["actual_sha256"] = _hash(params.get("actual"))  # type: ignore[arg-type]
    return CaseResult(**params)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# No result
# --------------------------------------------------------------------------


def test_verify_none_result_is_skipped() -> None:
    verdict = verify(None)
    assert verdict.status == "skipped"
    assert verdict.category is None
    assert verdict.summary == "no code was executed"


# --------------------------------------------------------------------------
# rejected / sandbox_error / timeout / memory_exceeded
# --------------------------------------------------------------------------


def test_verify_rejected_is_inconclusive() -> None:
    result = make_result(
        status="rejected", error=HarnessError(type="PayloadTooLarge", message="too big")
    )
    verdict = verify(result)
    assert verdict.status == "inconclusive"
    assert verdict.category == "rejected"
    assert verdict.error_type == "PayloadTooLarge"


def test_verify_rejected_without_error_is_inconclusive() -> None:
    result = make_result(status="rejected")
    verdict = verify(result)
    assert verdict.status == "inconclusive"
    assert verdict.category == "rejected"
    assert verdict.error_type is None


def test_verify_sandbox_error_report_tampered_is_fail() -> None:
    result = make_result(
        status="sandbox_error",
        error=HarnessError(type="ReportTampered", message="tampered"),
    )
    verdict = verify(result)
    assert verdict.status == "fail"
    assert verdict.category == "sandbox_error"
    assert verdict.diagnostics == ["report_tampered"]


def test_verify_sandbox_unavailable_is_inconclusive() -> None:
    result = make_result(
        status="sandbox_error",
        error=HarnessError(type="SandboxUnavailable", message="docker down"),
    )
    verdict = verify(result)
    assert verdict.status == "inconclusive"
    assert verdict.category == "sandbox_error"
    assert verdict.error_type == "SandboxUnavailable"


def test_verify_sandbox_error_without_error_is_inconclusive() -> None:
    result = make_result(status="sandbox_error")
    verdict = verify(result)
    assert verdict.status == "inconclusive"
    assert verdict.category == "sandbox_error"


def test_verify_timeout_is_fail() -> None:
    verdict = verify(make_result(status="timeout", timed_out=True))
    assert verdict.status == "fail"
    assert verdict.category == "timeout"
    assert verdict.diagnostics == ["possible_infinite_loop_or_too_slow"]
    assert verdict.summary == "timed out after the sandbox limit"


def test_verify_memory_exceeded_is_fail() -> None:
    verdict = verify(make_result(status="memory_exceeded", oom_killed=True))
    assert verdict.status == "fail"
    assert verdict.category == "memory_limit"
    assert verdict.diagnostics == ["excessive_memory"]


# --------------------------------------------------------------------------
# compile_error / entrypoint_missing / runtime_error (process-level)
# --------------------------------------------------------------------------


def test_verify_compile_error_with_lineno() -> None:
    result = make_result(
        status="compile_error",
        error=HarnessError(type="SyntaxError", message="bad", lineno=7),
    )
    verdict = verify(result)
    assert verdict.status == "fail"
    assert verdict.category == "syntax_error"
    assert verdict.error_type == "SyntaxError"
    assert "line:7" in verdict.diagnostics


def test_verify_compile_error_without_lineno() -> None:
    result = make_result(
        status="compile_error", error=HarnessError(type="SyntaxError", message="bad")
    )
    verdict = verify(result)
    assert verdict.status == "fail"
    assert verdict.diagnostics == []


def test_verify_entrypoint_missing_is_fail() -> None:
    verdict = verify(make_result(status="entrypoint_missing"))
    assert verdict.status == "fail"
    assert verdict.category == "missing_entrypoint"


def test_verify_runtime_error_with_lineno() -> None:
    result = make_result(
        status="runtime_error",
        error=HarnessError(type="ZeroDivisionError", message="boom", lineno=3),
    )
    verdict = verify(result)
    assert verdict.status == "fail"
    assert verdict.category == "runtime_error"
    assert verdict.error_type == "ZeroDivisionError"
    assert verdict.diagnostics == ["exception:ZeroDivisionError", "line:3"]
    assert verdict.summary == "runtime error ZeroDivisionError on line 3"


def test_verify_runtime_error_no_report() -> None:
    result = make_result(status="runtime_error", error=HarnessError(type="NoReport", message="x"))
    verdict = verify(result)
    assert verdict.status == "fail"
    assert verdict.category == "runtime_error"
    assert verdict.diagnostics == ["exception:NoReport"]


# --------------------------------------------------------------------------
# completed (script mode, no tests)
# --------------------------------------------------------------------------


def test_verify_completed_is_inconclusive_no_tests() -> None:
    verdict = verify(make_result(status="completed"))
    assert verdict.status == "inconclusive"
    assert verdict.category == "no_tests"


# --------------------------------------------------------------------------
# Without a request, tests-phase results can never be verified (F2/F3/F4):
# the (untrusted) harness report alone is never enough to call it a pass.
# --------------------------------------------------------------------------


def test_verify_tests_phase_without_request_is_inconclusive_not_pass() -> None:
    result = make_result(
        status="passed",
        phase="tests",
        cases=[make_case(name="c1", passed=True, actual=1)],
    )
    verdict = verify(result, request=None)
    assert verdict.status == "inconclusive"
    assert verdict.diagnostics == ["unverifiable_without_request"]


# --------------------------------------------------------------------------
# passed/failed derived HOST-side from cases + the original request, never
# from the harness's self-reported `passed` flags or `status` string.
# --------------------------------------------------------------------------


def test_verify_passed_all_cases_pass() -> None:
    request = make_request(
        tests=TestSuite(
            entrypoint="f",
            cases=[TestCase(name="c1", expected=3), TestCase(name="c2", expected=4)],
        )
    )
    result = make_result(
        status="passed",
        phase="tests",
        cases=[
            make_case(name="c1", passed=True, actual=3, actual_repr="3"),
            make_case(name="c2", passed=True, actual=4, actual_repr="4"),
        ],
    )
    verdict = verify(result, request)
    assert verdict.status == "pass"
    assert verdict.category is None
    assert verdict.cases_passed == 2
    assert verdict.cases_total == 2


def test_verify_status_passed_but_a_case_failed_is_still_fail() -> None:
    """The status string (and the harness's own `passed` flag) both lie; only
    the host-computed hash-vs-expected comparison decides pass/fail."""
    request = make_request(
        tests=TestSuite(
            entrypoint="f",
            cases=[TestCase(name="c1", expected=1), TestCase(name="c2", expected=99)],
        )
    )
    result = make_result(
        status="passed",
        phase="tests",
        cases=[
            make_case(name="c1", passed=True, actual=1, actual_repr="1"),
            # Harness claims this passed, but its actual (2) doesn't hash-match
            # the requested expected (99) -- the host must not trust `passed`.
            make_case(name="c2", passed=True, actual=2, actual_repr="2"),
        ],
    )
    verdict = verify(result, request)
    assert verdict.status == "fail"
    assert verdict.category == "wrong_answer"
    assert verdict.first_failing_case == "c2"


def test_verify_actual_sha256_not_matching_expected_hash_is_wrong_answer() -> None:
    """The host never compares `actual` to `expected` directly -- only
    `actual_sha256` (the harness's hash of its own canonicalized `actual`)
    against the host's own hash of `expected`. A mismatch is a wrong answer
    even though the harness's `passed` flag claims success."""
    request = make_request(tests=TestSuite(entrypoint="f", cases=[TestCase(name="c1", expected=1)]))
    result = make_result(
        status="passed",
        phase="tests",
        cases=[
            CaseResult(
                name="c1",
                passed=True,
                actual=2,
                actual_repr="2",
                actual_sha256=_hash(2),
                duration_ms=1.0,
            )
        ],
    )
    verdict = verify(result, request)
    assert verdict.status == "fail"
    assert verdict.category == "wrong_answer"


def test_verify_status_passed_with_zero_cases_is_fail_no_tests() -> None:
    request = make_request(tests=TestSuite(entrypoint="f", cases=[TestCase(name="c1", expected=1)]))
    result = make_result(status="passed", phase="tests", cases=[])
    verdict = verify(result, request)
    assert verdict.status == "fail"
    assert verdict.category == "sandbox_error"
    assert verdict.diagnostics == ["case_set_mismatch"]


def test_verify_failed_case_with_error_is_runtime_error() -> None:
    request = make_request(
        tests=TestSuite(
            entrypoint="f",
            cases=[TestCase(name="c1", expected=1), TestCase(name="c2", expected=2)],
        )
    )
    result = make_result(
        status="failed",
        phase="tests",
        cases=[
            make_case(name="c1", passed=True, actual=1, actual_repr="1"),
            make_case(
                name="c2",
                passed=False,
                error=HarnessError(type="ZeroDivisionError", message="boom", lineno=5),
            ),
        ],
    )
    verdict = verify(result, request)
    assert verdict.status == "fail"
    assert verdict.category == "runtime_error"
    assert verdict.first_failing_case == "c2"
    assert verdict.error_type == "ZeroDivisionError"
    assert verdict.diagnostics == ["exception:ZeroDivisionError", "line:5"]
    assert verdict.cases_passed == 1
    assert verdict.cases_total == 2


def test_verify_failed_wrong_answer_two_sum_off_by_one() -> None:
    """Hand-built Test-3 scenario: 4 cases, case 1 passes, case 2 is the
    first failure with an off-by-one actual vs expected."""
    request = make_request(
        code="def two_sum(nums, target): ...",
        tests=TestSuite(
            entrypoint="two_sum",
            cases=[
                TestCase(name="c1", args=[[2, 7, 11, 15], 9], expected=[0, 1]),
                TestCase(name="c2", args=[[3, 2, 4], 6], expected=[0, 1]),
                TestCase(name="c3", args=[[3, 3], 6], expected=[0, 1]),
                TestCase(name="c4", args=[[1, 2, 3], 5], expected=[1, 2]),
            ],
        ),
    )
    result = make_result(
        status="failed",
        phase="tests",
        cases=[
            make_case(name="c1", passed=True, actual=[0, 1], actual_repr="[0, 1]"),
            make_case(name="c2", passed=False, actual=[0, 2], actual_repr="[0, 2]"),
            make_case(name="c3", passed=False, actual=[1, 2], actual_repr="[1, 2]"),
            make_case(name="c4", passed=False, actual=[0, 1], actual_repr="[0, 1]"),
        ],
    )

    verdict = verify(result, request)

    assert verdict.status == "fail"
    assert verdict.category == "wrong_answer"
    assert verdict.first_failing_case == "c2"
    assert verdict.expected == [0, 1]
    assert verdict.actual == [0, 2]
    assert "off_by_one_suspected" in verdict.diagnostics
    assert "c2" in verdict.summary
    assert verdict.cases_passed == 1
    assert verdict.cases_total == 4


def test_verify_wrong_answer_actual_truncated_adds_diagnostic() -> None:
    request = make_request(tests=TestSuite(entrypoint="f", cases=[TestCase(name="c1", expected=1)]))
    result = make_result(
        status="failed",
        phase="tests",
        cases=[
            CaseResult(
                name="c1",
                passed=False,
                actual=None,
                actual_repr="<huge>",
                actual_sha256=_hash(2),
                actual_truncated=True,
                duration_ms=1.0,
            )
        ],
    )
    verdict = verify(result, request)
    assert verdict.status == "fail"
    assert verdict.category == "wrong_answer"
    assert "actual_truncated" in verdict.diagnostics
    assert verdict.actual == "<huge>"


# --------------------------------------------------------------------------
# Case-set mismatch: the report doesn't match what was asked for
# --------------------------------------------------------------------------


def test_verify_case_count_mismatch_is_fail_even_if_reported_cases_all_pass() -> None:
    result = make_result(
        status="passed",
        phase="tests",
        cases=[make_case(name="c1", passed=True, actual=1)],
    )
    request = make_request(
        tests=TestSuite(
            entrypoint="f",
            cases=[
                TestCase(name="c1", args=[], expected=1),
                TestCase(name="c2", args=[], expected=2),
            ],
        )
    )
    verdict = verify(result, request)
    assert verdict.status == "fail"
    assert verdict.category == "sandbox_error"
    assert verdict.diagnostics == ["case_set_mismatch"]


def test_verify_case_names_out_of_order_is_mismatch() -> None:
    result = make_result(
        status="passed",
        phase="tests",
        cases=[
            make_case(name="c2", passed=True, actual=2),
            make_case(name="c1", passed=True, actual=1),
        ],
    )
    request = make_request(
        tests=TestSuite(
            entrypoint="f",
            cases=[TestCase(name="c1", expected=1), TestCase(name="c2", expected=2)],
        )
    )
    verdict = verify(result, request)
    assert verdict.status == "fail"
    assert verdict.category == "sandbox_error"
    assert verdict.diagnostics == ["case_set_mismatch"]


def test_verify_case_count_matches_request_is_unaffected() -> None:
    result = make_result(
        status="passed",
        phase="tests",
        cases=[make_case(name="c1", passed=True, actual=1)],
    )
    request = make_request(tests=TestSuite(entrypoint="f", cases=[TestCase(name="c1", expected=1)]))
    verdict = verify(result, request)
    assert verdict.status == "pass"


def test_verify_wrong_answer_uses_actual_repr_when_actual_none() -> None:
    request = make_request(tests=TestSuite(entrypoint="f", cases=[TestCase(name="c1", expected=1)]))
    result = make_result(
        status="failed",
        phase="tests",
        cases=[
            CaseResult(
                name="c1",
                passed=False,
                actual=None,
                actual_repr="<obj>",
                actual_sha256=_hash("not comparable"),
                duration_ms=1.0,
            )
        ],
    )
    verdict = verify(result, request)
    assert verdict.actual == "<obj>"


# --------------------------------------------------------------------------
# Wrong-answer diagnostics helpers
# --------------------------------------------------------------------------


def test_diagnostic_off_by_one_ints() -> None:
    request = make_request(tests=TestSuite(entrypoint="f", cases=[TestCase(name="c1", expected=5)]))
    result = make_result(
        status="failed",
        phase="tests",
        cases=[make_case(name="c1", passed=False, actual=6, actual_repr="6")],
    )
    verdict = verify(result, request)
    assert "off_by_one_suspected" in verdict.diagnostics


def test_diagnostic_off_by_one_excludes_bools() -> None:
    request = make_request(
        tests=TestSuite(entrypoint="f", cases=[TestCase(name="c1", expected=False)])
    )
    result = make_result(
        status="failed",
        phase="tests",
        cases=[make_case(name="c1", passed=False, actual=True, actual_repr="True")],
    )
    verdict = verify(result, request)
    assert "off_by_one_suspected" not in verdict.diagnostics


def test_diagnostic_empty_result() -> None:
    request = make_request(
        tests=TestSuite(entrypoint="f", cases=[TestCase(name="c1", expected=[1, 2])])
    )
    result = make_result(
        status="failed",
        phase="tests",
        cases=[make_case(name="c1", passed=False, actual=[], actual_repr="[]")],
    )
    verdict = verify(result, request)
    assert "empty_result" in verdict.diagnostics


def test_diagnostic_wrong_type() -> None:
    request = make_request(tests=TestSuite(entrypoint="f", cases=[TestCase(name="c1", expected=5)]))
    result = make_result(
        status="failed",
        phase="tests",
        cases=[make_case(name="c1", passed=False, actual="5", actual_repr="'5'")],
    )
    verdict = verify(result, request)
    assert "wrong_type" in verdict.diagnostics


def test_diagnostic_wrong_type_bool_vs_int() -> None:
    request = make_request(tests=TestSuite(entrypoint="f", cases=[TestCase(name="c1", expected=1)]))
    result = make_result(
        status="failed",
        phase="tests",
        cases=[make_case(name="c1", passed=False, actual=True, actual_repr="True")],
    )
    verdict = verify(result, request)
    assert "wrong_type" in verdict.diagnostics


def test_diagnostic_wrong_length() -> None:
    request = make_request(
        tests=TestSuite(entrypoint="f", cases=[TestCase(name="c1", expected=[1, 2, 3])])
    )
    result = make_result(
        status="failed",
        phase="tests",
        cases=[make_case(name="c1", passed=False, actual=[1, 2], actual_repr="[1, 2]")],
    )
    verdict = verify(result, request)
    assert "wrong_length" in verdict.diagnostics


def test_diagnostic_order_mismatch() -> None:
    request = make_request(
        tests=TestSuite(entrypoint="f", cases=[TestCase(name="c1", expected=[1, 2, 3])])
    )
    result = make_result(
        status="failed",
        phase="tests",
        cases=[make_case(name="c1", passed=False, actual=[2, 1, 3], actual_repr="[2, 1, 3]")],
    )
    verdict = verify(result, request)
    assert "order_mismatch" in verdict.diagnostics


def test_diagnostic_none_apply_for_close_but_unrelated_values() -> None:
    request = make_request(
        tests=TestSuite(entrypoint="f", cases=[TestCase(name="c1", expected="world")])
    )
    result = make_result(
        status="failed",
        phase="tests",
        cases=[make_case(name="c1", passed=False, actual="hello", actual_repr="hello")],
    )
    verdict = verify(result, request)
    assert verdict.diagnostics == []


# --------------------------------------------------------------------------
# Summary rendering caps
# --------------------------------------------------------------------------


def test_summary_caps_rendered_values() -> None:
    long_expected: list[JsonValue] = list(range(1000))
    request = make_request(
        tests=TestSuite(
            entrypoint="f", cases=[TestCase(name="c1", args=[], expected=long_expected)]
        )
    )
    result = make_result(
        status="failed",
        phase="tests",
        cases=[make_case(name="c1", passed=False, actual=[], actual_repr="[]")],
    )
    verdict = verify(result, request)
    assert len(verdict.summary) < 600
