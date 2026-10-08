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

import asyncio
import contextlib
from collections.abc import AsyncGenerator, Hashable, Mapping
from dataclasses import dataclass
from functools import cache
from types import MappingProxyType
from typing import Final, cast
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
    decide_turn,
    dsa_agent,
    execute_code,
    explain_agent,
    final_response,
    grade_answer,
    load_learner_profile,
    plan_teaching,
    practice_agent,
    retrieve_knowledge,
    safe_node,
    understand_input,
    update_learner_model,
    verify_execution,
)
from app.graph.routing import (
    GRADE_AFTER_NODES,
    ROUTE_NODES,
    VERIFY_NODES,
    grade_after,
    route_after,
    verify_after,
)
from app.graph.stages import DEFAULT_STAGE_LABEL, STAGE_LABELS
from app.graph.state import AgentState, GraphContext, RawInput
from app.knowledge.base import DEFAULT_KNOWLEDGE_TOP_K, Retriever
from app.llm.base import LLMClient
from app.llm.budget import DEFAULT_MAX_LLM_CALLS, BudgetedLLMClient
from app.llm.client import Tracer

__all__ = [
    "NODE_FUNCTIONS",
    "RECURSION_LIMIT",
    "GraphResultEvent",
    "GraphRunResult",
    "GraphStageEvent",
    "GraphStreamEvent",
    "build_graph",
    "get_graph",
    "run_graph",
    "stream_graph",
]

# The graph is a DAG with ~8 steps per turn; this is a guard against an
# accidental cycle, not a real limit on how deep a run should go.
RECURSION_LIMIT: Final = 20

NODE_FUNCTIONS: Final[Mapping[str, Node]] = MappingProxyType(
    {
        "understand_input": understand_input,
        "classify_intent": classify_intent,
        "load_learner_profile": load_learner_profile,
        "decide_turn": decide_turn,
        "plan_teaching": plan_teaching,
        "retrieve_knowledge": retrieve_knowledge,
        "dsa_agent": dsa_agent,
        "debug_agent": debug_agent,
        "explain_agent": explain_agent,
        "practice_agent": practice_agent,
        "grade_answer": grade_answer,
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
    # The conversation is loaded BEFORE classification (A-08): the classifier
    # is shown the active subject, the pending question and the last messages,
    # so a follow-up is read as a follow-up instead of as a first message.
    builder.add_edge("understand_input", "load_learner_profile")
    builder.add_edge("load_learner_profile", "classify_intent")
    # ONE decision per turn, made as soon as the message has been read against
    # the conversation: what the turn is about, what the learner showed, what
    # to do. Retrieval, the planner and the agents all read it.
    builder.add_edge("classify_intent", "decide_turn")
    builder.add_edge("decide_turn", "retrieve_knowledge")
    builder.add_edge("retrieve_knowledge", "plan_teaching")
    # `dict(ROUTE_NODES)` types as `dict[RouteKey, str]`, which pyright treats
    # as an invariant mismatch against `add_conditional_edges`'s
    # `dict[Hashable, str]` param; a comprehension lets bidirectional
    # inference retarget the key type instead.
    path_map: dict[Hashable, str] = {key: value for key, value in ROUTE_NODES.items()}
    builder.add_conditional_edges("plan_teaching", route_after, path_map)
    # Every route node runs any code it produced through the sandbox +
    # verifier before responding, except `clarify` (it never runs code) which
    # goes straight to `final_response`. Derived from `ROUTE_NODES` rather
    # than hard-coded so a future route addition can't silently bypass
    # execution/verification by omission.
    clarify_node = ROUTE_NODES["clarify"]
    grade_node = ROUTE_NODES["grade"]
    for agent_node in ROUTE_NODES.values():
        if agent_node not in (clarify_node, grade_node):
            builder.add_edge(agent_node, "execute_code")
    builder.add_edge(clarify_node, "final_response")
    # ADAPTIVE-tutoring: a graded reply is answered directly (it runs no code),
    # except a "don't know" on a problem, which hands the next rung to the DSA
    # agent -- and through it to execute/verify like any other DSA turn.
    grade_path_map: dict[Hashable, str] = {key: value for key, value in GRADE_AFTER_NODES.items()}
    builder.add_conditional_edges(grade_node, grade_after, grade_path_map)
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


def _build_run_context(
    llm: LLMClient,
    *,
    session: AsyncSession | None,
    user_id: UUID | None,
    conversation_id: UUID | None,
    max_llm_calls: int,
    retriever: Retriever | None,
    knowledge_top_k: int,
    runner: CodeRunner | None,
) -> tuple[BudgetedLLMClient, GraphContext]:
    """Build the `BudgetedLLMClient` + `GraphContext` pair shared by `run_graph`
    and `stream_graph`, so the two entry points cannot drift apart."""
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
    return budgeted, context


def _trace_inputs(
    raw: RawInput,
    *,
    user_id: UUID | None,
    conversation_id: UUID | None,
    max_llm_calls: int,
) -> dict[str, object]:
    """Metadata-only inputs for the `teaching_graph` parent run.

    Never the learner's text/code/image bytes themselves -- only ids, flags,
    and enum-ish values (see `app.llm.client.Tracer.run`'s contract).
    """
    return {
        "has_text": bool(raw.text and raw.text.strip()),
        "has_image": raw.image is not None,
        "language": raw.language,
        "user_id": str(user_id) if user_id is not None else None,
        "conversation_id": str(conversation_id) if conversation_id is not None else None,
        "max_llm_calls": max_llm_calls,
    }


def _trace_outputs(state: AgentState, budgeted: BudgetedLLMClient) -> dict[str, object]:
    """Metadata-only outputs for the `teaching_graph` parent run: counts and
    enum-ish values, never the generated response or any retrieved text."""
    return {
        "route": state.route,
        "intent": state.intent.intent.value if state.intent is not None else None,
        "topic": state.plan.topic if state.plan is not None else None,
        "verification_status": (
            state.verification.status if state.verification is not None else None
        ),
        "error_count": len(state.errors),
        "event_count": len(state.events),
        "llm_calls": budgeted.calls,
        **budgeted.usage_summary(),
    }


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
    tracer: Tracer | None = None,
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

    `tracer`, when given, wraps the whole graph invocation in a single named
    LangSmith parent run (`teaching_graph`) so every node's own run (and any
    LLM call inside it) nests under one parent per turn instead of arriving
    as flat, parentless runs. `tracer=None` (the default) makes this a
    complete no-op -- identical to every caller/test that predates this
    parameter.
    """
    budgeted, context = _build_run_context(
        llm,
        session=session,
        user_id=user_id,
        conversation_id=conversation_id,
        max_llm_calls=max_llm_calls,
        retriever=retriever,
        knowledge_top_k=knowledge_top_k,
        runner=runner,
    )

    async def _invoke_graph() -> AgentState:
        result = await get_graph().ainvoke(  # pyright: ignore[reportUnknownMemberType]
            AgentState(input=raw),
            config={"recursion_limit": RECURSION_LIMIT},
            context=context,
        )
        return AgentState.model_validate(result)

    if tracer is None:
        state = await _invoke_graph()
    else:
        state = await tracer.run(
            name="teaching_graph",
            run_type="chain",
            inputs=_trace_inputs(
                raw,
                user_id=user_id,
                conversation_id=conversation_id,
                max_llm_calls=max_llm_calls,
            ),
            fn=_invoke_graph,
            outputs=lambda result: _trace_outputs(result, budgeted),
            metadata=lambda: {"transport": "chat", **budgeted.usage_summary()},
        )

    return GraphRunResult(state=state, llm_calls=budgeted.calls)


@dataclass(frozen=True, slots=True)
class GraphStageEvent:
    """One node's arrival, emitted while `stream_graph` is still running."""

    node: str
    label: str


@dataclass(frozen=True, slots=True)
class GraphResultEvent:
    """The terminal event `stream_graph` yields exactly once, at the end."""

    result: GraphRunResult


GraphStreamEvent = GraphStageEvent | GraphResultEvent


#: Nodes that always run but often do nothing: `execute_code` / `verify` are
#: no-ops when no agent produced a run request. Streaming their labels anyway
#: showed "Running your code in the sandbox" on turns where nothing ran (F9).
_CONDITIONAL_STAGES: Final[Mapping[str, str]] = MappingProxyType(
    {"execute_code": "execution_result", "verify": "verification"}
)


def _stage_did_work(node: str, payload: object) -> bool:
    """Whether `node` actually did its job this turn (only checked for the
    conditional stages; every other node always counts). Reads only whether
    the result field is present -- never its content."""
    field = _CONDITIONAL_STAGES.get(node)
    if field is None:
        return True
    if not isinstance(payload, Mapping):
        return False
    return cast("Mapping[str, object]", payload).get(field) is not None


async def stream_graph(
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
    tracer: Tracer | None = None,
) -> AsyncGenerator[GraphStreamEvent]:
    """Run the teaching graph once, yielding a `GraphStageEvent` as each node
    finishes and exactly one terminal `GraphResultEvent` at the end.

    Mirrors `run_graph`'s semantics exactly (same budgeted LLM client, same
    context, never commits `session`) but drives `get_graph().astream(...)`
    with `stream_mode=["updates", "values"]` instead of `ainvoke` so callers
    can surface progress. Node names from `"updates"` chunks are used only to
    build `GraphStageEvent`s (never their payloads, which may carry untrusted
    state) -- see `app.graph.stages` for the fixed label lookup callers are
    expected to pair this with.

    `tracer`, when given, wraps the run in the same single `teaching_graph`
    LangSmith parent run `run_graph` creates. `Tracer.run` takes a coroutine,
    and a LangSmith run tree lives in a contextvar, which an async generator
    cannot hold open across its yields: each `__anext__` runs in whatever
    context the consumer (Starlette's response loop) iterates from. So the
    graph runs in its OWN task, inside `tracer.run`, and hands events to this
    generator through a queue. The task copies the context once, at creation,
    so every node and LLM run nests under the parent. Events are forwarded as
    they arrive -- nothing is buffered until the end. If the consumer stops
    early (a client disconnect closes this generator), the task is cancelled.
    """
    budgeted, context = _build_run_context(
        llm,
        session=session,
        user_id=user_id,
        conversation_id=conversation_id,
        max_llm_calls=max_llm_calls,
        retriever=retriever,
        knowledge_top_k=knowledge_top_k,
        runner=runner,
    )

    queue: asyncio.Queue[GraphStreamEvent | BaseException | None] = asyncio.Queue()

    async def _drive_graph() -> AgentState:
        last_values: dict[str, object] | None = None
        stream = get_graph().astream(  # pyright: ignore[reportUnknownMemberType]
            AgentState(input=raw),
            config={"recursion_limit": RECURSION_LIMIT},
            context=context,
            stream_mode=["updates", "values"],
        )
        async for mode, chunk in stream:
            mode = cast("str", mode)
            if mode == "updates":
                update = cast("Mapping[str, object]", chunk)
                for node, payload in update.items():
                    if not _stage_did_work(node, payload):
                        continue
                    queue.put_nowait(
                        GraphStageEvent(
                            node=node, label=STAGE_LABELS.get(node, DEFAULT_STAGE_LABEL)
                        )
                    )
            elif mode == "values":
                last_values = cast("dict[str, object]", chunk)

        if last_values is None:
            raise RuntimeError("stream_graph: no 'values' chunk was ever emitted")
        return AgentState.model_validate(last_values)

    async def _produce() -> None:
        try:
            if tracer is None:
                state = await _drive_graph()
            else:
                state = await tracer.run(
                    name="teaching_graph",
                    run_type="chain",
                    inputs=_trace_inputs(
                        raw,
                        user_id=user_id,
                        conversation_id=conversation_id,
                        max_llm_calls=max_llm_calls,
                    ),
                    fn=_drive_graph,
                    outputs=lambda result: _trace_outputs(result, budgeted),
                    metadata=lambda: {"transport": "stream", **budgeted.usage_summary()},
                )
            queue.put_nowait(
                GraphResultEvent(GraphRunResult(state=state, llm_calls=budgeted.calls))
            )
        except BaseException as exc:  # noqa: BLE001 - re-raised in the consumer below
            queue.put_nowait(exc)
            if isinstance(exc, asyncio.CancelledError):
                raise
        finally:
            queue.put_nowait(None)

    task = asyncio.create_task(_produce())
    try:
        while (item := await queue.get()) is not None:
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        if not task.done():
            task.cancel()
        with contextlib.suppress(BaseException):
            await task
