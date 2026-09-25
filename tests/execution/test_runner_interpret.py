"""Table tests for `app.execution.runner.interpret` -- pure, no I/O.

Covers every branch of the raw-process-outcome -> `ExecutionResult` mapping:
timeout/OOM short-circuits, marker-framing edge cases (no report, well-framed,
forged/extra marker, trailing garbage, malformed JSON), the harness-phase ->
status mapping, tests all-pass/one-fail/zero-cases, output truncation, and
that raw stdout preceding the marker is preserved.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

import pytest

from app.execution.base import RawRun
from app.execution.runner import MAX_RESULT_OUTPUT_CHARS, interpret
from app.schemas.execution import HarnessReport

MARKER = "deadbeef" * 4  # 32 hex chars, like secrets.token_hex(16)


def make_raw(
    *,
    exit_code: int | None = 0,
    stdout: bytes = b"",
    stderr: bytes = b"",
    duration_ms: float = 12.5,
    timed_out: bool = False,
    oom_killed: bool = False,
    output_truncated: bool = False,
) -> RawRun:
    return RawRun(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_ms=duration_ms,
        timed_out=timed_out,
        oom_killed=oom_killed,
        output_truncated=output_truncated,
    )


def report_line(report: Mapping[str, object], *, marker: str = MARKER) -> bytes:
    return f"{marker}{json.dumps(report)}{marker}\n".encode()


def base_report(
    *,
    phase: str,
    error: Mapping[str, object] | None = None,
    cases: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "version": 1,
        "phase": phase,
        "error": error,
        "stdout": "",
        "stderr": "",
        "cases": cases or [],
    }


# --------------------------------------------------------------------------
# timeout / OOM short-circuits
# --------------------------------------------------------------------------


def test_timed_out_short_circuits_to_timeout_status() -> None:
    raw = make_raw(timed_out=True, exit_code=None, stdout=b"partial", stderr=b"")
    result = interpret(raw, MARKER)

    assert result.status == "timeout"
    assert result.timed_out is True
    assert result.stdout == "partial"


def test_oom_killed_short_circuits_to_memory_exceeded_status() -> None:
    raw = make_raw(oom_killed=True, exit_code=137)
    result = interpret(raw, MARKER)

    assert result.status == "memory_exceeded"
    assert result.oom_killed is True


def test_timeout_takes_priority_over_oom() -> None:
    raw = make_raw(timed_out=True, oom_killed=True)
    result = interpret(raw, MARKER)

    assert result.status == "timeout"


# --------------------------------------------------------------------------
# No report
# --------------------------------------------------------------------------


def test_no_report_exit_1() -> None:
    raw = make_raw(exit_code=1, stdout=b"", stderr=b"Segmentation fault")
    result = interpret(raw, MARKER)

    assert result.status == "runtime_error"
    assert result.error is not None
    assert result.error.type == "NoReport"


def test_no_report_exit_0() -> None:
    # e.g. os._exit(0) called before the report line was written.
    raw = make_raw(exit_code=0, stdout=b"some partial output, no marker here")
    result = interpret(raw, MARKER)

    assert result.status == "runtime_error"
    assert result.error is not None
    assert result.error.type == "NoReport"
    assert "some partial output" in result.stdout


# --------------------------------------------------------------------------
# Well-framed valid reports, per phase
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("phase", "expected_status"),
    [
        ("protocol", "sandbox_error"),
        ("compile", "compile_error"),
        ("load", "runtime_error"),
        ("entrypoint", "entrypoint_missing"),
        ("script", "completed"),
    ],
)
def test_phase_maps_to_status(phase: str, expected_status: str) -> None:
    error = None if phase == "script" else {"type": "SomeError", "message": "boom", "lineno": None}
    report = base_report(phase=phase, error=error)
    raw = make_raw(stdout=report_line(report))

    result = interpret(raw, MARKER)

    assert result.status == expected_status
    assert result.phase == phase


def test_tests_phase_all_pass() -> None:
    cases = [
        {
            "name": "case1",
            "passed": True,
            "actual": 3,
            "actual_repr": "3",
            "error": None,
            "stdout": "",
            "duration_ms": 0.1,
        },
        {
            "name": "case2",
            "passed": True,
            "actual": 4,
            "actual_repr": "4",
            "error": None,
            "stdout": "",
            "duration_ms": 0.1,
        },
    ]
    report = base_report(phase="tests", cases=cases)
    raw = make_raw(stdout=report_line(report))

    result = interpret(raw, MARKER)

    assert result.status == "passed"
    assert result.cases_total == 2
    assert result.cases_passed == 2


def test_tests_phase_one_fail() -> None:
    cases = [
        {
            "name": "case1",
            "passed": True,
            "actual": 3,
            "actual_repr": "3",
            "error": None,
            "stdout": "",
            "duration_ms": 0.1,
        },
        {
            "name": "case2",
            "passed": False,
            "actual": 5,
            "actual_repr": "5",
            "error": None,
            "stdout": "",
            "duration_ms": 0.1,
        },
    ]
    report = base_report(phase="tests", cases=cases)
    raw = make_raw(stdout=report_line(report))

    result = interpret(raw, MARKER)

    assert result.status == "failed"
    assert result.first_failure is not None
    assert result.first_failure.name == "case2"


def test_tests_phase_zero_cases() -> None:
    report = base_report(phase="tests", cases=[])
    raw = make_raw(stdout=report_line(report))

    result = interpret(raw, MARKER)

    assert result.status == "failed"
    assert result.cases_total == 0


# --------------------------------------------------------------------------
# Tampered / malformed reports
# --------------------------------------------------------------------------


def test_forged_extra_marker_before_real_report_is_tampered() -> None:
    report = base_report(phase="script")
    # Three occurrences of the marker: a forged one, then the real framing pair.
    stdout = f"{MARKER}fake{MARKER}".encode() + report_line(report)
    raw = make_raw(stdout=stdout)

    result = interpret(raw, MARKER)

    assert result.status == "sandbox_error"
    assert result.error is not None
    assert result.error.type == "ReportTampered"


def test_single_marker_occurrence_is_tampered() -> None:
    raw = make_raw(stdout=f"{MARKER}not a real report".encode())

    result = interpret(raw, MARKER)

    assert result.status == "sandbox_error"
    assert result.error is not None
    assert result.error.type == "ReportTampered"


def test_trailing_text_after_report_is_tampered() -> None:
    report = base_report(phase="script")
    stdout = report_line(report)[:-1] + b" extra garbage\n"
    raw = make_raw(stdout=stdout)

    result = interpret(raw, MARKER)

    assert result.status == "sandbox_error"
    assert result.error is not None
    assert result.error.type == "ReportTampered"


def test_trailing_whitespace_after_report_is_allowed() -> None:
    report = base_report(phase="script")
    stdout = report_line(report) + b"   \n\n"
    raw = make_raw(stdout=stdout)

    result = interpret(raw, MARKER)

    assert result.status == "completed"


def test_malformed_json_between_markers_is_malformed_report() -> None:
    raw = make_raw(stdout=f"{MARKER}{{not valid json{MARKER}\n".encode())

    result = interpret(raw, MARKER)

    assert result.status == "sandbox_error"
    assert result.error is not None
    assert result.error.type == "MalformedReport"


def test_json_missing_required_field_is_malformed_report() -> None:
    incomplete = {"version": 1, "phase": "script"}  # missing error/stdout/stderr/cases
    raw = make_raw(stdout=report_line(incomplete))

    result = interpret(raw, MARKER)

    assert result.status == "sandbox_error"
    assert result.error is not None
    assert result.error.type == "MalformedReport"


# --------------------------------------------------------------------------
# Output truncation + pre-marker raw stdout
# --------------------------------------------------------------------------


def test_pre_marker_raw_stdout_is_included() -> None:
    report = base_report(phase="script")
    stdout = b"print output before harness wrote its report\n" + report_line(report)
    raw = make_raw(stdout=stdout)

    result = interpret(raw, MARKER)

    assert "print output before harness wrote its report" in result.stdout


def test_output_truncation_applied_and_flagged() -> None:
    report = {**base_report(phase="script"), "stdout": "x" * (MAX_RESULT_OUTPUT_CHARS + 500)}
    raw = make_raw(stdout=report_line(report))

    result = interpret(raw, MARKER)

    assert len(result.stdout) <= MAX_RESULT_OUTPUT_CHARS
    assert result.stdout.endswith("…[truncated]")
    assert result.output_truncated is True


def test_raw_output_truncated_flag_propagates_even_without_report_truncation() -> None:
    report = base_report(phase="script")
    raw = make_raw(stdout=report_line(report), output_truncated=True)

    result = interpret(raw, MARKER)

    assert result.output_truncated is True


def test_report_validates_via_harness_report_model() -> None:
    """Sanity: the harness report shape used above round-trips through the
    real `HarnessReport` model (guards against the test fixtures drifting
    out of sync with the schema)."""
    report = base_report(phase="script")
    HarnessReport.model_validate(report)
