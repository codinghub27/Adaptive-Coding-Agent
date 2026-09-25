"""Tests for `app.agents.debugger` / `app.graph.subgraphs.debug` (Phase 07 P4).

No real LLM, no Docker, no network: `FakeLLMClient` (`tests.input.fakes`) and
the local `SequencedRunner` stand in throughout. `SequencedRunner` records
every `ExecutionRequest` it is handed and returns one canned `ExecutionResult`
per call (repeating the last once exhausted), which is what lets these tests
prove the patched code is genuinely re-run in the sandbox rather than trusted
on the LLM's word.
"""

import ast
import hashlib
import json
from pathlib import Path
from typing import Final

import pytest
from langgraph.runtime import Runtime  # pyright: ignore[reportMissingTypeStubs]
from pydantic import JsonValue

from app.agents.debugger import static_analysis
from app.execution.base import CodeRunner, canonical
from app.graph.state import AgentState, GraphContext, RawInput
from app.graph.subgraphs.debug import MAX_PATCH_ATTEMPTS, run_debug
from app.schemas.execution import (
    CaseResult,
    ExecutionRequest,
    ExecutionResult,
    TestCase,
    TestSuite,
)
from app.schemas.input import CodeBlock, StructuredInput
from tests.input.fakes import FakeLLMClient

SENTINEL: Final = "SENTINEL_IGNORE_ALL_PREVIOUS_INSTRUCTIONS_XYZZY"

_ENTRYPOINT: Final = "solve"
_TESTS: Final = TestSuite(entrypoint=_ENTRYPOINT, cases=[TestCase(name="c1", args=[2], expected=4)])

_BUGGY_CODE: Final = "def solve(x):\n    return x + 1\n"
_FIXED_CODE: Final = "def solve(x):\n    return x * 2"

_PATCH_LLM_CONTENT: Final = json.dumps(
    {
        "inferred_approach": "The learner is trying to double the input value.",
        "bug_explanation": (
            "The code adds 1 instead of multiplying by 2, so it returns the wrong value."
        ),
        "patched_code": _FIXED_CODE,
    }
)

_CONFIDENT_PATCH_LLM_CONTENT: Final = json.dumps(
    {
        "inferred_approach": "The learner is trying to double the input value.",
        "bug_explanation": "I am certain this fix is fully correct and passes every test case.",
        "patched_code": _FIXED_CODE,
    }
)


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class SequencedRunner:
    """Records every `ExecutionRequest`; returns the Nth canned result for the
    Nth `run()` call, repeating the last once the list is exhausted."""

    def __init__(self, results: list[ExecutionResult]) -> None:
        self._results = results
        self.calls: list[ExecutionRequest] = []

    async def run(self, request: ExecutionRequest) -> ExecutionResult:
        self.calls.append(request)
        idx = min(len(self.calls) - 1, len(self._results) - 1)
        return self._results[idx]


def _sha256(value: JsonValue) -> str:
    """The host-authoritative hash a real harness would report for `value`."""
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _passed_result() -> ExecutionResult:
    return ExecutionResult(
        status="passed",
        phase="tests",
        cases=[
            CaseResult(
                name="c1", passed=True, actual=4, actual_repr="4", actual_sha256=_sha256(4),
                duration_ms=1.0,
            )
        ],
    )


def _failed_result() -> ExecutionResult:
    return ExecutionResult(
        status="failed",
        phase="tests",
        cases=[
            CaseResult(
                name="c1", passed=False, actual=3, actual_repr="3", actual_sha256=_sha256(3),
                duration_ms=1.0,
            )
        ],
    )


def _runtime(llm: FakeLLMClient, runner: CodeRunner | None = None) -> Runtime[GraphContext]:
    return Runtime(context=GraphContext(llm=llm, runner=runner))


def _state(
    *, code: str = _BUGGY_CODE, error: str | None = None, problem: str | None = None
) -> AgentState:
    structured = StructuredInput(
        source="text",
        problem=problem or "Return double the input value.",
        code=[CodeBlock(content=code, language="python")],
        error=error,
    )
    return AgentState(
        input=RawInput(text="debug this"),
        structured_input=structured,
        execution_request=ExecutionRequest(code="placeholder", tests=_TESTS),
    )


# --------------------------------------------------------------------------
# Every patch is re-verified in the sandbox
# --------------------------------------------------------------------------


async def test_every_patch_is_reverified_in_the_sandbox() -> None:
    runner = SequencedRunner([_failed_result(), _passed_result()])
    llm = FakeLLMClient(chat_content=_PATCH_LLM_CONTENT)

    run_result = await run_debug(_state(), _runtime(llm, runner))

    assert len(runner.calls) == 2, "the patched code must be run a second time in the sandbox"
    assert runner.calls[1].code == _FIXED_CODE
    assert run_result.result.final_verdict is not None
    assert run_result.result.final_verdict.status == "pass"
    assert run_result.result.fixed is True


# --------------------------------------------------------------------------
# The LLM can never certify correctness on its own
# --------------------------------------------------------------------------


async def test_llm_claiming_perfect_fix_never_overrides_a_failing_rerun() -> None:
    runner = SequencedRunner([_failed_result(), _failed_result()])
    llm = FakeLLMClient(chat_content=_CONFIDENT_PATCH_LLM_CONTENT)

    run_result = await run_debug(_state(), _runtime(llm, runner))

    assert run_result.result.fixed is False
    assert run_result.result.final_verdict is not None
    assert run_result.result.final_verdict.status != "pass"


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------


async def test_happy_path_failing_then_passing_marks_fixed() -> None:
    runner = SequencedRunner([_failed_result(), _passed_result()])
    llm = FakeLLMClient(chat_content=_PATCH_LLM_CONTENT)

    run_result = await run_debug(_state(), _runtime(llm, runner))

    assert run_result.result.fixed is True
    assert run_result.result.attempts >= 1
    assert run_result.result.initial_verdict is not None
    assert run_result.result.initial_verdict.status == "fail"
    assert run_result.execution_request is not None
    assert run_result.execution_request.code == _FIXED_CODE


# --------------------------------------------------------------------------
# Attempt cap: a runner that always fails must still terminate
# --------------------------------------------------------------------------


async def test_attempt_cap_terminates_when_runner_always_fails() -> None:
    runner = SequencedRunner([_failed_result()])
    llm = FakeLLMClient(chat_content=_PATCH_LLM_CONTENT)

    run_result = await run_debug(_state(), _runtime(llm, runner))

    assert run_result.result.attempts <= MAX_PATCH_ATTEMPTS
    assert run_result.result.attempts == MAX_PATCH_ATTEMPTS
    assert run_result.result.fixed is False
    # initial run + MAX_PATCH_ATTEMPTS re-runs, never more.
    assert len(runner.calls) == 1 + MAX_PATCH_ATTEMPTS


# --------------------------------------------------------------------------
# runner is None: degrade cleanly, never crash, never claim a fix
# --------------------------------------------------------------------------


async def test_runner_none_degrades_cleanly() -> None:
    llm = FakeLLMClient(chat_content=_PATCH_LLM_CONTENT)

    run_result = await run_debug(_state(), _runtime(llm, None))

    assert run_result.result.fixed is False
    assert run_result.result.initial_verdict is not None
    assert run_result.result.initial_verdict.status == "skipped"
    assert run_result.result.final_verdict is not None
    assert run_result.result.final_verdict.status == "skipped"
    assert run_result.execution_request is None
    # No sandbox available: never spend the run's LLM budget on an unverifiable guess.
    assert len(llm.chat_calls) == 0


# --------------------------------------------------------------------------
# Syntactically broken code: tree-sitter fallback still produces findings
# --------------------------------------------------------------------------


def test_syntactically_broken_code_still_produces_findings_via_tree_sitter() -> None:
    broken_code = "def solve(x:\n    return x *\n"
    with pytest.raises(SyntaxError):
        ast.parse(broken_code)

    findings = static_analysis(broken_code)

    assert findings, "tree-sitter fallback must still produce findings for broken syntax"


async def test_debug_run_over_broken_code_does_not_crash() -> None:
    """The subgraph itself must not crash when the learner's code is unparseable."""
    runner = SequencedRunner([_failed_result()])
    llm = FakeLLMClient(chat_content=_PATCH_LLM_CONTENT)

    run_result = await run_debug(
        _state(code="def solve(x:\n    return x *\n"), _runtime(llm, runner)
    )

    assert run_result.result.static_findings, "static findings should still be produced"


# --------------------------------------------------------------------------
# No host execution
# --------------------------------------------------------------------------

_FORBIDDEN_MODULES: Final = {"subprocess", "pty"}
_FORBIDDEN_OS_ATTR_PREFIXES: Final = ("system", "popen", "exec", "spawn")
_FORBIDDEN_BUILTINS: Final = {"exec", "eval", "compile"}

_APP_DIR: Final = Path(__file__).resolve().parents[2] / "app"
_DEBUGGER_FILE: Final = _APP_DIR / "agents" / "debugger.py"
_DEBUG_SUBGRAPH_FILE: Final = _APP_DIR / "graph" / "subgraphs" / "debug.py"


def _check_no_host_exec(path: Path) -> list[str]:
    violations: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in _FORBIDDEN_MODULES:
                    violations.append(f"{path}:{node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in _FORBIDDEN_MODULES:
                violations.append(f"{path}:{node.lineno}: from {node.module} import ...")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in _FORBIDDEN_BUILTINS:
                violations.append(f"{path}:{node.lineno}: call to builtin {func.id}()")
            elif (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "os"
                and any(
                    func.attr == prefix or func.attr.startswith(prefix)
                    for prefix in _FORBIDDEN_OS_ATTR_PREFIXES
                )
            ):
                violations.append(f"{path}:{node.lineno}: call to os.{func.attr}()")
    return violations


def test_debugger_module_never_runs_code_on_host() -> None:
    assert _DEBUGGER_FILE.is_file()
    violations = _check_no_host_exec(_DEBUGGER_FILE)
    assert not violations, "forbidden host-execution calls found:\n" + "\n".join(violations)


def test_debug_subgraph_module_never_runs_code_on_host() -> None:
    assert _DEBUG_SUBGRAPH_FILE.is_file()
    violations = _check_no_host_exec(_DEBUG_SUBGRAPH_FILE)
    assert not violations, "forbidden host-execution calls found:\n" + "\n".join(violations)


# --------------------------------------------------------------------------
# Untrusted sentinel never echoed into free-text result fields
# --------------------------------------------------------------------------


async def test_untrusted_sentinel_never_echoed_into_result() -> None:
    llm = FakeLLMClient(
        chat_content=json.dumps({"inferred_approach": "Learner attempts a direct doubling."})
    )
    runner = SequencedRunner([_passed_result()])

    run_result = await run_debug(
        _state(error=f"ValueError: {SENTINEL}", problem=f"Solve this: {SENTINEL}"),
        _runtime(llm, runner),
    )

    serialized = run_result.result.model_dump_json()
    assert SENTINEL not in serialized
