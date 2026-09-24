"""Graph nodes for the teaching graph.

Every node shares one signature: `async def node(state: AgentState, runtime:
Runtime[GraphContext]) -> AgentStateUpdate`. Nodes read dependencies (the LLM
client, DB session, ids) only from `runtime.context` -- never module-level
globals -- so they stay swappable and testable in isolation.

The specialized-agent stubs (`dsa_agent`, `debug_agent`, `explain_agent`) are
placeholders: Phase 07 replaces their *implementations* with full subgraphs
without renaming them (see `app.graph.routing` for the STABLE routing
contract these names are part of). Every other node here is real Phase 04
pipeline logic.

Nodes must never interpolate raw user-supplied text (from `state.input` /
`state.structured_input`) into their own output -- that data is untrusted
content, not something to echo back. `NodeError.message` values are always
fixed, safe strings for the same reason: never the raw exception text or any
user-supplied content, either of which might carry secrets or untrusted data.
"""

import logging
from collections.abc import Callable
from types import MappingProxyType
from typing import Final, Protocol, cast
from uuid import UUID

from langgraph.runtime import Runtime

from app.agents.planner import INTENT_DEFAULTS, analyze_problem, build_plan
from app.graph.routing import select_route
from app.graph.state import (
    AgentOutcome,
    AgentState,
    AgentStateUpdate,
    GraphContext,
    NodeError,
)
from app.input.intent import classify_intent as _classify_intent_llm
from app.input.normalize import merge_inputs, normalize_text
from app.input.vision import ImageValidationError, extract_from_image
from app.llm.base import LLMError
from app.memory.conversation import add_turn, get_recent_context
from app.memory.events import record_event, requested_help_for
from app.memory.profile import PRIOR, get_profile
from app.schemas.event import LearningEventCreate
from app.schemas.intent import Intent
from app.schemas.plan import TeachingPlan
from app.schemas.profile import LearnerProfileView

__all__ = [
    "FALLBACKS",
    "Node",
    "SAFE_FALLBACK_RESPONSE",
    "classify_intent",
    "clarify",
    "debug_agent",
    "dsa_agent",
    "explain_agent",
    "final_response",
    "load_learner_profile",
    "plan_teaching",
    "route",
    "safe_node",
    "understand_input",
    "update_learner_model",
]

logger = logging.getLogger(__name__)


class Node(Protocol):
    """Callable protocol for a graph node, matching langgraph's `_NodeWithRuntime`.

    `runtime` is keyword-only here (as langgraph itself requires); a plain
    `async def node(state, runtime)` function is still assignable, since a
    positional-or-keyword parameter satisfies a keyword-only call.
    """

    async def __call__(
        self, state: AgentState, *, runtime: Runtime[GraphContext]
    ) -> AgentStateUpdate: ...


# --- understand_input --------------------------------------------------------

_IMAGE_EXTRACTION_FAILED_MESSAGE: Final = "could not read the image; continuing with text only"


async def understand_input(state: AgentState, runtime: Runtime[GraphContext]) -> AgentStateUpdate:
    """Normalize `state.input`'s text and/or image into a `StructuredInput`.

    Always drops `state.input.image` from the returned update (keeping
    `image_mime`) once extraction has been attempted, so the raw image bytes
    aren't carried forward into every later node's state/trace.
    """
    ctx = runtime.context
    raw = state.input
    scrubbed_input = raw.model_copy(update={"image": None})

    text = raw.text
    text_result = (
        normalize_text(text, language_hint=raw.language) if text and text.strip() else None
    )

    if raw.image is None:
        return {"structured_input": text_result}

    try:
        image_result = await extract_from_image(ctx.llm, raw.image, declared_mime=raw.image_mime)
    except (ImageValidationError, LLMError) as exc:
        error = NodeError(
            node="understand_input",
            error_type=type(exc).__name__,
            message=_IMAGE_EXTRACTION_FAILED_MESSAGE,
        )
        return {"input": scrubbed_input, "structured_input": text_result, "errors": [error]}

    if text_result is not None:
        return {
            "input": scrubbed_input,
            "structured_input": merge_inputs(image_result, text_result),
        }
    return {"input": scrubbed_input, "structured_input": image_result}


def _understand_input_fallback(state: AgentState) -> AgentStateUpdate:
    scrubbed_input = state.input.model_copy(update={"image": None})
    return {"input": scrubbed_input, "structured_input": None}


# --- classify_intent ---------------------------------------------------------


async def classify_intent(state: AgentState, runtime: Runtime[GraphContext]) -> AgentStateUpdate:
    """Classify `state.structured_input`'s intent, or `None` for empty input."""
    inp = state.structured_input
    if inp is None or inp.is_empty:
        return {"intent": None}
    result = await _classify_intent_llm(inp, runtime.context.llm)
    return {"intent": result}


def _classify_intent_fallback(state: AgentState) -> AgentStateUpdate:
    del state
    return {"intent": None}


# --- load_learner_profile ----------------------------------------------------

_PROFILE_LOAD_FAILED_MESSAGE: Final = "could not load your learner profile"
_RECENT_CONTEXT_FAILED_MESSAGE: Final = "could not load recent conversation history"


async def load_learner_profile(
    state: AgentState, runtime: Runtime[GraphContext]
) -> AgentStateUpdate:
    """Load the learner's profile and recent conversation context, if available.

    Each read runs in its own savepoint (`session.begin_nested()`), so a DB
    error in one read can never leave the shared session's outer transaction
    aborted for later reads/writes in this turn. Errors are caught here
    (rather than left to `safe_node`) so a failed read degrades to an empty
    profile / empty context instead of losing the other read's result too.
    """
    del state
    ctx = runtime.context
    errors: list[NodeError] = []

    if ctx.session is not None and ctx.user_id is not None:
        try:
            async with ctx.session.begin_nested():
                profile = await get_profile(ctx.session, ctx.user_id)
        except Exception as exc:
            profile = LearnerProfileView.empty()
            errors.append(
                NodeError(
                    node="load_learner_profile",
                    error_type=type(exc).__name__,
                    message=_PROFILE_LOAD_FAILED_MESSAGE,
                )
            )
    else:
        profile = LearnerProfileView.empty()

    if ctx.session is None or ctx.user_id is None or ctx.conversation_id is None:
        update: AgentStateUpdate = {"profile": profile, "recent_context": []}
        if errors:
            update["errors"] = errors
        return update

    try:
        async with ctx.session.begin_nested():
            recent_context = await get_recent_context(ctx.session, ctx.user_id, ctx.conversation_id)
    except Exception as exc:
        recent_context = []
        errors.append(
            NodeError(
                node="load_learner_profile",
                error_type=type(exc).__name__,
                message=_RECENT_CONTEXT_FAILED_MESSAGE,
            )
        )

    update = {"profile": profile, "recent_context": recent_context}
    if errors:
        update["errors"] = errors
    return update


def _load_learner_profile_fallback(state: AgentState) -> AgentStateUpdate:
    del state
    return {"profile": LearnerProfileView.empty(), "recent_context": []}


# --- plan_teaching ------------------------------------------------------------


async def plan_teaching(state: AgentState, runtime: Runtime[GraphContext]) -> AgentStateUpdate:
    """Build this turn's `TeachingPlan` from the classified intent and profile."""
    del runtime
    profile = state.profile if state.profile is not None else LearnerProfileView.empty()
    analysis = analyze_problem(state.structured_input, profile, state.input.topic_hint)
    plan = build_plan(state.intent, profile, analysis)
    return {"plan": plan}


def _plan_teaching_fallback(state: AgentState) -> AgentStateUpdate:
    intent = state.intent
    strategy = (
        "clarify" if intent is None or intent.low_confidence else INTENT_DEFAULTS[intent.intent][1]
    )
    plan = TeachingPlan(
        difficulty="easy",
        assistance_level="hint",
        solution_strategy=strategy,
        topic=None,
        skill_level=PRIOR,
        rationale=["planner_fallback"],
    )
    return {"plan": plan}


# --- route --------------------------------------------------------------------


async def route(state: AgentState, runtime: Runtime[GraphContext]) -> AgentStateUpdate:
    """Record the routing decision for this turn on the state."""
    del runtime
    return {"route": select_route(state)}


def _route_fallback(state: AgentState) -> AgentStateUpdate:
    del state
    return {"route": "clarify"}


# --- Specialized-agent stubs (Phase 07 replaces these with subgraphs) ------


def _stub_outcome(label: str, plan: TeachingPlan | None) -> AgentOutcome:
    """Build a fixed, plan-derived placeholder outcome for a stub agent node.

    The text is built only from `plan`'s own fields (never from user input,
    which is untrusted). If no plan is available yet, a generic fallback is
    used instead.
    """
    if plan is not None:
        text = (
            f"[{label} stub] Phase 7 will provide {plan.solution_strategy} "
            f"at '{plan.assistance_level}' level (difficulty: {plan.difficulty})."
        )
    else:
        text = f"[{label} stub] Phase 7 will provide guided help once a teaching plan is available."
    return AgentOutcome(text=text, topic=plan.topic if plan is not None else None, solved=None)


_AGENT_FALLBACK_TEXT: Final = (
    "Something went wrong on my end while working on that -- could you try again, "
    "or rephrase your request?"
)


def _agent_outcome_fallback(state: AgentState) -> AgentStateUpdate:
    del state
    return {"agent_output": AgentOutcome(text=_AGENT_FALLBACK_TEXT, topic=None, solved=None)}


async def dsa_agent(state: AgentState, runtime: Runtime[GraphContext]) -> AgentStateUpdate:
    """Stub for the DSA solver subgraph (hint ladder), added in Phase 07."""
    del runtime
    return {"agent_output": _stub_outcome("dsa", state.plan)}


async def debug_agent(state: AgentState, runtime: Runtime[GraphContext]) -> AgentStateUpdate:
    """Stub for the debugger subgraph, added in Phase 07."""
    del runtime
    return {"agent_output": _stub_outcome("debug", state.plan)}


async def explain_agent(state: AgentState, runtime: Runtime[GraphContext]) -> AgentStateUpdate:
    """Stub for the code/concept explainer subgraph, added in Phase 07."""
    del runtime
    return {"agent_output": _stub_outcome("explain", state.plan)}


# --- Clarify node -----------------------------------------------------------

_ASK_FOR_INPUT: Final = (
    "I don't have enough to go on yet -- could you share the problem statement, "
    "your code, and/or the error message you're seeing?"
)

_GENERIC_CLARIFY: Final = (
    "Could you tell me a bit more about what you'd like help with -- a hint, "
    "a debugging walkthrough, an explanation, or a code review?"
)

_INTENT_PHRASES: Final[MappingProxyType[Intent, str]] = MappingProxyType(
    {
        Intent.DSA_SOLVE: "solve a DSA problem",
        Intent.DSA_HINT: "get a hint on a DSA problem",
        Intent.APPROACH_DISCUSSION: "discuss an approach to a problem",
        Intent.CODE_DEBUG: "debug your code",
        Intent.ERROR_EXPLANATION: "understand an error",
        Intent.TEST_CASE_ANALYSIS: "analyze a failing test case",
        Intent.CODE_EXPLAIN: "understand what your code does",
        Intent.CONCEPT_EXPLANATION: "explain a concept",
        Intent.IMAGE_CODE_ANALYSIS: "analyze code from an image",
        Intent.CODE_REVIEW: "review your code",
        Intent.OPTIMIZATION: "optimize your code",
    }
)


async def clarify(state: AgentState, runtime: Runtime[GraphContext]) -> AgentStateUpdate:
    """Ask a deterministic clarifying question; never echoes user input."""
    del runtime
    if state.structured_input is None or state.structured_input.is_empty:
        text = _ASK_FOR_INPUT
    elif state.intent is not None:
        phrase = _INTENT_PHRASES[state.intent.intent]
        text = (
            f"It looks like you might want to {phrase}. Could you confirm, or let me "
            "know if you'd prefer a hint, a debugging walkthrough, an explanation, or "
            "a code review?"
        )
    else:
        text = _GENERIC_CLARIFY
    return {"agent_output": AgentOutcome(text=text, topic=None, solved=None)}


# --- final_response -----------------------------------------------------------

SAFE_FALLBACK_RESPONSE: Final = (
    "I wasn't able to put together a full response for that -- could you try again?"
)


async def final_response(state: AgentState, runtime: Runtime[GraphContext]) -> AgentStateUpdate:
    """Surface the specialized agent's text as this turn's response.

    Phase 08 replaces this with real response generation (tone, formatting,
    etc.); today it's a pass-through of `agent_output.text`.
    """
    del runtime
    agent_output = state.agent_output
    response = agent_output.text if agent_output is not None else SAFE_FALLBACK_RESPONSE
    return {"response": response}


def _final_response_fallback(state: AgentState) -> AgentStateUpdate:
    del state
    return {"response": SAFE_FALLBACK_RESPONSE}


# --- update_learner_model -----------------------------------------------------

_BUILD_EVENT_FAILED_MESSAGE: Final = "failed to build this turn's learning event"
_SAVE_EVENT_FAILED_MESSAGE: Final = "failed to save this turn's learning event"
_SAVE_TURN_FAILED_MESSAGE: Final = "failed to save this turn's conversation history"


def _event_topic(plan: TeachingPlan | None, agent_output: AgentOutcome) -> str | None:
    return agent_output.topic or (plan.topic if plan is not None else None)


def _build_learning_event(state: AgentState, ctx: GraphContext, topic: str) -> LearningEventCreate:
    agent_output = state.agent_output
    intent_result = state.intent
    if agent_output is None or intent_result is None:
        # Callers only reach here after confirming both are set; this is a
        # future-proofing invariant, not expected control flow.
        raise ValueError("agent_output and intent are required to build a learning event")

    structured = state.structured_input
    problem = (structured.problem or structured.question) if structured is not None else None

    return LearningEventCreate(
        conversation_id=ctx.conversation_id,
        intent=intent_result.intent,
        problem=problem,
        topic=topic,
        pattern=agent_output.pattern,
        difficulty=state.plan.difficulty if state.plan is not None else None,
        requested_help=requested_help_for(intent_result.intent),
        hints_used=agent_output.hints_used,
        needed_full_solution=agent_output.needed_full_solution,
        errors=agent_output.errors,
        solved=agent_output.solved if agent_output.solved is not None else False,
    )


async def _persist_event(
    ctx: GraphContext, event: LearningEventCreate
) -> tuple[UUID | None, NodeError | None]:
    """Attempt to record `event`, isolated in its own savepoint.

    Returns the persisted event id (only when a new row was actually
    inserted) and/or a `NodeError` -- never raises.
    """
    if ctx.session is None or ctx.user_id is None:
        # Callers only reach here after confirming both are set; this is a
        # future-proofing invariant, not expected control flow.
        return None, NodeError(
            node="update_learner_model",
            error_type="RuntimeError",
            message=_SAVE_EVENT_FAILED_MESSAGE,
        )
    try:
        async with ctx.session.begin_nested():
            result = await record_event(ctx.session, ctx.user_id, event)
    except Exception as exc:
        return None, NodeError(
            node="update_learner_model",
            error_type=type(exc).__name__,
            message=_SAVE_EVENT_FAILED_MESSAGE,
        )
    return (result.event.id if result.applied else None), None


async def _persist_turns(state: AgentState, ctx: GraphContext) -> NodeError | None:
    """Save the user (and, if present, assistant) turn, isolated in its own savepoint."""
    if ctx.session is None or ctx.user_id is None or ctx.conversation_id is None:
        # Callers only reach here after confirming all three are set; this is
        # a future-proofing invariant, not expected control flow.
        return NodeError(
            node="update_learner_model",
            error_type="RuntimeError",
            message=_SAVE_TURN_FAILED_MESSAGE,
        )

    user_text = state.input.text
    user_content = user_text if user_text and user_text.strip() else "[image]"
    intent_result = state.intent

    try:
        async with ctx.session.begin_nested():
            await add_turn(
                ctx.session,
                ctx.user_id,
                ctx.conversation_id,
                "user",
                user_content,
                intent=intent_result.intent if intent_result is not None else None,
            )
            if state.response is not None:
                await add_turn(
                    ctx.session, ctx.user_id, ctx.conversation_id, "assistant", state.response
                )
    except Exception as exc:
        return NodeError(
            node="update_learner_model",
            error_type=type(exc).__name__,
            message=_SAVE_TURN_FAILED_MESSAGE,
        )
    return None


async def update_learner_model(
    state: AgentState, runtime: Runtime[GraphContext]
) -> AgentStateUpdate:
    """Emit (and, when safe, persist) this turn's learning event and conversation turns.

    A learning event is only ever *saved* (see `_persist_event`) when the
    agent reported an observed outcome (`agent_output.solved is not None`)
    and a session/user/topic are all available -- a stub's `solved=None`
    guess is surfaced via `events` for `/chat` to see, but never written to
    the profile. Conversation turns are only saved when `conversation_id` is
    set. Never commits: the caller owns the transaction.
    """
    ctx = runtime.context
    events: list[LearningEventCreate] = []
    events_persisted: list[UUID] = []
    errors: list[NodeError] = []

    agent_output = state.agent_output
    if (
        state.route is not None
        and state.route != "clarify"
        and state.intent is not None
        and agent_output is not None
    ):
        event: LearningEventCreate | None = None
        try:
            topic = _event_topic(state.plan, agent_output)
            if topic is not None:
                event = _build_learning_event(state, ctx, topic)
        except Exception as exc:
            errors.append(
                NodeError(
                    node="update_learner_model",
                    error_type=type(exc).__name__,
                    message=_BUILD_EVENT_FAILED_MESSAGE,
                )
            )

        if event is not None:
            events.append(event)

            can_persist = (
                agent_output.solved is not None
                and ctx.session is not None
                and ctx.user_id is not None
            )
            if can_persist:
                persisted_id, event_error = await _persist_event(ctx, event)
                if persisted_id is not None:
                    events_persisted.append(persisted_id)
                if event_error is not None:
                    errors.append(event_error)

    if ctx.conversation_id is not None and ctx.session is not None and ctx.user_id is not None:
        turn_error = await _persist_turns(state, ctx)
        if turn_error is not None:
            errors.append(turn_error)

    update: AgentStateUpdate = {}
    if events:
        update["events"] = events
    if events_persisted:
        update["events_persisted"] = events_persisted
    if errors:
        update["errors"] = errors
    return update


def _update_learner_model_fallback(state: AgentState) -> AgentStateUpdate:
    del state
    return {}


# --- safe_node ----------------------------------------------------------------

_SAFE_NODE_FAILURE_MESSAGE: Final = "this step failed unexpectedly; a safe fallback was used"


def safe_node(
    name: str,
    fn: Node,
    fallback: Callable[[AgentState], AgentStateUpdate],
) -> Node:
    """Wrap `fn` so any `Exception` degrades to `fallback(state)` plus a `NodeError`.

    Only `Exception` (never `BaseException`) is caught, so cooperative
    cancellation (`asyncio.CancelledError`) always propagates. The log line
    and the recorded `NodeError.message` are both fixed, safe strings --
    never the raw exception text, which may carry secrets or untrusted data.
    """

    async def wrapped(state: AgentState, *, runtime: Runtime[GraphContext]) -> AgentStateUpdate:
        try:
            return await fn(state, runtime=runtime)
        except Exception as exc:
            logger.warning("graph node %s failed: %s", name, type(exc).__name__)
            update = fallback(state)
            node_error = NodeError(
                node=name, error_type=type(exc).__name__, message=_SAFE_NODE_FAILURE_MESSAGE
            )
            existing_errors: list[NodeError] = list(update.get("errors", []))
            existing_errors.append(node_error)
            merged: AgentStateUpdate = cast("AgentStateUpdate", dict(update))
            merged["errors"] = existing_errors
            return merged

    return wrapped


FALLBACKS: Final[MappingProxyType[str, Callable[[AgentState], AgentStateUpdate]]] = (
    MappingProxyType(
        {
            "understand_input": _understand_input_fallback,
            "classify_intent": _classify_intent_fallback,
            "load_learner_profile": _load_learner_profile_fallback,
            "plan_teaching": _plan_teaching_fallback,
            "route": _route_fallback,
            "dsa_agent": _agent_outcome_fallback,
            "debug_agent": _agent_outcome_fallback,
            "explain_agent": _agent_outcome_fallback,
            "clarify": _agent_outcome_fallback,
            "final_response": _final_response_fallback,
            "update_learner_model": _update_learner_model_fallback,
        }
    )
)
