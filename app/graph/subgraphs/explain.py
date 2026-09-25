"""The code-explainer subgraph: structure outline + line explanations + complexity.

`ExplainState` is this subgraph's own **private** scratch state -- never
`AgentState`, which is frozen with `extra="forbid"` and must not grow scratch
fields for one capability (mirrors `app.graph.subgraphs.dsa.DSAState` and
`app.graph.subgraphs.debug.DebugState`). `run_explain` is the clean boundary
the outer graph (Phase 07 P7's `explain_agent` node) calls: it maps
`AgentState` onto a fresh `ExplainState`, runs the compiled subgraph once,
and maps the finished `ExplainState` back onto an `ExplainResult`.

Pipeline: `parse_structure -> line_explanations -> complexity`.
`parse_structure` is pure (`app.agents.explainer.build_structure`, no LLM).
`line_explanations` is this run's first LLM call
(`app.agents.explainer.generate_line_explanations`). `complexity` first
computes a deterministic Big-O estimate (`app.agents.explainer.
estimate_complexity`, never the LLM) and then makes this run's second (and
last) LLM call purely for plain-language rationale prose
(`app.agents.explainer.generate_complexity_rationale`), which is not allowed
to contradict the already-fixed estimate. That keeps this subgraph to at
most two LLM calls per run.

**Never executes code.** This subgraph only ever parses the learner's code
(`ast`/tree-sitter, inside `app.agents.explainer`); it has no execution path
at all, so `run_explain` always returns `execution_request=None`.

`state["problem"]`/`state["code"]` (sourced from `AgentState.structured_input`
via `app.agents.debugger.extract_learner_code`) are untrusted learner
content, exactly as documented in `app.graph.nodes`'s module docstring; they
are only ever handed to the LLM as clearly-delimited data to analyze, never
echoed into `ExplainResult`'s narrative fields.
"""

from dataclasses import dataclass
from functools import cache
from typing import TypedDict

from langgraph.graph import END, START, StateGraph  # pyright: ignore[reportMissingTypeStubs]
from langgraph.graph.state import (  # pyright: ignore[reportMissingTypeStubs]
    CompiledStateGraph,
)
from langgraph.runtime import Runtime

from app.agents.debugger import extract_learner_code
from app.agents.explainer import (
    build_structure,
    estimate_complexity,
    generate_complexity_rationale,
    generate_line_explanations,
)
from app.graph.state import AgentState, GraphContext
from app.schemas.agent_results import CodeStructureNode, ExplainResult, LineExplanation
from app.schemas.execution import ExecutionRequest
from app.schemas.input import StructuredInput

__all__ = [
    "ExplainRunResult",
    "ExplainState",
    "build_explain_graph",
    "get_explain_graph",
    "run_explain",
]


class ExplainState(TypedDict, total=False):
    """Private scratch state threaded through the explainer subgraph only.

    `problem`/`code` are this run's inputs (set once by `run_explain` and
    never rewritten by any node); every other field is a stage output.
    """

    problem: StructuredInput | None
    code: str | None

    structure: CodeStructureNode | None
    line_explanations: list[LineExplanation]
    complexity_time: str | None
    complexity_space: str | None
    complexity_rationale: str | None


_CompiledExplainGraph = CompiledStateGraph[ExplainState, GraphContext, ExplainState, ExplainState]


# --------------------------------------------------------------------------
# Nodes
# --------------------------------------------------------------------------


async def _parse_structure(state: ExplainState, runtime: Runtime[GraphContext]) -> ExplainState:
    """Pure, LLM-free structural outline of the learner's code (`ast`, tree-sitter fallback)."""
    del runtime
    return {"structure": build_structure(state.get("code"))}


async def _line_explanations(state: ExplainState, runtime: Runtime[GraphContext]) -> ExplainState:
    """LLM call #1: a short explanation per meaningful source line."""
    code = state.get("code")
    if not code:
        return {"line_explanations": []}
    explanations = await generate_line_explanations(code, runtime.context.llm)
    return {"line_explanations": explanations}


async def _complexity(state: ExplainState, runtime: Runtime[GraphContext]) -> ExplainState:
    """Deterministic Big-O estimate first, then LLM call #2 for rationale prose only."""
    code = state.get("code")
    estimate = estimate_complexity(code)
    if not code:
        return {
            "complexity_time": estimate.time,
            "complexity_space": estimate.space,
            "complexity_rationale": None,
        }
    rationale = await generate_complexity_rationale(
        code, time=estimate.time, space=estimate.space, llm=runtime.context.llm
    )
    return {
        "complexity_time": estimate.time,
        "complexity_space": estimate.space,
        "complexity_rationale": rationale,
    }


# --------------------------------------------------------------------------
# Graph assembly
# --------------------------------------------------------------------------


def build_explain_graph() -> _CompiledExplainGraph:
    """Build and compile a fresh explainer subgraph."""
    builder = StateGraph(ExplainState, context_schema=GraphContext)
    builder.add_node("parse_structure", _parse_structure)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("line_explanations", _line_explanations)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("complexity", _complexity)  # pyright: ignore[reportUnknownMemberType]

    builder.add_edge(START, "parse_structure")
    builder.add_edge("parse_structure", "line_explanations")
    builder.add_edge("line_explanations", "complexity")
    builder.add_edge("complexity", END)

    return builder.compile()  # pyright: ignore[reportUnknownMemberType]


@cache
def get_explain_graph() -> _CompiledExplainGraph:
    """The compiled explainer subgraph, built once and cached for reuse.

    Stateless (no checkpointer, no module-level mutable state), so sharing
    this one instance across concurrent runs is safe -- mirrors
    `app.graph.subgraphs.dsa.get_dsa_graph`/`app.graph.subgraphs.debug.get_debug_graph`.
    """
    return build_explain_graph()


# --------------------------------------------------------------------------
# AgentState <-> ExplainState boundary
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExplainRunResult:
    """The outcome of one `run_explain` call.

    `execution_request` is always `None`: explaining code runs nothing, so
    there is nothing for the outer `execute_code -> verify` edges to do for
    this turn.
    """

    result: ExplainResult
    execution_request: ExecutionRequest | None


async def run_explain(state: AgentState, runtime: Runtime[GraphContext]) -> ExplainRunResult:
    """Run the explainer subgraph for this turn and map the result back."""
    problem = state.structured_input
    initial: ExplainState = {
        "problem": problem,
        "code": extract_learner_code(problem),
    }
    final_state = await get_explain_graph().ainvoke(  # pyright: ignore[reportUnknownMemberType]
        initial, context=runtime.context
    )

    result = ExplainResult(
        structure=final_state.get("structure"),
        line_explanations=final_state.get("line_explanations") or [],
        complexity_time=final_state.get("complexity_time"),
        complexity_space=final_state.get("complexity_space"),
        complexity_rationale=final_state.get("complexity_rationale"),
    )
    return ExplainRunResult(result=result, execution_request=None)
