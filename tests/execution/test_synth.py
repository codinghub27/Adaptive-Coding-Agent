"""Tests for `app.execution.synth.synthesize_test_suite` (Phase 07 packet P1a).

`FakeLLMClient` (`tests.input.fakes`) stands in for the LLM; `_StaticRunner`
(local, mirroring `SequencedRunner` in `tests/agents/test_debugger.py`) stands
in for the sandbox `CodeRunner` -- no real LLM, no Docker. The one hash helper
(`_sha256`) mirrors `tests/agents/test_debugger.py::_sha256`: the verifier
never trusts a harness's self-reported `passed` flag, only the sha256 of its
canonicalized `actual` against the host's own hash of the expected value.
"""

from __future__ import annotations

import ast
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from pydantic import JsonValue

from app.execution.base import canonical
from app.execution.synth import MAX_SYNTH_CASES, synthesize_test_suite
from app.llm.budget import BudgetedLLMClient
from app.schemas.execution import (
    CaseResult,
    ExecutionRequest,
    ExecutionResult,
    HarnessError,
    TestSuite,
)
from app.schemas.input import CodeBlock, StructuredInput
from tests.input.fakes import FakeLLMClient

SYNTH_FILE = Path(__file__).resolve().parents[2] / "app" / "execution" / "synth.py"

_LEARNER_CODE = "def double(x):\n    return x * 2\n"


class _StaticRunner:
    """A `CodeRunner` stand-in returning one canned `ExecutionResult` always."""

    def __init__(self, result: ExecutionResult) -> None:
        self.result = result
        self.calls: list[ExecutionRequest] = []

    async def run(self, request: ExecutionRequest) -> ExecutionResult:
        self.calls.append(request)
        return self.result


def _problem(
    *, statement: str = "Double the input.", code: str | None = _LEARNER_CODE
) -> StructuredInput:
    return StructuredInput(
        source="text",
        problem=statement,
        code=[CodeBlock(content=code)] if code else [],
    )


def _sha256(value: JsonValue) -> str:
    """The host-authoritative hash a real harness would report for `value`."""
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _synth_content(
    *,
    entrypoint: str = "double",
    reference_solution: str = "def double(x):\n    return x * 2\n",
    cases: list[dict[str, JsonValue]] | None = None,
) -> str:
    if cases is None:
        cases = [{"name": "c1", "args": [2], "kwargs": {}, "expected": 4}]
    return json.dumps(
        {"entrypoint": entrypoint, "reference_solution": reference_solution, "cases": cases}
    )


def _passing_result(names_and_expected: Sequence[tuple[str, JsonValue]]) -> ExecutionResult:
    return ExecutionResult(
        status="passed",
        phase="tests",
        cases=[
            CaseResult(
                name=name,
                passed=True,
                actual=expected,
                actual_repr=repr(expected),
                actual_sha256=_sha256(expected),
                duration_ms=1.0,
            )
            for name, expected in names_and_expected
        ],
    )


def _failing_result(name: str, *, expected: JsonValue, actual: JsonValue) -> ExecutionResult:
    return ExecutionResult(
        status="failed",
        phase="tests",
        cases=[
            CaseResult(
                name=name,
                passed=False,
                actual=actual,
                actual_repr=repr(actual),
                actual_sha256=_sha256(actual),
                duration_ms=1.0,
            )
        ],
    )


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------


async def test_reference_passes_returns_suite_with_learner_entrypoint() -> None:
    # The LLM's own "entrypoint" field says "solve" -- a name the learner
    # never wrote -- to prove selection ignores it and uses the learner's
    # real function name instead.
    llm = FakeLLMClient(chat_content=_synth_content(entrypoint="solve"))
    runner = _StaticRunner(_passing_result([("c1", 4)]))

    suite = await synthesize_test_suite(_problem(), llm, runner)

    assert suite is not None
    assert isinstance(suite, TestSuite)
    assert suite.entrypoint == "double"
    assert len(suite.cases) == 1
    assert runner.calls, "the reference solution must actually be run in the sandbox"
    assert runner.calls[0].code == "def double(x):\n    return x * 2\n"


# --------------------------------------------------------------------------
# The core guarantee: a reference that doesn't actually pass never escapes
# --------------------------------------------------------------------------


async def test_reference_fails_a_case_returns_none() -> None:
    llm = FakeLLMClient(chat_content=_synth_content())
    runner = _StaticRunner(_failing_result("c1", expected=4, actual=5))

    suite = await synthesize_test_suite(_problem(), llm, runner)

    assert suite is None


async def test_reference_runtime_error_returns_none() -> None:
    llm = FakeLLMClient(chat_content=_synth_content())
    runner = _StaticRunner(
        ExecutionResult(
            status="runtime_error",
            phase="script",
            error=HarnessError(type="ZeroDivisionError", message="division by zero"),
        )
    )

    assert await synthesize_test_suite(_problem(), llm, runner) is None


async def test_reference_sandbox_error_returns_none() -> None:
    llm = FakeLLMClient(chat_content=_synth_content())
    runner = _StaticRunner(
        ExecutionResult(
            status="sandbox_error",
            error=HarnessError(type="DockerException", message="daemon unreachable"),
        )
    )

    assert await synthesize_test_suite(_problem(), llm, runner) is None


async def test_reference_timeout_returns_none() -> None:
    llm = FakeLLMClient(chat_content=_synth_content())
    runner = _StaticRunner(ExecutionResult(status="timeout", timed_out=True))

    assert await synthesize_test_suite(_problem(), llm, runner) is None


# --------------------------------------------------------------------------
# LLM response is malformed / not usable
# --------------------------------------------------------------------------


async def test_llm_unparseable_text_returns_none() -> None:
    llm = FakeLLMClient(chat_content="I refuse to answer in JSON today.")
    runner = _StaticRunner(_passing_result([("c1", 4)]))

    assert await synthesize_test_suite(_problem(), llm, runner) is None
    assert not runner.calls, "an unparseable response must never reach the sandbox"


async def test_llm_json_missing_cases_returns_none() -> None:
    llm = FakeLLMClient(
        chat_content=json.dumps(
            {"entrypoint": "double", "reference_solution": "def double(x):\n    return x*2\n"}
        )
    )
    runner = _StaticRunner(_passing_result([("c1", 4)]))

    assert await synthesize_test_suite(_problem(), llm, runner) is None


async def test_llm_json_missing_reference_solution_returns_none() -> None:
    llm = FakeLLMClient(
        chat_content=json.dumps(
            {
                "entrypoint": "double",
                "cases": [{"name": "c1", "args": [2], "kwargs": {}, "expected": 4}],
            }
        )
    )
    runner = _StaticRunner(_passing_result([("c1", 4)]))

    assert await synthesize_test_suite(_problem(), llm, runner) is None


async def test_llm_case_missing_name_returns_none() -> None:
    llm = FakeLLMClient(
        chat_content=_synth_content(cases=[{"args": [2], "kwargs": {}, "expected": 4}])
    )
    runner = _StaticRunner(_passing_result([("c1", 4)]))

    assert await synthesize_test_suite(_problem(), llm, runner) is None


# --------------------------------------------------------------------------
# Entrypoint must come from the learner's code, never the LLM's say-so
# --------------------------------------------------------------------------


async def test_case_shape_absent_from_learner_code_returns_none() -> None:
    # The learner's only function is `double(x)`. These cases can only ever
    # call a function with a `n` keyword parameter -- an interface `double`
    # doesn't have -- so no learner function fits and selection must fail,
    # regardless of what the LLM's "entrypoint" field claims.
    llm = FakeLLMClient(
        chat_content=_synth_content(
            entrypoint="solve",
            reference_solution="def solve(n):\n    return n * 2\n",
            cases=[{"name": "c1", "args": [], "kwargs": {"n": 2}, "expected": 4}],
        )
    )
    runner = _StaticRunner(_passing_result([("c1", 4)]))

    assert await synthesize_test_suite(_problem(), llm, runner) is None
    assert not runner.calls, "a suite with no valid entrypoint must never reach the sandbox"


async def test_reference_solution_missing_selected_entrypoint_returns_none() -> None:
    # Cases fit the learner's real `double(x)`, so selection succeeds -- but
    # the reference solution never defines `double` at all.
    llm = FakeLLMClient(
        chat_content=_synth_content(
            entrypoint="double",
            reference_solution="def solve(x):\n    return x * 2\n",
        )
    )
    runner = _StaticRunner(_passing_result([("c1", 4)]))

    assert await synthesize_test_suite(_problem(), llm, runner) is None
    assert not runner.calls, "a reference missing the entrypoint must never reach the sandbox"


# --------------------------------------------------------------------------
# Guards
# --------------------------------------------------------------------------


async def test_problem_none_returns_none() -> None:
    llm = FakeLLMClient(chat_content=_synth_content())
    runner = _StaticRunner(_passing_result([("c1", 4)]))

    assert await synthesize_test_suite(None, llm, runner) is None
    assert not llm.chat_calls


async def test_no_learner_code_returns_none() -> None:
    llm = FakeLLMClient(chat_content=_synth_content())
    runner = _StaticRunner(_passing_result([("c1", 4)]))

    assert await synthesize_test_suite(_problem(code=None), llm, runner) is None
    assert not llm.chat_calls


async def test_runner_none_returns_none_and_llm_not_called() -> None:
    llm = FakeLLMClient(chat_content=_synth_content())

    assert await synthesize_test_suite(_problem(), llm, None) is None
    assert not llm.chat_calls, "with no sandbox to validate against, the LLM must not be spent"


# --------------------------------------------------------------------------
# Case-count cap
# --------------------------------------------------------------------------


async def test_more_than_max_synth_cases_rejected() -> None:
    names_and_expected = [(f"c{i}", i * 2) for i in range(MAX_SYNTH_CASES + 1)]
    too_many: list[dict[str, JsonValue]] = [
        {"name": name, "args": [i], "kwargs": {}, "expected": expected}
        for i, (name, expected) in enumerate(names_and_expected)
    ]
    llm = FakeLLMClient(chat_content=_synth_content(cases=too_many))
    runner = _StaticRunner(_passing_result(names_and_expected))

    assert await synthesize_test_suite(_problem(), llm, runner) is None
    assert not runner.calls, "an oversized case set must never reach the sandbox"


# --------------------------------------------------------------------------
# Budget / fail-soft
# --------------------------------------------------------------------------


async def test_llm_budget_exceeded_returns_none() -> None:
    inner = FakeLLMClient(chat_content=_synth_content())
    budgeted = BudgetedLLMClient(inner, max_calls=0)
    runner = _StaticRunner(_passing_result([("c1", 4)]))

    suite = await synthesize_test_suite(_problem(), budgeted, runner)

    assert suite is None
    assert not inner.chat_calls, "the budget must reject before the inner client is ever called"
    assert not runner.calls


# --------------------------------------------------------------------------
# Static guard: no host execution anywhere in this module
# --------------------------------------------------------------------------

_FORBIDDEN_BUILTINS = {"exec", "eval", "compile"}
_FORBIDDEN_MODULES = {"subprocess", "pty"}


def test_synth_module_never_executes_code_on_host() -> None:
    assert SYNTH_FILE.is_file(), f"expected {SYNTH_FILE} to exist"
    tree = ast.parse(SYNTH_FILE.read_text(encoding="utf-8"), filename=str(SYNTH_FILE))

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in _FORBIDDEN_MODULES:
                    violations.append(f"line {node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in _FORBIDDEN_MODULES:
                violations.append(f"line {node.lineno}: from {node.module} import ...")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in _FORBIDDEN_BUILTINS:
                violations.append(f"line {node.lineno}: call to builtin {func.id}()")
            elif (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "os"
                and (func.attr == "system" or func.attr.startswith("popen"))
            ):
                violations.append(f"line {node.lineno}: call to os.{func.attr}()")

    assert not violations, "forbidden host-execution calls found:\n" + "\n".join(violations)
