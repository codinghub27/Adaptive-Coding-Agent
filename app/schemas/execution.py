"""Sandbox code-execution request/result schemas.

`ExecutionRequest` / `TestSuite` are the API-facing shape submitted for
execution; `HarnessReport` mirrors the JSON report line emitted by the
in-container harness (`docker/harness/run.py`) byte-for-byte, so it must
parse that wire format exactly (see the harness module docstring for the
protocol). `ExecutionResult` is the host-side result after running the
sandbox (harness report plus process-level outcome such as timeout/OOM).
`Verdict` is the higher-level pass/fail judgement consumed by the P5
verifier and the graph, derived from an `ExecutionResult` plus the original
`TestSuite`/expected behaviour.
"""

from typing import ClassVar, Final, Literal

from pydantic import Field, JsonValue, field_validator, model_validator

from app.schemas.base import APIModel

__all__ = [
    "DEFAULT_TIMEOUT_S",
    "MAX_CODE_CHARS",
    "MAX_TEST_CASES",
    "MAX_TIMEOUT_S",
    "MIN_TIMEOUT_S",
    "CaseResult",
    "ExecutionRequest",
    "ExecutionResult",
    "ExecutionStatus",
    "FailureCategory",
    "HarnessError",
    "HarnessPhase",
    "HarnessReport",
    "Language",
    "TestCase",
    "TestSuite",
    "Verdict",
    "VerdictStatus",
]

MAX_CODE_CHARS: Final = 50_000
MAX_TEST_CASES: Final = 100
DEFAULT_TIMEOUT_S: Final = 5.0
MIN_TIMEOUT_S: Final = 0.5
MAX_TIMEOUT_S: Final = 30.0

Language = Literal["python"]


# --------------------------------------------------------------------------
# Request
# --------------------------------------------------------------------------


class TestCase(APIModel):
    """A single call to the submission's entrypoint plus its expected result."""

    __test__: ClassVar[bool] = False

    name: str = Field(min_length=1, max_length=128)
    args: list[JsonValue] = Field(default_factory=list[JsonValue])
    kwargs: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    expected: JsonValue


class TestSuite(APIModel):
    """An entrypoint name plus the cases to call it with."""

    __test__: ClassVar[bool] = False

    entrypoint: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=128)
    cases: list[TestCase] = Field(min_length=1, max_length=MAX_TEST_CASES)

    @field_validator("cases")
    @classmethod
    def _unique_case_names(cls, value: list[TestCase]) -> list[TestCase]:
        names = [case.name for case in value]
        if len(names) != len(set(names)):
            raise ValueError("test case names must be unique")
        return value


class ExecutionRequest(APIModel):
    """A submission to run in the sandbox, with optional test cases."""

    language: Language = "python"
    code: str = Field(min_length=1, max_length=MAX_CODE_CHARS)
    tests: TestSuite | None = None
    timeout_s: float = Field(default=DEFAULT_TIMEOUT_S, ge=MIN_TIMEOUT_S, le=MAX_TIMEOUT_S)


# --------------------------------------------------------------------------
# Harness report mirror (must parse the harness JSON exactly)
# --------------------------------------------------------------------------

HarnessPhase = Literal["protocol", "compile", "load", "entrypoint", "script", "tests"]


class HarnessError(APIModel):
    """An error surfaced by the harness (compile/load/runtime/protocol)."""

    type: str
    message: str
    lineno: int | None = None


class CaseResult(APIModel):
    """The harness's report for a single test case."""

    name: str
    passed: bool
    actual: JsonValue = None
    actual_repr: str = ""
    actual_sha256: str | None = None
    actual_truncated: bool = False
    error: HarnessError | None = None
    stdout: str = ""
    duration_ms: float = Field(ge=0)


class HarnessReport(APIModel):
    """The full JSON report line emitted by `docker/harness/run.py`."""

    version: Literal[1]
    phase: HarnessPhase
    error: HarnessError | None
    stdout: str
    stderr: str
    cases: list[CaseResult]


# --------------------------------------------------------------------------
# Execution result (host-side, harness report + process-level outcome)
# --------------------------------------------------------------------------

ExecutionStatus = Literal[
    "passed",  # tests phase, all cases passed
    "failed",  # tests phase, at least one case failed
    "completed",  # script mode (no tests) ran cleanly
    "compile_error",  # user code failed to compile
    "runtime_error",  # load/script raised, or the process died without a report
    "entrypoint_missing",  # tests requested but the entrypoint wasn't found/callable
    "timeout",  # wall-clock kill
    "memory_exceeded",  # OOM kill
    "sandbox_error",  # infra/protocol problem: docker down, image missing, malformed report
    "rejected",  # refused before running: payload too large, unsupported language, etc.
]


class ExecutionResult(APIModel):
    """The host-side outcome of running a submission in the sandbox."""

    status: ExecutionStatus
    language: Language = "python"
    exit_code: int | None = None
    duration_ms: float = Field(default=0, ge=0)
    timed_out: bool = False
    oom_killed: bool = False
    stdout: str = ""
    stderr: str = ""
    phase: HarnessPhase | None = None
    error: HarnessError | None = None
    cases: list[CaseResult] = Field(default_factory=list[CaseResult])
    output_truncated: bool = False

    @property
    def cases_total(self) -> int:
        return len(self.cases)

    @property
    def cases_passed(self) -> int:
        return sum(1 for case in self.cases if case.passed)

    @property
    def first_failure(self) -> CaseResult | None:
        for case in self.cases:
            if not case.passed:
                return case
        return None


# --------------------------------------------------------------------------
# Verdict (consumed by the P5 verifier + graph)
# --------------------------------------------------------------------------

VerdictStatus = Literal["pass", "fail", "inconclusive", "skipped"]

FailureCategory = Literal[
    "wrong_answer",
    "runtime_error",
    "timeout",
    "memory_limit",
    "syntax_error",
    "missing_entrypoint",
    "no_tests",
    "sandbox_error",
    "rejected",
]


class Verdict(APIModel):
    """The higher-level pass/fail judgement derived from an `ExecutionResult`."""

    status: VerdictStatus
    category: FailureCategory | None = None
    first_failing_case: str | None = None
    expected: JsonValue = None
    actual: JsonValue = None
    error_type: str | None = None
    summary: str
    diagnostics: list[str] = Field(default_factory=list[str])
    cases_passed: int = Field(default=0, ge=0)
    cases_total: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _check_status_invariants(self) -> "Verdict":
        if self.status == "pass":
            if self.category is not None:
                raise ValueError("a 'pass' verdict must not have a failure category")
            if self.cases_passed != self.cases_total:
                raise ValueError("a 'pass' verdict must have cases_passed == cases_total")
        if self.status == "fail" and self.category is None:
            raise ValueError("a 'fail' verdict must have a failure category")
        return self
