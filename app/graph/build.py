"""Assembles and runs the Phase 04-06 teaching graph.

`build_graph` wires the pipeline/agent-stub nodes from `app.graph.nodes` (the
Phase 04 pipeline, Phase 05's `retrieve_knowledge`, and Phase 06's
`execute_code`/`verify`) into a single `StateGraph`, wrapping every node in
`safe_node` so an unexpected exception degrades to that node's fallback plus
a recorded `NodeError` instead of failing the whole run. `run_graph` is the
single entry point callers (the `/chat` route, tests here) use to execute one
turn end-to-end.

The edge names below (`dsa_agent` / `debug_agent` / `explain_agent` /
`clarify`, wired via `app.graph.routing.ROUTE_NODES`) are a **stable
contract**: Phase 07 replaces those nodes' implementations with full
subgraphs without renaming them or reshaping the surrounding edges. The three
specialized-agent nodes now flow through `execute_code` -> `verify` (wired via
`app.graph.routing.VERIFY_NODES`) before reaching `final_response`; `clarify`
skips straight to `final_response` since it never runs code.
"""

from collections.abc import Hashable, Mapping
from dataclasses import dataclass
from functools import cache
from types import MappingProxyType
from typing import Final
from uuid import UUID

from langgraph.graph import END, START, StateGraph  # pyright: ignore[reportMissingTypeStubs]
from langgraph.graph.state import (  # pyright: ignore[reportMissingTypeStubs]
    CompiledStateGraph,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.base import CodeRunner
from app.graph.nodes import (
    FALLBACKS,
    Node,
    clarify,
    classify_intent,
    debug_agent,
    dsa_agent,
    execute_code,
    explain_agent,
    final_response,
    load_learner_profile,
    plan_teaching,
    retrieve_knowledge,
    route,
    safe_node,
    understand_input,
    update_learner_model,
    verify_execution,
)
from app.graph.routing import ROUTE_NODES, VERIFY_NODES, route_after, verify_after
from app.graph.state import AgentState, GraphContext, RawInput
from app.knowledge.base import DEFAULT_KNOWLEDGE_TOP_K, Retriever
from app.llm.base import LLMClient
from app.llm.budget import DEFAULT_MAX_LLM_CALLS, BudgetedLLMClient

__all__ = [
    "NODE_FUNCTIONS",
    "RECURSION_LIMIT",
    "GraphRunResult",
    "build_graph",
    "get_graph",
    "run_graph",
]

# The graph is a DAG with ~8 steps per turn; this is a guard against an
# accidental cycle, not a real limit on how deep a run should go.
RECURSION_LIMIT: Final = 20

NODE_FUNCTIONS: Final[Mapping[str, Node]] = MappingProxyType(
    {
        "understand_input": understand_input,
        "classify_intent": classify_intent,
        "load_learner_profile": load_learner_profile,
        "plan_teaching": plan_teaching,
        "retrieve_knowledge": retrieve_knowledge,
        "route": route,
        "dsa_agent": dsa_agent,
        "debug_agent": debug_agent,
        "explain_agent": explain_agent,
        "execute_code": execute_code,
        "verify": verify_execution,
        "clarify": clarify,
        "final_response": final_response,
        "update_learner_model": update_learner_model,
    }
)

_CompiledGraph = CompiledStateGraph[AgentState, GraphContext, AgentState, AgentState]


def build_graph(node_overrides: Mapping[str, Node] | None = None) -> _CompiledGraph:
    """Build and compile a fresh teaching graph.

    `node_overrides` lets tests substitute a node function (e.g. to force a
    node to fail) without mutating the shared `NODE_FUNCTIONS` mapping or
    polluting the cached `get_graph()` graph.
    """
    functions: dict[str, Node] = dict(NODE_FUNCTIONS)
    if node_overrides:
        functions.update(node_overrides)

    if set(functions) != set(FALLBACKS):
        raise RuntimeError("graph node functions and FALLBACKS must declare the same node names")

    builder = StateGraph(AgentState, context_schema=GraphContext)
    for name, fn in functions.items():
        builder.add_node(name, safe_node(name, fn, FALLBACKS[name]))  # pyright: ignore[reportUnknownMemberType]

    builder.add_edge(START, "understand_input")
    builder.add_edge("understand_input", "classify_intent")
    builder.add_edge("classify_intent", "load_learner_profile")
    builder.add_edge("load_learner_profile", "plan_teaching")
    builder.add_edge("plan_teaching", "retrieve_knowledge")
    builder.add_edge("retrieve_knowledge", "route")
    # `dict(ROUTE_NODES)` types as `dict[RouteKey, str]`, which pyright treats
    # as an invariant mismatch against `add_conditional_edges`'s
    # `dict[Hashable, str]` param; a comprehension lets bidirectional
    # inference retarget the key type instead.
    path_map: dict[Hashable, str] = {key: value for key, value in ROUTE_NODES.items()}
    builder.add_conditional_edges("route", route_after, path_map)
    # Every route node runs any code it produced through the sandbox +
    # verifier before responding, except `clarify` (it never runs code) which
    # goes straight to `final_response`. Derived from `ROUTE_NODES` rather
    # than hard-coded so a future route addition can't silently bypass
    # execution/verification by omission.
    clarify_node = ROUTE_NODES["clarify"]
    for agent_node in ROUTE_NODES.values():
        if agent_node != clarify_node:
            builder.add_edge(agent_node, "execute_code")
    builder.add_edge(clarify_node, "final_response")
    builder.add_edge("execute_code", "verify")
    verify_path_map: dict[Hashable, str] = {key: value for key, value in VERIFY_NODES.items()}
    builder.add_conditional_edges("verify", verify_after, verify_path_map)
    builder.add_edge("final_response", "update_learner_model")
    builder.add_edge("update_learner_model", END)

    return builder.compile()  # pyright: ignore[reportUnknownMemberType]


@cache
def get_graph() -> _CompiledGraph:
    """The compiled teaching graph, built once and cached for reuse.

    The compiled graph is stateless (no checkpointer, no module-level mutable
    state), so sharing this one instance across concurrent runs is safe.
    """
    return build_graph()


@dataclass(frozen=True, slots=True)
class GraphRunResult:
    """The outcome of a single `run_graph` call."""

    state: AgentState
    llm_calls: int


async def run_graph(
    raw: RawInput,
    *,
    llm: LLMClient,
    session: AsyncSession | None = None,
    user_id: UUID | None = None,
    conversation_id: UUID | None = None,
    max_llm_calls: int = DEFAULT_MAX_LLM_CALLS,
    retriever: Retriever | None = None,
    knowledge_top_k: int = DEFAULT_KNOWLEDGE_TOP_K,
    runner: CodeRunner | None = None,
) -> GraphRunResult:
    """Run the teaching graph once, for a single turn.

    `llm` is wrapped in a fresh, per-run `BudgetedLLMClient` so this run can
    never make more than `max_llm_calls` LLM calls in total, no matter how
    many nodes end up needing it. Never commits `session` -- the caller owns
    the transaction. `retriever` is entirely separate from the LLM budget:
    knowledge retrieval runs on local models, not the LLM provider. `runner`
    is `None` whenever the sandbox is disabled or unavailable (see
    `app.execution.runner.build_sandbox_runner`) -- `execute_code` degrades
    to a `sandbox_error` result rather than failing the run.
    """
    budgeted = BudgetedLLMClient(llm, max_llm_calls)
    context = GraphContext(
        llm=budgeted,
        session=session,
        user_id=user_id,
        conversation_id=conversation_id,
        retriever=retriever,
        knowledge_top_k=knowledge_top_k,
        runner=runner,
    )
    result = await get_graph().ainvoke(  # pyright: ignore[reportUnknownMemberType]
        AgentState(input=raw),
        config={"recursion_limit": RECURSION_LIMIT, "run_name": "teaching_graph"},
        context=context,
    )
    state = AgentState.model_validate(result)
    return GraphRunResult(state=state, llm_calls=budgeted.calls)
