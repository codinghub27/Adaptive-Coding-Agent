"""The debugger subgraph: static analysis + sandbox-grounded bug fixing.

`DebugState` is this subgraph's own **private** scratch state -- never
`AgentState`, which is frozen with `extra="forbid"` and must not grow scratch
fields for one capability (mirrors `app.graph.subgraphs.dsa.DSAState`).
`run_debug` is the clean boundary the outer graph (Phase 07 P7's
`debug_agent` node) calls: it maps `AgentState` onto a fresh `DebugState`,
runs the compiled subgraph once, and maps the finished `DebugState` back onto
a `DebugResult` plus the `ExecutionRequest` P7 should hand to the outer
`execute_code`/`verify` edges for the authoritative re-verification.

Pipeline: `static_analysis -> infer_approach -> run_tests -> find_failing_case
-> localize -> explain -> patch -> re_run -> verify_final`, with a bounded
conditional edge from `verify_final` back to `patch` (at most
`MAX_PATCH_ATTEMPTS` cycles) when the patched code still fails. Every stage
that produces free text is either a pure function of already-established
sandbox/static facts (`find_failing_case`, `localize`) or one of exactly
three possible LLM calls per run (`infer_approach`, `explain`, `patch` --
`patch` may repeat up to the attempt cap), keeping this subgraph inside the
run's `BudgetedLLMClient` budget.

**Never executes code directly.** The only place anything in this subgraph
is ever run is `runtime.context.runner` (`run_tests`/`re_run`); everything
else only parses (`app.agents.debugger.static_analysis`) or calls an LLM.
`app.execution.verification.verify` is the single, pure source of pass/fail
truth -- `DebugResult.fixed` can only ever become `True` by mirroring a
passing `final_verdict` from an actual second sandbox run of the patched
code; nothing here ever trusts an LLM's own claim that a fix works.

`state["problem"]` (sourced from `AgentState.structured_input`) is untrusted
learner content, exactly as documented in `app.graph.nodes`'s module
docstring and `app.agents.debugger`'s; it is only ever handed to the LLM as
clearly-delimited data to reason about, never echoed into `DebugResult`'s
free-text fields.
"""

from dataclasses import dataclass
from functools import cache
from typing import Final, Literal, TypedDict

from langgraph.graph import END, START, StateGraph  # pyright: ignore[reportMissingTypeStubs]
from langgraph.graph.state import (  # pyright: ignore[reportMissingTypeStubs]
    CompiledStateGraph,
)
from langgraph.runtime import Runtime

from app.agents.debugger import (
    explain_bug,
    extract_learner_code,
    failing_case_summary,
    infer_approach,
    localize_bug,
    patch_code,
    static_analysis,
)
from app.execution.verification import verify
from app.graph.state import AgentState, GraphContext
from app.schemas.agent_results import BugLocation, DebugResult, StaticFinding
from app.schemas.execution import (
    ExecutionRequest,
    ExecutionResult,
    HarnessError,
    TestSuite,
    Verdict,
)
from app.schemas.input import StructuredInput

__all__ = [
    "MAX_PATCH_ATTEMPTS",
    "DebugRunResult",
    "DebugState",
    "build_debug_graph",
    "get_debug_graph",
    "run_debug",
]

MAX_PATCH_ATTEMPTS: Final = 2
"""Hard cap on patch+re-verify cycles per run. Impossible to exceed: the
conditional edge out of `verify_final` only ever routes back to `patch` while
`attempts < MAX_PATCH_ATTEMPTS`."""

_SANDBOX_RUN_FAILED_MESSAGE: Final = "running your code failed unexpectedly"


class DebugState(TypedDict, total=False):
    """Private scratch state threaded through the debugger subgraph only.

    `problem`/`code`/`tests` are this run's inputs (set once by `run_debug`
    and never rewritten by any node); every other field is a stage output.
    `attempts` counts real `patch` calls made so far -- the single source of
    truth `run_debug` copies onto `DebugResult.attempts`.
    """

    problem: StructuredInput | None
    code: str | None
    tests: TestSuite | None

    static_findings: list[StaticFinding]
    inferred_approach: str | None

    request: ExecutionRequest | None
    result: ExecutionResult | None
    initial_verdict: Verdict | None

    failing_case: str | None
    has_established_failure: bool
    bug_location: BugLocation | None
    bug_explanation: str | None

    patched_code: str | None
    final_request: ExecutionRequest | None
    final_result: ExecutionResult | None
    final_verdict: Verdict | None
    attempts: int


_CompiledDebugGraph = CompiledStateGraph[DebugState, GraphContext, DebugState, DebugState]

_AfterVerify = Literal["patch", "end"]


# --------------------------------------------------------------------------
# Sandbox helpers (never execute anything themselves -- only build the
# request/degrade a runner failure into a safe, non-leaking ExecutionResult)
# --------------------------------------------------------------------------


def _sandbox_error_result(request: ExecutionRequest) -> ExecutionResult:
    return ExecutionResult(
        status="sandbox_error",
        language=request.language,
        error=HarnessError(type="DebuggerRunFailed", message=_SANDBOX_RUN_FAILED_MESSAGE),
    )


async def _run_in_sandbox(
    request: ExecutionRequest, runtime: Runtime[GraphContext]
) -> ExecutionResult:
    """Run `request` via `runtime.context.runner`, degrading any raised
    exception to a `sandbox_error` result rather than propagating it (which
    could otherwise leak raw exception text from an untrusted run)."""
    runner = runtime.context.runner
    if runner is None:
        return _sandbox_error_result(request)
    try:
        return await runner.run(request)
    except Exception:  # noqa: BLE001 - never leak the raw exception from an untrusted run
        return _sandbox_error_result(request)


# --------------------------------------------------------------------------
# Nodes
# --------------------------------------------------------------------------


async def _static_analysis(state: DebugState, runtime: Runtime[GraphContext]) -> DebugState:
    """Pure, LLM-free static findings for the learner's code (`ast`, tree-sitter fallback)."""
    del runtime
    return {"static_findings": static_analysis(state.get("code"))}


async def _infer_approach(state: DebugState, runtime: Runtime[GraphContext]) -> DebugState:
    """LLM call #1: what approach does the learner appear to be attempting?"""
    problem = state.get("problem")
    if problem is None or problem.is_empty:
        return {"inferred_approach": None}
    approach = await infer_approach(problem, runtime.context.llm)
    return {"inferred_approach": approach}


async def _run_tests(state: DebugState, runtime: Runtime[GraphContext]) -> DebugState:
    """Run the learner's own code once, unmodified, to establish ground truth."""
    code = state.get("code")
    if code is None:
        return {"request": None, "result": None, "initial_verdict": verify(None)}
    request = ExecutionRequest(code=code, tests=state.get("tests"))
    result = await _run_in_sandbox(request, runtime)
    return {"request": request, "result": result, "initial_verdict": verify(result, request)}


async def _find_failing_case(state: DebugState, runtime: Runtime[GraphContext]) -> DebugState:
    """Read the failing case straight from the `Verdict` -- never from the LLM."""
    del runtime
    verdict = state.get("initial_verdict")
    return {
        "failing_case": failing_case_summary(verdict),
        "has_established_failure": verdict is not None and verdict.status == "fail",
    }


async def _localize(state: DebugState, runtime: Runtime[GraphContext]) -> DebugState:
    """Combine static findings + the failing case's own diagnostics into a `BugLocation`."""
    del runtime
    location = localize_bug(state.get("static_findings", []), state.get("initial_verdict"))
    return {"bug_location": location}


async def _explain(state: DebugState, runtime: Runtime[GraphContext]) -> DebugState:
    """LLM call #2 (only when a failure is established): explain why it happens."""
    if not state.get("has_established_failure", False):
        return {"bug_explanation": None}
    problem = state.get("problem")
    if problem is None:
        return {"bug_explanation": None}
    explanation = await explain_bug(
        problem,
        static_findings=state.get("static_findings", []),
        failing_case=state.get("failing_case"),
        bug_location=state.get("bug_location"),
        inferred_approach=state.get("inferred_approach"),
        llm=runtime.context.llm,
    )
    return {"bug_explanation": explanation}


async def _patch(state: DebugState, runtime: Runtime[GraphContext]) -> DebugState:
    """LLM call #3 (repeated up to `MAX_PATCH_ATTEMPTS`): produce a candidate fix.

    Never itself decides correctness -- `re_run`/`verify_final` are the only
    source of truth for whether this attempt actually worked.
    """
    attempts = state.get("attempts", 0)
    if not state.get("has_established_failure", False):
        return {"patched_code": None}
    problem = state.get("problem")
    if problem is None:
        return {"patched_code": None, "attempts": attempts + 1}
    previous_verdict = state.get("final_verdict")
    patched = await patch_code(
        problem,
        static_findings=state.get("static_findings", []),
        failing_case=state.get("failing_case"),
        bug_location=state.get("bug_location"),
        bug_explanation=state.get("bug_explanation"),
        previous_patch=state.get("patched_code") if attempts > 0 else None,
        previous_failure=previous_verdict.summary if attempts > 0 and previous_verdict else None,
        llm=runtime.context.llm,
    )
    return {"patched_code": patched, "attempts": attempts + 1}


async def _re_run(state: DebugState, runtime: Runtime[GraphContext]) -> DebugState:
    """Every patch must be re-verified in the sandbox: run the patched code, unconditionally."""
    patched = state.get("patched_code")
    if patched is None:
        return {"final_request": None, "final_result": None}
    request = ExecutionRequest(code=patched, tests=state.get("tests"))
    result = await _run_in_sandbox(request, runtime)
    return {"final_request": request, "final_result": result}


async def _verify_final(state: DebugState, runtime: Runtime[GraphContext]) -> DebugState:
    """Derive this cycle's authoritative `Verdict` from the actual re-run -- never from
    the LLM's own claim about the patch."""
    del runtime
    return {"final_verdict": verify(state.get("final_result"), state.get("final_request"))}


def _after_verify(state: DebugState) -> _AfterVerify:
    """Bounded loop guard: retry `patch` only while there is an established failure
    still unresolved and the attempt cap has not been reached -- impossible to exceed
    `MAX_PATCH_ATTEMPTS` patch calls."""
    if not state.get("has_established_failure", False):
        return "end"
    verdict = state.get("final_verdict")
    if verdict is not None and verdict.status == "pass":
        return "end"
    if state.get("attempts", 0) >= MAX_PATCH_ATTEMPTS:
        return "end"
    return "patch"


# --------------------------------------------------------------------------
# Graph assembly
# --------------------------------------------------------------------------


def build_debug_graph() -> _CompiledDebugGraph:
    """Build and compile a fresh debugger subgraph."""
    builder = StateGraph(DebugState, context_schema=GraphContext)
    builder.add_node("static_analysis", _static_analysis)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("infer_approach", _infer_approach)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("run_tests", _run_tests)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("find_failing_case", _find_failing_case)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("localize", _localize)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("explain", _explain)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("patch", _patch)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("re_run", _re_run)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("verify_final", _verify_final)  # pyright: ignore[reportUnknownMemberType]

    builder.add_edge(START, "static_analysis")
    builder.add_edge("static_analysis", "infer_approach")
    builder.add_edge("infer_approach", "run_tests")
    builder.add_edge("run_tests", "find_failing_case")
    builder.add_edge("find_failing_case", "localize")
    builder.add_edge("localize", "explain")
    builder.add_edge("explain", "patch")
    builder.add_edge("patch", "re_run")
    builder.add_edge("re_run", "verify_final")
    builder.add_conditional_edges(  # pyright: ignore[reportUnknownMemberType]
        "verify_final", _after_verify, {"patch": "patch", "end": END}
    )

    return builder.compile()  # pyright: ignore[reportUnknownMemberType]


@cache
def get_debug_graph() -> _CompiledDebugGraph:
    """The compiled debugger subgraph, built once and cached for reuse.

    Stateless (no checkpointer, no module-level mutable state), so sharing
    this one instance across concurrent runs is safe -- mirrors
    `app.graph.subgraphs.dsa.get_dsa_graph`.
    """
    return build_debug_graph()


# --------------------------------------------------------------------------
# AgentState <-> DebugState boundary
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DebugRunResult:
    """The outcome of one `run_debug` call.

    `execution_request` is the final (patched, if any) request -- P7's
    `debug_agent` node body puts it on `AgentState.execution_request` so the
    outer `execute_code -> verify` edges (Phase 06) re-run it and produce the
    turn's authoritative `Verdict`. That deliberate double-run (once here to
    establish/patch the bug, again in the outer graph) is an accepted
    architecture decision.
    """

    result: DebugResult
    execution_request: ExecutionRequest | None


def _is_fixed(verdict: Verdict | None) -> bool:
    return verdict is not None and verdict.status == "pass"


async def run_debug(state: AgentState, runtime: Runtime[GraphContext]) -> DebugRunResult:
    """Run the debugger subgraph for this turn and map the result back.

    `runtime.context.runner is None` (sandbox disabled/unavailable) short-circuits
    before the subgraph -- and therefore before any LLM call -- to a
    `DebugResult` whose `initial_verdict`/`final_verdict` are both the
    verifier's own "skipped" output and `fixed=False`: never raises, never
    claims a fix, never spends the run's LLM budget on an unverifiable guess.
    """
    if runtime.context.runner is None:
        skipped = verify(None)
        return DebugRunResult(
            result=DebugResult(initial_verdict=skipped, final_verdict=skipped, fixed=False),
            execution_request=None,
        )

    problem = state.structured_input
    available_tests = state.execution_request.tests if state.execution_request is not None else None
    initial: DebugState = {
        "problem": problem,
        "code": extract_learner_code(problem),
        "tests": available_tests,
        "attempts": 0,
    }
    final_state = await get_debug_graph().ainvoke(  # pyright: ignore[reportUnknownMemberType]
        initial, context=runtime.context
    )

    final_verdict = final_state.get("final_verdict")
    result = DebugResult(
        static_findings=final_state.get("static_findings") or [],
        inferred_approach=final_state.get("inferred_approach"),
        failing_case=final_state.get("failing_case"),
        bug_explanation=final_state.get("bug_explanation"),
        bug_location=final_state.get("bug_location"),
        patched_code=final_state.get("patched_code"),
        attempts=final_state.get("attempts", 0),
        initial_verdict=final_state.get("initial_verdict"),
        final_verdict=final_verdict,
        fixed=_is_fixed(final_verdict),
    )
    execution_request = final_state.get("final_request") or final_state.get("request")
    return DebugRunResult(result=result, execution_request=execution_request)
