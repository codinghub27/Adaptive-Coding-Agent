"""Tests for the Phase 04 pipeline nodes, `safe_node`, and `FALLBACKS`."""

import asyncio
import logging
import uuid
from collections.abc import Sequence

import pytest
from langgraph.graph import END, START, StateGraph  # pyright: ignore[reportMissingTypeStubs]
from langgraph.runtime import Runtime
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.planner import INTENT_DEFAULTS
from app.db.models import Conversation, Message
from app.graph.nodes import (
    FALLBACKS,
    Node,
    clarify,
    classify_intent,
    debug_agent,
    dsa_agent,
    explain_agent,
    final_response,
    load_learner_profile,
    plan_teaching,
    route,
    safe_node,
    understand_input,
    update_learner_model,
)
from app.graph.state import (
    AgentOutcome,
    AgentState,
    AgentStateUpdate,
    GraphContext,
    NodeError,
    RawInput,
)
from app.input.normalize import normalize_text
from app.llm.base import LLMClient
from app.memory.conversation import get_recent_context, start_conversation
from app.memory.events import list_events
from app.memory.profile import PRIOR, get_profile, set_learning_preferences
from app.schemas.input import CodeBlock, StructuredInput
from app.schemas.intent import Intent, IntentResult
from app.schemas.plan import TeachingPlan
from app.schemas.profile import LearnerProfileView
from tests.input.fakes import FakeLLMClient

_NODE_NAMES: Sequence[str] = (
    "understand_input",
    "classify_intent",
    "load_learner_profile",
    "plan_teaching",
    "retrieve_knowledge",
    "route",
    "dsa_agent",
    "debug_agent",
    "explain_agent",
    "execute_code",
    "verify",
    "clarify",
    "final_response",
    "update_learner_model",
)

_EMPTY_PROFILE = LearnerProfileView(
    language=None, skill_levels={}, learning_preferences={}, common_errors=[]
)


def _runtime(
    *,
    llm: LLMClient | None = None,
    session: AsyncSession | None = None,
    user_id: uuid.UUID | None = None,
    conversation_id: uuid.UUID | None = None,
) -> Runtime[GraphContext]:
    return Runtime(
        context=GraphContext(
            llm=llm if llm is not None else FakeLLMClient(),
            session=session,
            user_id=user_id,
            conversation_id=conversation_id,
        )
    )


def _plan(topic: str | None = "arrays", difficulty: str = "easy") -> TeachingPlan:
    return TeachingPlan(
        difficulty=difficulty,  # type: ignore[arg-type]
        assistance_level="hint",
        solution_strategy="socratic_hints",
        topic=topic,
        skill_level=0.5,
    )


def _outcome(
    *,
    topic: str | None = "arrays",
    solved: bool | None = None,
    pattern: str | None = None,
    hints_used: int = 0,
    needed_full_solution: bool = False,
) -> AgentOutcome:
    return AgentOutcome(
        text="ok",
        topic=topic,
        pattern=pattern,
        solved=solved,
        hints_used=hints_used,
        needed_full_solution=needed_full_solution,
    )


def _pipeline_state(
    *,
    text: str = "help",
    route_key: str = "dsa",
    intent: Intent = Intent.DSA_HINT,
    agent_output: AgentOutcome | None = None,
    plan: TeachingPlan | None = None,
    response: str | None = "ok",
) -> AgentState:
    return AgentState(
        input=RawInput(text=text),
        structured_input=StructuredInput(source="text", question=text),
        intent=IntentResult(intent=intent, confidence=0.9, source="rule"),
        plan=plan if plan is not None else _plan(),
        route=route_key,  # type: ignore[arg-type]
        agent_output=agent_output,
        response=response,
    )


# ---------------------------------------------------------------------------
# understand_input
# ---------------------------------------------------------------------------


async def test_understand_input_text_only() -> None:
    text = "How do I reverse a linked list?"
    state = AgentState(input=RawInput(text=text))

    update = await understand_input(state, _runtime())

    assert update.get("structured_input") == normalize_text(text)
    assert not update.get("errors")


async def test_understand_input_image_validation_failure_keeps_text() -> None:
    text = "please help me debug this"
    state = AgentState(input=RawInput(text=text, image=b"not-a-real-image"))

    update = await understand_input(state, _runtime())

    assert update.get("structured_input") == normalize_text(text)
    errors = update.get("errors")
    assert errors is not None and len(errors) == 1
    assert errors[0].node == "understand_input"
    assert errors[0].error_type == "ImageValidationError"


async def test_understand_input_image_llm_error_keeps_text() -> None:
    text = "please help me debug this"
    image = b"\x89PNG\r\n\x1a\n" + b"0" * 16
    state = AgentState(input=RawInput(text=text, image=image, image_mime="image/png"))
    llm = FakeLLMClient(raise_vision=True)

    update = await understand_input(state, _runtime(llm=llm))

    assert update.get("structured_input") == normalize_text(text)
    errors = update.get("errors")
    assert errors is not None and len(errors) == 1
    assert errors[0].error_type == "LLMError"


async def test_understand_input_nothing_returns_none() -> None:
    state = AgentState(input=RawInput(text=None, image=None))

    update = await understand_input(state, _runtime())

    assert update == {"structured_input": None}


# ---------------------------------------------------------------------------
# classify_intent
# ---------------------------------------------------------------------------


async def test_classify_intent_none_structured_input_no_llm_call() -> None:
    llm = FakeLLMClient()
    state = AgentState(input=RawInput(text=None))

    update = await classify_intent(state, _runtime(llm=llm))

    assert update == {"intent": None}
    assert llm.chat_calls == []


async def test_classify_intent_empty_structured_input_no_llm_call() -> None:
    llm = FakeLLMClient()
    state = AgentState(input=RawInput(text=None), structured_input=StructuredInput(source="text"))

    update = await classify_intent(state, _runtime(llm=llm))

    assert update == {"intent": None}
    assert llm.chat_calls == []


async def test_classify_intent_delegates_to_llm_classifier() -> None:
    llm = FakeLLMClient(
        chat_content='{"intent": "CODE_EXPLAIN", "confidence": 0.9, "rationale": "ok"}'
    )
    structured = StructuredInput(
        source="text", question="what does this do", code=[CodeBlock(content="print(1)")]
    )
    state = AgentState(input=RawInput(text="what does this do"), structured_input=structured)

    update = await classify_intent(state, _runtime(llm=llm))

    result = update.get("intent")
    assert result is not None
    assert result.intent == Intent.CODE_EXPLAIN
    assert len(llm.chat_calls) == 1


# ---------------------------------------------------------------------------
# load_learner_profile
# ---------------------------------------------------------------------------


async def test_load_learner_profile_no_session_returns_empty() -> None:
    state = AgentState(input=RawInput(text="hi"))

    update = await load_learner_profile(state, _runtime())

    assert update == {"profile": _EMPTY_PROFILE, "recent_context": []}


@pytest.mark.db
async def test_load_learner_profile_real_profile(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    await set_learning_preferences(db_session, user_id, {"prefers_hints": True})
    state = AgentState(input=RawInput(text="hi"))

    update = await load_learner_profile(state, _runtime(session=db_session, user_id=user_id))

    profile = update.get("profile")
    assert profile is not None
    assert profile.learning_preferences.get("prefers_hints") is True
    assert update.get("recent_context") == []


@pytest.mark.db
async def test_load_learner_profile_bad_conversation_id_keeps_profile(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    await set_learning_preferences(db_session, user_id, {"prefers_hints": True})
    bad_conversation_id = uuid.uuid4()
    state = AgentState(input=RawInput(text="hi"))

    update = await load_learner_profile(
        state,
        _runtime(session=db_session, user_id=user_id, conversation_id=bad_conversation_id),
    )

    profile = update.get("profile")
    assert profile is not None
    assert profile.learning_preferences.get("prefers_hints") is True
    assert update.get("recent_context") == []
    errors = update.get("errors")
    assert errors is not None and len(errors) == 1
    assert errors[0].node == "load_learner_profile"
    assert errors[0].error_type == "ConversationNotFoundError"


@pytest.mark.db
async def test_load_learner_profile_db_error_leaves_session_usable(
    db_session: AsyncSession, user_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real DB error inside the profile read must not abort the outer transaction."""

    async def broken_get_profile(session: AsyncSession, uid: uuid.UUID) -> LearnerProfileView:
        del uid
        await session.execute(text("SELECT 1/0"))
        raise AssertionError("unreachable")  # pragma: no cover

    monkeypatch.setattr("app.graph.nodes.get_profile", broken_get_profile)

    state = AgentState(input=RawInput(text="hi"))
    update = await load_learner_profile(state, _runtime(session=db_session, user_id=user_id))

    assert update.get("profile") == LearnerProfileView.empty()
    errors = update.get("errors")
    assert errors is not None and len(errors) == 1
    assert errors[0].node == "load_learner_profile"

    # The session must still be usable after the rolled-back savepoint.
    result = await db_session.execute(select(1))
    assert result.scalar_one() == 1

    # `update_learner_model` can still persist turns in the same session.
    conversation_id = await start_conversation(db_session, user_id)
    pipeline_state = _pipeline_state(agent_output=_outcome(solved=None))
    persist_update = await update_learner_model(
        pipeline_state,
        _runtime(session=db_session, user_id=user_id, conversation_id=conversation_id),
    )
    assert not persist_update.get("errors")

    turns = await get_recent_context(db_session, user_id, conversation_id)
    assert [t.role for t in turns] == ["user", "assistant"]


# ---------------------------------------------------------------------------
# plan_teaching
# ---------------------------------------------------------------------------


async def test_plan_teaching_consumes_profile() -> None:
    profile = LearnerProfileView(
        language=None, skill_levels={"arrays": 0.2}, learning_preferences={}, common_errors=[]
    )
    structured = StructuredInput(source="text", problem="Given an array of integers, ...")
    intent = IntentResult(intent=Intent.DSA_HINT, confidence=0.9, source="rule")
    state = AgentState(
        input=RawInput(text="hint please", topic_hint="arrays"),
        structured_input=structured,
        intent=intent,
        profile=profile,
    )

    update = await plan_teaching(state, _runtime())

    plan = update.get("plan")
    assert isinstance(plan, TeachingPlan)
    assert plan.topic == "arrays"
    assert plan.skill_level == 0.2


def test_plan_teaching_fallback_clarify_when_no_intent() -> None:
    state = AgentState(input=RawInput(text="hi"))

    update = FALLBACKS["plan_teaching"](state)

    plan = update.get("plan")
    assert plan is not None
    assert plan.solution_strategy == "clarify"


def test_plan_teaching_fallback_clarify_when_low_confidence() -> None:
    intent = IntentResult(intent=Intent.CODE_DEBUG, confidence=0.2, source="rule")
    state = AgentState(input=RawInput(text="hi"), intent=intent)

    update = FALLBACKS["plan_teaching"](state)

    plan = update.get("plan")
    assert plan is not None
    assert plan.solution_strategy == "clarify"


def test_plan_teaching_fallback_uses_intent_defaults_when_confident() -> None:
    intent = IntentResult(intent=Intent.CODE_DEBUG, confidence=0.9, source="rule")
    state = AgentState(input=RawInput(text="hi"), intent=intent)

    update = FALLBACKS["plan_teaching"](state)

    plan = update.get("plan")
    assert plan is not None
    assert plan.solution_strategy == INTENT_DEFAULTS[Intent.CODE_DEBUG][1]


# ---------------------------------------------------------------------------
# update_learner_model
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_update_learner_model_stub_outcome_not_persisted(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    state = _pipeline_state(agent_output=_outcome(solved=None))

    update = await update_learner_model(state, _runtime(session=db_session, user_id=user_id))

    events = update.get("events")
    assert events is not None and len(events) == 1
    assert events[0].topic == "arrays"
    assert not update.get("events_persisted")
    assert not update.get("errors")

    rows = await list_events(db_session, user_id)
    assert rows == []

    profile = await get_profile(db_session, user_id)
    assert profile.skill_levels == {}


@pytest.mark.db
async def test_update_learner_model_persists_observed_outcome(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    state = _pipeline_state(agent_output=_outcome(solved=True))

    update = await update_learner_model(state, _runtime(session=db_session, user_id=user_id))

    events = update.get("events")
    assert events is not None and len(events) == 1
    event_id = events[0].event_id
    assert update.get("events_persisted") == [event_id]
    assert not update.get("errors")

    profile = await get_profile(db_session, user_id)
    assert profile.skill_levels["arrays"] > PRIOR


async def test_update_learner_model_clarify_route_no_event() -> None:
    outcome = AgentOutcome(text="clarify text", topic=None, solved=None)
    state = _pipeline_state(route_key="clarify", agent_output=outcome)

    update = await update_learner_model(state, _runtime())

    assert not update.get("events")
    assert not update.get("events_persisted")


@pytest.mark.db
async def test_update_learner_model_saves_turns_with_conversation_id(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    conversation_id = await start_conversation(db_session, user_id)
    state = _pipeline_state(agent_output=_outcome(solved=None), response="here is a hint")

    update = await update_learner_model(
        state, _runtime(session=db_session, user_id=user_id, conversation_id=conversation_id)
    )

    assert not update.get("errors")
    turns = await get_recent_context(db_session, user_id, conversation_id)
    assert [t.role for t in turns] == ["user", "assistant"]
    assert turns[0].content == "help"
    assert turns[0].intent == Intent.DSA_HINT
    assert turns[1].content == "here is a hint"


@pytest.mark.db
async def test_update_learner_model_no_turns_without_conversation_id(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    state = _pipeline_state(agent_output=_outcome(solved=None))

    update = await update_learner_model(state, _runtime(session=db_session, user_id=user_id))

    assert not update.get("errors")
    rows = (
        (await db_session.execute(select(Message).where(Message.user_id == user_id)))
        .scalars()
        .all()
    )
    assert rows == []


@pytest.mark.db
async def test_update_learner_model_unknown_user_id_records_node_error_and_session_survives(
    db_session: AsyncSession,
) -> None:
    unknown_user_id = uuid.uuid4()
    state = _pipeline_state(agent_output=_outcome(solved=True))

    update = await update_learner_model(
        state, _runtime(session=db_session, user_id=unknown_user_id)
    )

    errors = update.get("errors")
    assert errors is not None and len(errors) == 1
    assert errors[0].node == "update_learner_model"
    assert not update.get("events_persisted")

    # The session must still be usable after the rolled-back savepoint. Scoped
    # to this turn's own (unknown) user rather than the whole `messages` table:
    # an unfiltered select asserts the table is globally empty, which is false
    # on any dev DB that has been used (the fixture rolls back its own rows,
    # not pre-existing ones).
    result = await db_session.execute(
        select(Message).join(Conversation).where(Conversation.user_id == unknown_user_id)
    )
    assert result.scalars().all() == []


@pytest.mark.db
async def test_update_learner_model_invalid_event_topic_still_saves_turns(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    """A learning event that fails `LearningEventCreate` validation must not
    block turn persistence."""
    conversation_id = await start_conversation(db_session, user_id)
    too_long_topic = "a" * 65
    state = _pipeline_state(
        agent_output=_outcome(topic=too_long_topic, solved=None), response="here is a hint"
    )

    update = await update_learner_model(
        state, _runtime(session=db_session, user_id=user_id, conversation_id=conversation_id)
    )

    assert not update.get("events")
    errors = update.get("errors")
    assert errors is not None and len(errors) == 1
    assert errors[0].node == "update_learner_model"

    turns = await get_recent_context(db_session, user_id, conversation_id)
    assert [t.role for t in turns] == ["user", "assistant"]


# ---------------------------------------------------------------------------
# safe_node
# ---------------------------------------------------------------------------


async def test_safe_node_falls_back_on_exception_without_leaking_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def failing(state: AgentState, runtime: Runtime[GraphContext]) -> AgentStateUpdate:
        del state, runtime
        raise RuntimeError("secret-token-xyz")

    def fallback(state: AgentState) -> AgentStateUpdate:
        del state
        return {"response": "fallback-response"}

    wrapped: Node = safe_node("test_node", failing, fallback)

    with caplog.at_level(logging.WARNING):
        update = await wrapped(AgentState(input=RawInput(text="hi")), runtime=_runtime())

    assert update.get("response") == "fallback-response"
    errors = update.get("errors")
    assert errors is not None and len(errors) == 1
    assert errors[0].node == "test_node"
    assert errors[0].error_type == "RuntimeError"
    assert "secret-token-xyz" not in errors[0].message
    assert "secret-token-xyz" not in caplog.text


async def test_safe_node_preserves_fallback_errors() -> None:
    async def failing(state: AgentState, runtime: Runtime[GraphContext]) -> AgentStateUpdate:
        del state, runtime
        raise ValueError("boom")

    def fallback(state: AgentState) -> AgentStateUpdate:
        del state
        return {"errors": [NodeError(node="other", error_type="X", message="pre-existing")]}

    wrapped: Node = safe_node("test_node", failing, fallback)

    update = await wrapped(AgentState(input=RawInput(text="hi")), runtime=_runtime())

    errors = update.get("errors")
    assert errors is not None and len(errors) == 2
    assert {e.node for e in errors} == {"other", "test_node"}


async def test_safe_node_lets_cancelled_error_propagate() -> None:
    async def failing(state: AgentState, runtime: Runtime[GraphContext]) -> AgentStateUpdate:
        del state, runtime
        raise asyncio.CancelledError

    def fallback(state: AgentState) -> AgentStateUpdate:
        del state
        return {}

    wrapped: Node = safe_node("test_node", failing, fallback)

    with pytest.raises(asyncio.CancelledError):
        await wrapped(AgentState(input=RawInput(text="hi")), runtime=_runtime())


async def test_safe_node_wrapped_function_still_receives_runtime_injection() -> None:
    async def uses_runtime(state: AgentState, runtime: Runtime[GraphContext]) -> AgentStateUpdate:
        del state
        assert isinstance(runtime.context, GraphContext)
        return {"response": "used-runtime"}

    def fallback(state: AgentState) -> AgentStateUpdate:
        del state
        return {}

    wrapped = safe_node("uses_runtime", uses_runtime, fallback)

    builder = StateGraph(AgentState, context_schema=GraphContext)
    builder.add_node("uses_runtime", wrapped)  # pyright: ignore[reportUnknownMemberType]
    builder.add_edge(START, "uses_runtime")
    builder.add_edge("uses_runtime", END)
    graph = builder.compile()  # pyright: ignore[reportUnknownMemberType]

    result = await graph.ainvoke(  # pyright: ignore[reportUnknownMemberType]
        AgentState(input=RawInput(text="hi")),
        context=GraphContext(llm=FakeLLMClient()),
    )

    assert result["response"] == "used-runtime"


def test_fallbacks_has_entry_for_every_node_name() -> None:
    assert set(FALLBACKS) == set(_NODE_NAMES)
    assert len(FALLBACKS) == len(_NODE_NAMES)


# ---------------------------------------------------------------------------
# Miscellaneous node smoke coverage (route / final_response)
# ---------------------------------------------------------------------------


async def test_route_records_decision_on_state() -> None:
    state = _pipeline_state()
    update = await route(state, _runtime())
    assert update.get("route") == "dsa"


async def test_final_response_uses_agent_output_text() -> None:
    state = _pipeline_state(agent_output=_outcome())
    update = await final_response(state, _runtime())
    assert update.get("response") == "ok"


async def test_final_response_fixed_text_when_no_agent_output() -> None:
    state = AgentState(input=RawInput(text="hi"))
    update = await final_response(state, _runtime())
    assert update.get("response") is not None
    assert "hi" not in (update.get("response") or "")


async def test_specialized_agents_and_clarify_return_real_outcomes() -> None:
    """Phase 07: the three specialized-agent nodes call their real subgraphs
    (no session/user, no runner, so `dsa_agent`'s hint progress starts fresh
    and `debug_agent`/`explain_agent` never need to run code)."""
    state = _pipeline_state(agent_output=None)
    llm = FakeLLMClient(chat_content="{}")
    for node in (dsa_agent, debug_agent, explain_agent, clarify):
        update = await node(state, _runtime(llm=llm))
        outcome = update.get("agent_output")
        assert outcome is not None
        assert "stub" not in outcome.text.lower()
