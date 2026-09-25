"""Deterministic verification: `ExecutionResult` (+ optional `ExecutionRequest`)
-> `Verdict`.

NON-NEGOTIABLE: this module consumes execution FACTS only. It must never
import from `app.llm` or any LLM/network/HTTP client, and nothing in here can
turn a failing test case into a pass -- `verify` is a pure function of its
arguments (see `tests/execution/test_verification.py`'s import-graph guard
test, which asserts this module's AST contains no such imports).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Final

from pydantic import JsonValue

from app.execution.base import canonical, truncate_text
from app.schemas.execution import (
    CaseResult,
    ExecutionRequest,
    ExecutionResult,
    Verdict,
)

__all__ = ["verify"]

_MAX_RENDER_CHARS: Final = 200
_MAX_CASE_NAME_CHARS: Final = 128
_TRUNCATION_SUFFIX: Final = "…"


def _expected_sha256(expected: JsonValue) -> str:
    return hashlib.sha256(canonical(expected).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Rendering helpers
# --------------------------------------------------------------------------


def _render(value: JsonValue) -> str:
    """Render a JSON value deterministically, capped to `_MAX_RENDER_CHARS`."""
    text = json.dumps(value)
    return truncate_text(text, _MAX_RENDER_CHARS, suffix=_TRUNCATION_SUFFIX)[0]


def _truncate_name(name: str) -> str:
    return truncate_text(name, _MAX_CASE_NAME_CHARS, suffix=_TRUNCATION_SUFFIX)[0]


# --------------------------------------------------------------------------
# Wrong-answer diagnostics (deterministic, pure, JSON-value only)
# --------------------------------------------------------------------------


def _as_int_not_bool(value: JsonValue) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _off_by_one(expected: JsonValue, actual: JsonValue) -> bool:
    expected_int = _as_int_not_bool(expected)
    actual_int = _as_int_not_bool(actual)
    if expected_int is not None and actual_int is not None:
        return abs(expected_int - actual_int) == 1

    if isinstance(expected, list) and isinstance(actual, list) and len(expected) == len(actual):
        if not expected:
            return False
        expected_ints = [_as_int_not_bool(v) for v in expected]
        actual_ints = [_as_int_not_bool(v) for v in actual]
        if any(v is None for v in expected_ints) or any(v is None for v in actual_ints):
            return False
        diffs = [
            abs(e - a)
            for e, a in zip(expected_ints, actual_ints, strict=True)
            if e is not None and a is not None
        ]
        return all(d <= 1 for d in diffs) and any(d != 0 for d in diffs)

    return False


_EMPTY_VALUES: Final[tuple[JsonValue, ...]] = (None, [], {}, "")


def _is_empty_value(value: JsonValue) -> bool:
    return any(type(value) is type(v) and value == v for v in _EMPTY_VALUES)


def _empty_result(expected: JsonValue, actual: JsonValue) -> bool:
    return _is_empty_value(actual) and not _is_empty_value(expected)


def _json_type(value: JsonValue) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _wrong_type(expected: JsonValue, actual: JsonValue) -> bool:
    return _json_type(expected) != _json_type(actual)


def _wrong_length(expected: JsonValue, actual: JsonValue) -> bool:
    return isinstance(expected, list) and isinstance(actual, list) and len(expected) != len(actual)


def _sort_key(value: JsonValue) -> str:
    return json.dumps(value, sort_keys=True)


def _order_mismatch(expected: JsonValue, actual: JsonValue) -> bool:
    if not (isinstance(expected, list) and isinstance(actual, list)):
        return False
    if len(expected) != len(actual):
        return False
    if expected == actual:
        return False
    return sorted(expected, key=_sort_key) == sorted(actual, key=_sort_key)


_WRONG_ANSWER_CHECKS: Final[list[tuple[str, Callable[[JsonValue, JsonValue], bool]]]] = [
    ("off_by_one_suspected", _off_by_one),
    ("empty_result", _empty_result),
    ("wrong_type", _wrong_type),
    ("wrong_length", _wrong_length),
    ("order_mismatch", _order_mismatch),
]


def _wrong_answer_diagnostics(expected: JsonValue, actual: JsonValue) -> list[str]:
    return [name for name, check in _WRONG_ANSWER_CHECKS if check(expected, actual)]


# --------------------------------------------------------------------------
# Summaries (deterministic, no stdout/stderr/user text beyond rendered values)
# --------------------------------------------------------------------------


def _rejected_summary(result: ExecutionResult) -> str:
    if result.error is not None:
        return f"execution was rejected: {result.error.type}"
    return "execution was rejected before running"


def _sandbox_error_summary(result: ExecutionResult) -> str:
    if result.error is not None:
        return f"sandbox infrastructure error: {result.error.type}"
    return "sandbox infrastructure error"


def _compile_error_summary(result: ExecutionResult) -> str:
    error_type = result.error.type if result.error else "syntax error"
    lineno = result.error.lineno if result.error else None
    if lineno is not None:
        return f"syntax error ({error_type}) on line {lineno}"
    return f"syntax error ({error_type})"


def _runtime_error_summary(result: ExecutionResult) -> str:
    error_type = result.error.type if result.error else "unknown error"
    lineno = result.error.lineno if result.error else None
    if lineno is not None:
        return f"runtime error {error_type} on line {lineno}"
    return f"runtime error {error_type}"


def _case_runtime_error_summary(case: CaseResult, cases_passed: int, cases_total: int) -> str:
    name = _truncate_name(case.name)
    error_type = case.error.type if case.error else "unknown error"
    return f"{cases_passed}/{cases_total} cases passed; case '{name}' raised {error_type}"


def _wrong_answer_summary(
    case: CaseResult, expected: JsonValue, actual: JsonValue, cases_passed: int, cases_total: int
) -> str:
    name = _truncate_name(case.name)
    return (
        f"{cases_passed}/{cases_total} cases passed; first failing case '{name}': "
        f"expected {_render(expected)}, got {_render(actual)}"
    )


# --------------------------------------------------------------------------
# Test-phase (passed/failed) verdict, derived from cases -- never the status
# --------------------------------------------------------------------------


def _actual_for_case(case: CaseResult) -> JsonValue:
    if case.actual is not None:
        return case.actual
    return case.actual_repr or None


_CASE_SET_MISMATCH_SUMMARY: Final = (
    "the sandbox report's case names did not match the requested test cases"
)
_UNVERIFIABLE_WITHOUT_REQUEST_SUMMARY: Final = (
    "the submission ran but there is no original request to verify its results against"
)


def _host_case_passed(case: CaseResult, expected_sha256: str) -> bool:
    """Whether `case` is a genuine pass, as judged by the HOST, not the
    harness: no error, AND the sha256 of the harness's canonicalized `actual`
    matches the host's own hash of the canonicalized expected value. The
    harness's self-reported `passed` flag is never trusted for this -- a
    hostile submission that forges its own comparison (subclassing a builtin
    with a lying `__eq__`, or monkeypatching the harness's equality helper)
    cannot pass unless its ACTUAL serialized value truly equals `expected`."""
    return (
        case.error is None
        and case.actual_sha256 is not None
        and case.actual_sha256 == expected_sha256
    )


def _verify_tests(result: ExecutionResult, request: ExecutionRequest | None) -> Verdict:
    cases_total = result.cases_total
    cases_passed_reported = result.cases_passed

    if request is None or request.tests is None:
        # Nothing to check the harness's claims against -- never call this a
        # pass no matter what the (untrusted) report says.
        return Verdict(
            status="inconclusive",
            diagnostics=["unverifiable_without_request"],
            cases_passed=cases_passed_reported,
            cases_total=cases_total,
            summary=_UNVERIFIABLE_WITHOUT_REQUEST_SUMMARY,
        )

    requested_names = [case.name for case in request.tests.cases]
    reported_names = [case.name for case in result.cases]
    if reported_names != requested_names:
        # The report doesn't match what we asked for (wrong count, wrong
        # names, wrong order, or duplicates) -- never trust it enough to call
        # it a pass, no matter how many cases it claims passed.
        return Verdict(
            status="fail",
            category="sandbox_error",
            diagnostics=["case_set_mismatch"],
            cases_passed=cases_passed_reported,
            cases_total=cases_total,
            summary=_CASE_SET_MISMATCH_SUMMARY,
        )

    expected_by_name = {case.name: case.expected for case in request.tests.cases}
    expected_hash_by_name = {
        name: _expected_sha256(value) for name, value in expected_by_name.items()
    }
    host_verdicts = [
        (case, _host_case_passed(case, expected_hash_by_name[case.name])) for case in result.cases
    ]
    cases_passed = sum(1 for _, passed in host_verdicts if passed)

    if cases_total > 0 and cases_passed == cases_total:
        return Verdict(
            status="pass",
            cases_passed=cases_passed,
            cases_total=cases_total,
            summary=f"all {cases_total} case(s) passed",
        )

    first_pair = next((pair for pair in host_verdicts if not pair[1]), None)
    if first_pair is None:
        return Verdict(
            status="fail",
            category="no_tests",
            cases_passed=cases_passed,
            cases_total=cases_total,
            summary="no test cases were executed",
        )
    first = first_pair[0]

    if first.error is not None:
        error_type = first.error.type
        diagnostics = [f"exception:{error_type}"]
        if first.error.lineno is not None:
            diagnostics.append(f"line:{first.error.lineno}")
        return Verdict(
            status="fail",
            category="runtime_error",
            first_failing_case=_truncate_name(first.name),
            error_type=error_type,
            diagnostics=diagnostics,
            cases_passed=cases_passed,
            cases_total=cases_total,
            summary=_case_runtime_error_summary(first, cases_passed, cases_total),
        )

    expected = expected_by_name[first.name]
    actual = _actual_for_case(first)
    diagnostics = _wrong_answer_diagnostics(expected, actual) if first.actual is not None else []
    if first.actual_truncated:
        diagnostics = [*diagnostics, "actual_truncated"]
    return Verdict(
        status="fail",
        category="wrong_answer",
        first_failing_case=_truncate_name(first.name),
        expected=expected,
        actual=actual,
        diagnostics=diagnostics,
        cases_passed=cases_passed,
        cases_total=cases_total,
        summary=_wrong_answer_summary(first, expected, actual, cases_passed, cases_total),
    )


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def verify(result: ExecutionResult | None, request: ExecutionRequest | None = None) -> Verdict:
    """Derive a deterministic `Verdict` from an `ExecutionResult`.

    Pure -- no LLM, no network, no I/O. For the tests phase (`passed`/
    `failed`), the verdict is derived from `result.cases`, not from the
    status string, so nothing (including a mislabeled status) can turn a
    failing case into a pass.
    """
    if result is None:
        return Verdict(status="skipped", summary="no code was executed")

    if result.status == "rejected":
        error_type = result.error.type if result.error else None
        return Verdict(
            status="inconclusive",
            category="rejected",
            error_type=error_type,
            summary=_rejected_summary(result),
        )

    if result.status == "sandbox_error":
        if result.error is not None and result.error.type == "ReportTampered":
            return Verdict(
                status="fail",
                category="sandbox_error",
                error_type=result.error.type,
                diagnostics=["report_tampered"],
                summary="the sandbox report could not be trusted (tampered)",
            )
        error_type = result.error.type if result.error else None
        return Verdict(
            status="inconclusive",
            category="sandbox_error",
            error_type=error_type,
            summary=_sandbox_error_summary(result),
        )

    if result.status == "timeout":
        return Verdict(
            status="fail",
            category="timeout",
            diagnostics=["possible_infinite_loop_or_too_slow"],
            summary="timed out after the sandbox limit",
        )

    if result.status == "memory_exceeded":
        return Verdict(
            status="fail",
            category="memory_limit",
            diagnostics=["excessive_memory"],
            summary="exceeded the sandbox memory limit",
        )

    if result.status == "compile_error":
        error_type = result.error.type if result.error else None
        diagnostics: list[str] = []
        lineno = result.error.lineno if result.error else None
        if lineno is not None:
            diagnostics.append(f"line:{lineno}")
        return Verdict(
            status="fail",
            category="syntax_error",
            error_type=error_type,
            diagnostics=diagnostics,
            summary=_compile_error_summary(result),
        )

    if result.status == "entrypoint_missing":
        return Verdict(
            status="fail",
            category="missing_entrypoint",
            summary="the requested entrypoint was not found or not callable",
        )

    if result.status == "runtime_error":
        error_type = result.error.type if result.error else None
        diagnostics = []
        if error_type is not None:
            diagnostics.append(f"exception:{error_type}")
        lineno = result.error.lineno if result.error else None
        if lineno is not None:
            diagnostics.append(f"line:{lineno}")
        return Verdict(
            status="fail",
            category="runtime_error",
            error_type=error_type,
            diagnostics=diagnostics,
            summary=_runtime_error_summary(result),
        )

    if result.status == "completed":
        return Verdict(
            status="inconclusive",
            category="no_tests",
            summary="the submission ran cleanly with no tests to verify correctness against",
        )

    # result.status in ("passed", "failed") -- derive from cases, never trust
    # the status string itself.
    return _verify_tests(result, request)
