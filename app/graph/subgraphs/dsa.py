"""The DSA solver subgraph: hint ladder + level-gated problem analysis.

`DSAState` is this subgraph's own **private** scratch state -- never
`AgentState`, which is frozen with `extra="forbid"` and must not grow
scratch fields for one capability. `run_dsa` is the clean boundary the outer
graph (Phase 07 P7's `dsa_agent` node) calls: it maps `AgentState` onto a
fresh `DSAState`, runs the compiled subgraph once, and maps the finished
`DSAState` back onto a `DSAResult` (plus, when this turn produced an L6 full
solution, the `ExecutionRequest` to hand to `execute_code`/`verify`).

Pipeline: `understand -> constraints -> pattern -> brute_force -> why_slow
-> key_insight -> hint`. Exactly one LLM call happens, in `understand`
(`app.agents.dsa_solver.analyze_dsa_problem`), to stay within the graph
run's LLM call budget (`BudgetedLLMClient`); every other content node is a
small, single-purpose extraction of its own already-level-gated field(s)
from that one parsed `DSAAnalysis` -- never a second call. `hint` is the
only node that touches `app.agents.hint_engine.next_hint` (pure, LLM-free);
it is safe to call it twice in one run (once, in `understand`, purely to
determine this turn's gating level; again here for the attached result)
because it is a deterministic, side-effect-free function of unchanged
inputs.

`state["problem"]` (sourced from `AgentState.structured_input`) is untrusted
learner content, exactly as documented in `app.graph.nodes`'s module
docstring; it is only ever handed to the LLM as clearly-delimited data to
reason about (see `app.agents.dsa_solver`'s module docstring), never echoed
into `DSAResult`'s free-text fields.
"""

from dataclasses import dataclass
from functools import cache
from typing import Final, TypedDict

from langgraph.graph import END, START, StateGraph  # pyright: ignore[reportMissingTypeStubs]
from langgraph.graph.state import (  # pyright: ignore[reportMissingTypeStubs]
    CompiledStateGraph,
)
from langgraph.runtime import Runtime

from app.agents.dsa_solver import DSAAnalysis, analyze_dsa_problem, build_execution_request
from app.agents.hint_engine import HintProgress, next_hint
from app.graph.state import AgentState, GraphContext
from app.schemas.agent_results import DSAResult, HintLevel, HintResult
from app.schemas.execution import ExecutionRequest
from app.schemas.input import StructuredInput
from app.schemas.knowledge import RetrievalHit
from app.schemas.plan import TeachingPlan

__all__ = [
    "DSARunResult",
    "DSAState",
    "build_dsa_graph",
    "get_dsa_graph",
    "run_dsa",
]


class DSAState(TypedDict, total=False):
    """Private scratch state threaded through the DSA solver subgraph only.

    `problem`/`plan`/`context`/`progress` are this run's inputs (set once by
    `run_dsa` and never rewritten by any node); every other field is a stage
    output, populated incrementally as the pipeline runs. `analysis` holds
    the one parsed, level-masked `DSAAnalysis` from `understand`'s single LLM
    call -- every later content node reads its own field(s) from it rather
    than calling the LLM again.
    """

    problem: StructuredInput | None
    plan: TeachingPlan | None
    context: list[RetrievalHit]
    progress: HintProgress

    hint_level: HintLevel | None
    analysis: DSAAnalysis | None

    understanding: str | None
    constraints: list[str]
    topic: str | None
    pattern: str | None
    common_mistakes: list[str]
    brute_force: str | None
    why_slow: str | None
    key_insight: str | None
    pseudocode: str | None
    complexity_time: str | None
    complexity_space: str | None
    code: str | None
    hint: HintResult | None


_CompiledDSAGraph = CompiledStateGraph[DSAState, GraphContext, DSAState, DSAState]

_DEFAULT_PROGRESS: Final = HintProgress()


# --------------------------------------------------------------------------
# Nodes
# --------------------------------------------------------------------------


async def _understand(state: DSAState, runtime: Runtime[GraphContext]) -> DSAState:
    """Determine this turn's gating hint level, then run the one LLM call.

    Both a missing plan and `progress.solved` leave `hint_level`/`analysis`
    at `None`: no plan means no ceiling to gate against, and a solved
    problem means this turn has nothing further to teach.
    """
    plan = state.get("plan")
    if plan is None:
        return {"hint_level": None, "analysis": None}

    progress = state.get("progress", _DEFAULT_PROGRESS)
    context = state.get("context", [])
    preview = next_hint(None, plan, progress, context=context)
    if preview is None:
        return {"hint_level": None, "analysis": None}

    level = preview.level
    problem = state.get("problem")
    if problem is None or problem.is_empty:
        return {"hint_level": level, "analysis": None, "understanding": None}

    analysis = await analyze_dsa_problem(problem, plan, level, context, runtime.context.llm)
    return {"hint_level": level, "analysis": analysis, "understanding": analysis.understanding}


async def _constraints(state: DSAState, runtime: Runtime[GraphContext]) -> DSAState:
    """Surface the (already level-gated) constraints and common-mistakes fields."""
    del runtime
    analysis = state.get("analysis")
    if analysis is None:
        return {}
    return {"constraints": analysis.constraints, "common_mistakes": analysis.common_mistakes}


async def _pattern(state: DSAState, runtime: Runtime[GraphContext]) -> DSAState:
    """Surface the (already level-gated) topic/pattern tags, drawn from `retrieved_context`."""
    del runtime
    analysis = state.get("analysis")
    if analysis is None:
        return {}
    return {"topic": analysis.topic, "pattern": analysis.pattern}


async def _brute_force(state: DSAState, runtime: Runtime[GraphContext]) -> DSAState:
    """Surface the (already level-gated) brute-force description."""
    del runtime
    analysis = state.get("analysis")
    if analysis is None:
        return {}
    return {"brute_force": analysis.brute_force}


async def _why_slow(state: DSAState, runtime: Runtime[GraphContext]) -> DSAState:
    """Surface the (already level-gated) explanation of why the brute force is slow."""
    del runtime
    analysis = state.get("analysis")
    if analysis is None:
        return {}
    return {"why_slow": analysis.why_slow}


async def _key_insight(state: DSAState, runtime: Runtime[GraphContext]) -> DSAState:
    """Surface the (already level-gated) solution-shape fields: insight through code.

    Grouped in one node because they form a single "reveal the efficient
    solution" tier (`key_insight` at L3, `pseudocode`/complexity at L4,
    `code` only at L6) -- `analysis` already enforces every one of those
    gates, so this node's only job is to copy the fields across.
    """
    del runtime
    analysis = state.get("analysis")
    if analysis is None:
        return {}
    return {
        "key_insight": analysis.key_insight,
        "pseudocode": analysis.pseudocode,
        "complexity_time": analysis.complexity_time,
        "complexity_space": analysis.complexity_space,
        "code": analysis.code,
    }


async def _hint(state: DSAState, runtime: Runtime[GraphContext]) -> DSAState:
    """Compute and attach this turn's authoritative `HintResult`.

    Recomputes `next_hint` (rather than reusing `understand`'s preview
    level) so this node stands on its own as the pipeline's one authoritative
    hint-ladder step; `next_hint` is pure, so this is guaranteed to reproduce
    the same level `understand` gated `analysis` against.
    """
    del runtime
    plan = state.get("plan")
    if plan is None:
        return {"hint": None}
    progress = state.get("progress", _DEFAULT_PROGRESS)
    context = state.get("context", [])
    return {"hint": next_hint(None, plan, progress, context=context)}


# --------------------------------------------------------------------------
# Graph assembly
# --------------------------------------------------------------------------


def build_dsa_graph() -> _CompiledDSAGraph:
    """Build and compile a fresh DSA solver subgraph."""
    builder = StateGraph(DSAState, context_schema=GraphContext)
    builder.add_node("understand", _understand)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("constraints", _constraints)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("pattern", _pattern)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("brute_force", _brute_force)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("why_slow", _why_slow)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("key_insight", _key_insight)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("hint", _hint)  # pyright: ignore[reportUnknownMemberType]

    builder.add_edge(START, "understand")
    builder.add_edge("understand", "constraints")
    builder.add_edge("constraints", "pattern")
    builder.add_edge("pattern", "brute_force")
    builder.add_edge("brute_force", "why_slow")
    builder.add_edge("why_slow", "key_insight")
    builder.add_edge("key_insight", "hint")
    builder.add_edge("hint", END)

    return builder.compile()  # pyright: ignore[reportUnknownMemberType]


@cache
def get_dsa_graph() -> _CompiledDSAGraph:
    """The compiled DSA solver subgraph, built once and cached for reuse.

    Stateless (no checkpointer, no module-level mutable state), so sharing
    this one instance across concurrent runs is safe -- mirrors
    `app.graph.build.get_graph`.
    """
    return build_dsa_graph()


# --------------------------------------------------------------------------
# AgentState <-> DSAState boundary
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DSARunResult:
    """The outcome of one `run_dsa` call.

    `execution_request` is only ever set alongside `result.code` (an L6 full
    solution) -- P7's `dsa_agent` node body puts it on
    `AgentState.execution_request` so the outer `execute_code -> verify`
    edges (already wired, Phase 06) run it in the sandbox and produce the
    authoritative `Verdict`. This module never runs it itself.
    """

    result: DSAResult
    execution_request: ExecutionRequest | None


async def run_dsa(
    state: AgentState,
    runtime: Runtime[GraphContext],
    *,
    progress: HintProgress = _DEFAULT_PROGRESS,
) -> DSARunResult:
    """Run the DSA solver subgraph for this turn and map the result back.

    `progress` defaults to a fresh ladder (`last_level=None`): `AgentState`
    does not yet carry a persisted hint-progress field across turns (no
    packet in this phase adds one), so today every call starts the ladder
    over. Passing an explicit `progress` (e.g. from a caller that tracks it
    elsewhere) lets a turn resume mid-ladder without changing this
    function's primary two-argument shape that `dsa_agent` calls.
    """
    initial: DSAState = {
        "problem": state.structured_input,
        "plan": state.plan,
        "context": list(state.retrieved_context),
        "progress": progress,
    }
    final_state = await get_dsa_graph().ainvoke(  # pyright: ignore[reportUnknownMemberType]
        initial, context=runtime.context
    )

    citations = [hit.chunk.id for hit in state.retrieved_context]
    result = DSAResult(
        topic=final_state.get("topic"),
        pattern=final_state.get("pattern"),
        hint=final_state.get("hint"),
        understanding=final_state.get("understanding"),
        constraints=final_state.get("constraints") or [],
        brute_force=final_state.get("brute_force"),
        why_slow=final_state.get("why_slow"),
        key_insight=final_state.get("key_insight"),
        pseudocode=final_state.get("pseudocode"),
        code=final_state.get("code"),
        complexity_time=final_state.get("complexity_time"),
        complexity_space=final_state.get("complexity_space"),
        common_mistakes=final_state.get("common_mistakes") or [],
        citations=citations,
    )
    execution_request = build_execution_request(result.code) if result.code else None
    return DSARunResult(result=result, execution_request=execution_request)
