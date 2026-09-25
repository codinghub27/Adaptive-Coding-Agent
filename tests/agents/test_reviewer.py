"""Tests for `app.agents.reviewer` (Phase 07 P6).

No real LLM, no Docker, no network: `FakeLLMClient` (`tests.input.fakes`) and
the local `FakeRunner` stand in throughout. `FakeRunner` is what proves
correctness findings are grounded in an actual sandbox `Verdict`, never in
whatever the (possibly lying) LLM claims.
"""

import ast
import hashlib
import json
from pathlib import Path
from typing import Final

from langgraph.runtime import Runtime  # pyright: ignore[reportMissingTypeStubs]
from pydantic import JsonValue

from app.agents.reviewer import review_code
from app.execution.base import CodeRunner, canonical
from app.graph.state import AgentState, GraphContext, RawInput
from app.schemas.execution import CaseResult, ExecutionRequest, ExecutionResult, TestCase, TestSuite
from app.schemas.input import CodeBlock, StructuredInput
from tests.input.fakes import FakeLLMClient

_ENTRYPOINT: Final = "solve"
_TESTS: Final = TestSuite(entrypoint=_ENTRYPOINT, cases=[TestCase(name="c1", args=[2], expected=4)])
_CODE: Final = "def solve(x):\n    return x + 1\n"

_MESSY_CODE: Final = (
    "import os\n"
    "\n"
    "def BadName(items=[]):\n"
    "    try:\n"
    "        return items[0]\n"
    "    except:\n"
    "        return None\n"
)

# A confident (and, per the failing-runner tests, WRONG) claim of correctness --
# also smuggles a "correctness" category finding, which must never survive
# `_to_review_findings`'s category allow-list regardless of what it says.
_CONFIDENT_LLM_CONTENT: Final = json.dumps(
    {
        "findings": [
            {
                "category": "correctness",
                "severity": "info",
                "message": "This code is completely correct and passes every test case.",
                "lineno": None,
                "suggestion": None,
            }
        ]
    }
)

_MULTI_CATEGORY_LLM_CONTENT: Final = json.dumps(
    {
        "findings": [
            {
                "category": "readability",
                "severity": "minor",
                "message": "Consider a more descriptive parameter name.",
                "lineno": 3,
                "suggestion": None,
            },
            {
                "category": "python_practices",
                "severity": "minor",
                "message": "Prefer explicit key checks over bare indexing.",
                "lineno": None,
                "suggestion": None,
            },
            {
                "category": "edge_cases",
                "severity": "info",
                "message": "Consider an empty items list.",
                "lineno": None,
                "suggestion": "Add a guard clause.",
            },
            {
                "category": "improvements",
                "severity": "info",
                "message": "Could early-return for clarity.",
                "lineno": None,
                "suggestion": None,
            },
            {
                "category": "correctness",
                "severity": "info",
                "message": "This code is definitely correct.",
                "lineno": None,
                "suggestion": None,
            },
        ]
    }
)


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class FakeRunner:
    """Records every `ExecutionRequest` it's handed; returns one canned result."""

    def __init__(self, result: ExecutionResult) -> None:
        self.result = result
        self.calls: list[ExecutionRequest] = []

    async def run(self, request: ExecutionRequest) -> ExecutionResult:
        self.calls.append(request)
        return self.result


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


def _state(*, code: str, tests: TestSuite | None) -> AgentState:
    structured = StructuredInput(source="text", code=[CodeBlock(content=code, language="python")])
    execution_request = ExecutionRequest(code="placeholder", tests=tests) if tests else None
    return AgentState(
        input=RawInput(text="review this"),
        structured_input=structured,
        execution_request=execution_request,
    )


# --------------------------------------------------------------------------
# No correctness claim without a passing verdict
# --------------------------------------------------------------------------


async def test_no_correctness_claim_without_a_passing_verdict() -> None:
    runner = FakeRunner(_failed_result())
    llm = FakeLLMClient(chat_content=_CONFIDENT_LLM_CONTENT)

    run_result = await review_code(_state(code=_CODE, tests=_TESTS), _runtime(llm, runner))

    assert run_result.result.claims_correct is False
    assert run_result.result.correctness_verdict is not None
    assert run_result.result.correctness_verdict.status == "fail"

    correctness_findings = [f for f in run_result.result.findings if f.category == "correctness"]
    assert len(correctness_findings) == 1, "the LLM's smuggled correctness finding must be dropped"
    assert "not confirmed" in correctness_findings[0].message.lower()
    assert "completely correct" not in correctness_findings[0].message.lower()


# --------------------------------------------------------------------------
# No runner: correctness_verdict is None, no crash, no assertion either way
# --------------------------------------------------------------------------


async def test_no_runner_leaves_correctness_verdict_none() -> None:
    llm = FakeLLMClient(chat_content=_CONFIDENT_LLM_CONTENT)

    run_result = await review_code(_state(code=_CODE, tests=_TESTS), _runtime(llm, None))

    assert run_result.result.correctness_verdict is None
    assert run_result.result.claims_correct is False
    correctness_findings = [f for f in run_result.result.findings if f.category == "correctness"]
    assert len(correctness_findings) == 1
    assert "could not be verified" in correctness_findings[0].message.lower()


async def test_no_tests_leaves_correctness_verdict_none() -> None:
    runner = FakeRunner(_passed_result())
    llm = FakeLLMClient(chat_content=_CONFIDENT_LLM_CONTENT)

    run_result = await review_code(_state(code=_CODE, tests=None), _runtime(llm, runner))

    assert run_result.result.correctness_verdict is None
    assert runner.calls == [], "no tests to check against: the runner must never be called"


# --------------------------------------------------------------------------
# Passing tests -> a genuine pass verdict
# --------------------------------------------------------------------------


async def test_passing_tests_yield_pass_verdict() -> None:
    runner = FakeRunner(_passed_result())
    llm = FakeLLMClient(chat_content=_CONFIDENT_LLM_CONTENT)

    run_result = await review_code(_state(code=_CODE, tests=_TESTS), _runtime(llm, runner))

    assert run_result.result.correctness_verdict is not None
    assert run_result.result.correctness_verdict.status == "pass"
    assert run_result.result.claims_correct is True
    assert run_result.execution_request is not None
    assert run_result.execution_request.code == _CODE


# --------------------------------------------------------------------------
# Findings span multiple categories; severities are valid
# --------------------------------------------------------------------------


async def test_findings_span_multiple_categories_with_valid_severities() -> None:
    llm = FakeLLMClient(chat_content=_MULTI_CATEGORY_LLM_CONTENT)

    run_result = await review_code(_state(code=_MESSY_CODE, tests=None), _runtime(llm, None))

    categories = {f.category for f in run_result.result.findings}
    expected = {"python_practices", "readability", "complexity", "edge_cases", "improvements"}
    assert expected <= categories

    valid_severities = {"info", "minor", "major"}
    assert all(f.severity in valid_severities for f in run_result.result.findings)

    # Deterministic checks must have actually fired for this snippet.
    messages = " ".join(f.message for f in run_result.result.findings)
    assert "mutable default" in messages.lower()
    assert "bare 'except:'" in messages.lower()
    assert "never used" in messages.lower()


# --------------------------------------------------------------------------
# No host execution
# --------------------------------------------------------------------------

_FORBIDDEN_MODULES: Final = {"subprocess", "pty"}
_FORBIDDEN_OS_ATTR_PREFIXES: Final = ("system", "popen", "exec", "spawn")
_FORBIDDEN_BUILTINS: Final = {"exec", "eval", "compile"}

_APP_DIR: Final = Path(__file__).resolve().parents[2] / "app"
_REVIEWER_FILE: Final = _APP_DIR / "agents" / "reviewer.py"


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


def test_reviewer_module_never_runs_code_on_host() -> None:
    assert _REVIEWER_FILE.is_file()
    violations = _check_no_host_exec(_REVIEWER_FILE)
    assert not violations, "forbidden host-execution calls found:\n" + "\n".join(violations)
