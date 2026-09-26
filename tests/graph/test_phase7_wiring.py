"""Phase 07 P7 wiring tests.

Covers: `dsa_agent`/`debug_agent`/`explain_agent` calling the real Phase 07
subgraphs/pipelines (no more stub text), `explain_agent`'s `CODE_REVIEW`/
`OPTIMIZATION` dispatch to the reviewer, cross-turn hint-progress
reconstruction (`resolve_hint_progress`), the graph still compiling with an
unchanged node/fallback contract, a full end-to-end `run_graph` turn, and
`safe_node` fallback behaviour when a subgraph raises. No Docker, no network,
no real LLM/provider calls -- `FakeLLMClient`/`FakeRunner` throughout.
"""

import uuid
from typing import Final, NoReturn

import pytest
from langgraph.runtime import Runtime
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.hint_engine import HintProgress
from app.agents.reviewer import ReviewRunResult
from app.execution.base import CodeRunner
from app.graph.build import NODE_FUNCTIONS, build_graph, run_graph
from app.graph.nodes import (
    FALLBACKS,
    debug_agent,
    dsa_agent,
    explain_agent,
    resolve_hint_progress,
    safe_node,
)
from app.graph.state import AgentState, GraphContext, RawInput
from app.graph.subgraphs.explain import ExplainRunResult
from app.llm.base import LLMClient
from app.memory.conversation import start_conversation
from app.memory.hint_progress import get_hint_progress, save_hint_progress
from app.schemas.agent_results import ExplainResult, HintLevel, ReviewResult
from app.schemas.execution import ExecutionRequest, ExecutionResult, TestCase, TestSuite
from app.schemas.input import CodeBlock, StructuredInput
from app.schemas.intent import Intent, IntentResult
from app.schemas.plan import AssistanceLevel, TeachingPlan
from tests.graph.test_execute_verify_nodes import FakeRunner
from tests.input.fakes import FakeLLMClient

_DSA_PROBLEM_TEXT: Final = (
    "Given an array of integers nums and an integer target, return indices "
    "of the two numbers such that they add up to target."
)
_DSA_INTENT_JSON: Final = '{"intent": "DSA_HINT", "confidence": 0.9, "rationale": "clear"}'


def _plan(
    *, topic: str | None = "arrays", assistance_level: AssistanceLevel = "hint"
) -> TeachingPlan:
    return TeachingPlan(
        difficulty="easy",
        assistance_level=assistance_level,
        solution_strategy="socratic_hints",
        topic=topic,
        skill_level=0.5,
    )


def _pipeline_state(
    *,
    route_key: str = "dsa",
    intent: Intent = Intent.DSA_HINT,
    plan: TeachingPlan | None = None,
    structured: StructuredInput | None = None,
    execution_request: ExecutionRequest | None = None,
) -> AgentState:
    default_structured = StructuredInput(source="text", question="help")
    return AgentState(
        input=RawInput(text="help"),
        structured_input=structured if structured is not None else default_structured,
        intent=IntentResult(intent=intent, confidence=0.9, source="rule"),
        plan=plan if plan is not None else _plan(),
        route=route_key,  # type: ignore[arg-type]
        execution_request=execution_request,
    )


def _runtime(
    *,
    llm: LLMClient | None = None,
    session: AsyncSession | None = None,
    user_id: uuid.UUID | None = None,
    conversation_id: uuid.UUID | None = None,
    runner: CodeRunner | None = None,
) -> Runtime[GraphContext]:
    return Runtime(
        context=GraphContext(
            llm=llm if llm is not None else FakeLLMClient(),
            session=session,
            user_id=user_id,
            conversation_id=conversation_id,
            runner=runner,
        )
    )


# ---------------------------------------------------------------------------
# dsa_agent
# ---------------------------------------------------------------------------


async def test_dsa_agent_returns_real_outcome_without_execution_request() -> None:
    """Low assistance level + a fresh ladder never reaches L6 -> no code, no request."""
    state = _pipeline_state(
        structured=StructuredInput(source="text", problem="Given an array of integers, ..."),
        plan=_plan(topic="arrays", assistance_level="hint"),
    )
    llm = FakeLLMClient(chat_content="{}")

    update = await dsa_agent(state, _runtime(llm=llm))

    outcome = update.get("agent_output")
    assert outcome is not None
    assert outcome.text
    assert "stub" not in outcome.text.lower()
    assert "execution_request" not in update


@pytest.mark.db
async def test_dsa_agent_sets_execution_request_once_ladder_reaches_full(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    """A prior hint-progress row already at rung L5 for this conversation+topic
    resumes the ladder at L6 this turn, producing code and an execution request.

    Uses `app.memory.hint_progress` directly (the dedicated store `dsa_agent`
    reads/writes) rather than `record_event`/a `LearningEvent`: hint-ladder
    progress is conversation state, not a learning outcome, so it is no
    longer reconstructed from the event log (see `resolve_hint_progress`).
    """
    conversation_id = await start_conversation(db_session, user_id)
    await save_hint_progress(
        db_session, user_id, conversation_id, "two_pointers", level=5, solved=False
    )

    state = _pipeline_state(
        structured=StructuredInput(source="text", problem="Given a sorted array, ..."),
        plan=_plan(topic="two_pointers", assistance_level="full"),
    )
    llm = FakeLLMClient(chat_content='{"code": "print(1)"}')

    update = await dsa_agent(
        state,
        _runtime(llm=llm, session=db_session, user_id=user_id, conversation_id=conversation_id),
    )

    request = update.get("execution_request")
    assert request is not None
    assert request.code == "print(1)"
    outcome = update.get("agent_output")
    assert outcome is not None
    assert outcome.needed_full_solution is True
    assert outcome.hints_used == 7


# ---------------------------------------------------------------------------
# debug_agent
# ---------------------------------------------------------------------------


async def test_debug_agent_no_runner_returns_real_outcome_without_execution_request() -> None:
    state = _pipeline_state(
        route_key="debug",
        intent=Intent.CODE_DEBUG,
        structured=StructuredInput(
            source="text", question="why does this fail", code=[CodeBlock(content="def f(): 1/0")]
        ),
    )

    update = await debug_agent(state, _runtime())

    outcome = update.get("agent_output")
    assert outcome is not None
    assert "stub" not in outcome.text.lower()
    assert "execution_request" not in update


async def test_debug_agent_with_runner_and_code_sets_execution_request() -> None:
    state = _pipeline_state(
        route_key="debug",
        intent=Intent.CODE_DEBUG,
        structured=StructuredInput(
            source="text",
            question="why does this fail",
            code=[CodeBlock(content="def f():\n    return 1\n")],
        ),
    )
    runner = FakeRunner(ExecutionResult(status="completed", phase="script"))
    llm = FakeLLMClient(chat_content="ok")

    update = await debug_agent(state, _runtime(llm=llm, runner=runner))

    request = update.get("execution_request")
    assert request is not None
    assert request.code == "def f():\n    return 1\n"
    outcome = update.get("agent_output")
    assert outcome is not None
    assert "stub" not in outcome.text.lower()


async def test_debug_agent_threads_extracted_tests_into_sandbox_request() -> None:
    """A debug turn whose `structured_input` carries worked examples must reach
    the sandbox with a derived `TestSuite` (Phase 07 Known Issue defect 1),
    even though nothing earlier in the graph populated `state.execution_request`.
    """
    statement = "Example 1:\nInput: nums = [2,7,11,15], target = 9\nOutput: [0,1]\n"
    state = _pipeline_state(
        route_key="debug",
        intent=Intent.CODE_DEBUG,
        structured=StructuredInput(
            source="text",
            problem=statement,
            code=[CodeBlock(content="def two_sum(nums, target):\n    return None\n")],
        ),
    )
    runner = FakeRunner(ExecutionResult(status="completed", phase="script"))
    llm = FakeLLMClient(chat_content="ok")

    await debug_agent(state, _runtime(llm=llm, runner=runner))

    assert runner.calls, "expected the sandbox to be invoked"
    assert runner.calls[0].tests is not None
    assert runner.calls[0].tests.entrypoint == "two_sum"


async def test_debug_agent_explicit_execution_request_tests_take_precedence() -> None:
    """`state.execution_request.tests`, when already set, must keep winning
    over the extractor's derived suite."""
    explicit_tests = TestSuite(
        entrypoint="two_sum", cases=[TestCase(name="c1", args=[1], expected=1)]
    )
    statement = "Example 1:\nInput: nums = [2,7,11,15], target = 9\nOutput: [0,1]\n"
    code = "def two_sum(nums, target):\n    return None\n"
    state = _pipeline_state(
        route_key="debug",
        intent=Intent.CODE_DEBUG,
        structured=StructuredInput(
            source="text", problem=statement, code=[CodeBlock(content=code)]
        ),
        execution_request=ExecutionRequest(code=code, tests=explicit_tests),
    )
    runner = FakeRunner(ExecutionResult(status="completed", phase="script"))
    llm = FakeLLMClient(chat_content="ok")

    await debug_agent(state, _runtime(llm=llm, runner=runner))

    assert runner.calls[0].tests == explicit_tests


# ---------------------------------------------------------------------------
# explain_agent: dispatch + real outcomes + execution_request
# ---------------------------------------------------------------------------


async def test_explain_agent_dispatches_review_and_explain_intents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_review_code(
        state: AgentState, runtime: Runtime[GraphContext], *, tests: TestSuite | None = None
    ) -> ReviewRunResult:
        del state, runtime, tests
        calls.append("review")
        return ReviewRunResult(result=ReviewResult(), execution_request=None)

    async def fake_run_explain(
        state: AgentState, runtime: Runtime[GraphContext]
    ) -> ExplainRunResult:
        del state, runtime
        calls.append("explain")
        return ExplainRunResult(result=ExplainResult(), execution_request=None)

    monkeypatch.setattr("app.graph.nodes.review_code", fake_review_code)
    monkeypatch.setattr("app.graph.nodes.run_explain", fake_run_explain)

    cases = [
        (Intent.CODE_REVIEW, "review"),
        (Intent.OPTIMIZATION, "review"),
        (Intent.CODE_EXPLAIN, "explain"),
        (Intent.CONCEPT_EXPLANATION, "explain"),
    ]
    for intent_value, expected in cases:
        calls.clear()
        state = _pipeline_state(route_key="explain", intent=intent_value)

        update = await explain_agent(state, _runtime())

        assert calls == [expected]
        assert update.get("agent_output") is not None


async def test_explain_agent_non_review_intent_never_sets_execution_request() -> None:
    state = _pipeline_state(
        route_key="explain",
        intent=Intent.CODE_EXPLAIN,
        structured=StructuredInput(source="text", question="what does this do"),
    )

    update = await explain_agent(state, _runtime())

    outcome = update.get("agent_output")
    assert outcome is not None
    assert "stub" not in outcome.text.lower()
    assert "execution_request" not in update


async def test_explain_agent_review_intent_sets_execution_request_when_possible() -> None:
    code = "def f():\n    return 1\n"
    tests = TestSuite(entrypoint="f", cases=[TestCase(name="c1", args=[], expected=1)])
    structured = StructuredInput(
        source="text", question="review this", code=[CodeBlock(content=code)]
    )
    state = _pipeline_state(
        route_key="explain",
        intent=Intent.CODE_REVIEW,
        structured=structured,
        execution_request=ExecutionRequest(code=code, tests=tests),
    )
    runner = FakeRunner(ExecutionResult(status="runtime_error", phase="tests"))
    llm = FakeLLMClient(chat_content='{"findings": []}')

    update = await explain_agent(state, _runtime(llm=llm, runner=runner))

    request = update.get("execution_request")
    assert request is not None
    assert request.code == code
    outcome = update.get("agent_output")
    assert outcome is not None
    assert "stub" not in outcome.text.lower()


async def test_explain_agent_review_intent_no_runner_never_sets_execution_request() -> None:
    code = "def f():\n    return 1\n"
    structured = StructuredInput(
        source="text", question="review this", code=[CodeBlock(content=code)]
    )
    state = _pipeline_state(
        route_key="explain",
        intent=Intent.CODE_REVIEW,
        structured=structured,
    )
    llm = FakeLLMClient(chat_content='{"findings": []}')

    update = await explain_agent(state, _runtime(llm=llm))

    assert "execution_request" not in update


# ---------------------------------------------------------------------------
# resolve_hint_progress
# ---------------------------------------------------------------------------


async def test_resolve_hint_progress_no_session_or_user_returns_fresh() -> None:
    state = _pipeline_state(plan=_plan(topic="arrays"))
    ctx = GraphContext(llm=FakeLLMClient())

    progress = await resolve_hint_progress(state, ctx)

    assert progress == HintProgress()


@pytest.mark.db
async def test_resolve_hint_progress_no_conversation_id_returns_fresh(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    state = _pipeline_state(plan=_plan(topic="arrays"))
    ctx = GraphContext(llm=FakeLLMClient(), session=db_session, user_id=user_id)

    progress = await resolve_hint_progress(state, ctx)

    assert progress == HintProgress()


@pytest.mark.db
async def test_resolve_hint_progress_no_plan_topic_returns_fresh(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    conversation_id = await start_conversation(db_session, user_id)
    state = _pipeline_state(plan=_plan(topic=None))
    ctx = GraphContext(
        llm=FakeLLMClient(), session=db_session, user_id=user_id, conversation_id=conversation_id
    )

    progress = await resolve_hint_progress(state, ctx)

    assert progress == HintProgress()


@pytest.mark.db
async def test_resolve_hint_progress_reads_stored_progress_same_conversation_and_topic(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    """Reads from `app.memory.hint_progress`, not the event log (see F1/F7/F8:
    hint-ladder progress is conversation state, no longer reconstructed from
    `LearningEvent`s, and keyed on `slug_tag(plan.topic)` -- the same key
    `dsa_agent` writes under)."""
    conversation_id = await start_conversation(db_session, user_id)
    await save_hint_progress(db_session, user_id, conversation_id, "graphs", level=2, solved=False)

    state = _pipeline_state(plan=_plan(topic="graphs"))
    ctx = GraphContext(
        llm=FakeLLMClient(), session=db_session, user_id=user_id, conversation_id=conversation_id
    )

    progress = await resolve_hint_progress(state, ctx)

    assert progress.last_level == HintLevel(2)
    assert progress.solved is False


@pytest.mark.db
async def test_resolve_hint_progress_no_matching_topic_returns_fresh(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    conversation_id = await start_conversation(db_session, user_id)
    await save_hint_progress(db_session, user_id, conversation_id, "graphs", level=3, solved=False)

    state = _pipeline_state(plan=_plan(topic="arrays"))
    ctx = GraphContext(
        llm=FakeLLMClient(), session=db_session, user_id=user_id, conversation_id=conversation_id
    )

    progress = await resolve_hint_progress(state, ctx)

    assert progress == HintProgress()


# ---------------------------------------------------------------------------
# dsa_agent <-> hint_progress store: the headline end-to-end fix (F1/F7/F8)
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_dsa_agent_hint_level_climbs_across_turns_via_hint_progress_store(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    """Three DSA turns on the same user/conversation/topic climb the hint
    ladder 0 -> 1 -> 2, with progress flowing *only* through the dedicated
    `app.memory.hint_progress` store (no mocking of `resolve_hint_progress`
    or `save_hint_progress`) -- this is the test that would have caught the
    original bug, where every turn returned L0 again."""
    conversation_id = await start_conversation(db_session, user_id)
    plan = _plan(topic="arrays", assistance_level="full")
    runtime = _runtime(
        llm=FakeLLMClient(chat_content="{}"),
        session=db_session,
        user_id=user_id,
        conversation_id=conversation_id,
    )

    levels: list[int] = []
    for _ in range(3):
        state = _pipeline_state(
            plan=plan,
            structured=StructuredInput(source="text", problem=_DSA_PROBLEM_TEXT),
        )
        update = await dsa_agent(state, runtime)
        outcome = update.get("agent_output")
        assert outcome is not None
        levels.append(outcome.hints_used - 1)

    assert levels == [0, 1, 2]


@pytest.mark.db
async def test_debug_agent_never_changes_dsa_hint_progress(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    """A debugger turn on the same conversation+topic must not move (or
    reset) the DSA hint ladder -- only `dsa_agent` ever writes the
    hint-progress store (F8 regression guard)."""
    conversation_id = await start_conversation(db_session, user_id)
    plan = _plan(topic="arrays", assistance_level="full")
    runtime = _runtime(
        llm=FakeLLMClient(chat_content="{}"),
        session=db_session,
        user_id=user_id,
        conversation_id=conversation_id,
    )

    dsa_state = _pipeline_state(
        plan=plan, structured=StructuredInput(source="text", problem=_DSA_PROBLEM_TEXT)
    )
    first = await dsa_agent(dsa_state, runtime)
    first_outcome = first.get("agent_output")
    assert first_outcome is not None
    assert first_outcome.hints_used - 1 == 0

    debug_state = _pipeline_state(
        route_key="debug",
        intent=Intent.CODE_DEBUG,
        plan=_plan(topic="arrays"),
        structured=StructuredInput(
            source="text", question="why does this fail", code=[CodeBlock(content="def f(): 1/0")]
        ),
    )
    await debug_agent(debug_state, runtime)

    progress = await get_hint_progress(db_session, user_id, conversation_id, "arrays")
    assert progress.last_level == HintLevel.L0_NUDGE

    dsa_state_again = _pipeline_state(
        plan=plan, structured=StructuredInput(source="text", problem=_DSA_PROBLEM_TEXT)
    )
    second = await dsa_agent(dsa_state_again, runtime)
    second_outcome = second.get("agent_output")
    assert second_outcome is not None
    assert second_outcome.hints_used - 1 == 1


# ---------------------------------------------------------------------------
# Graph contract: still compiles, same node/fallback names
# ---------------------------------------------------------------------------


def test_build_graph_still_compiles_with_matching_node_and_fallback_names() -> None:
    build_graph()
    assert set(NODE_FUNCTIONS) == set(FALLBACKS)


# ---------------------------------------------------------------------------
# End-to-end run_graph turn
# ---------------------------------------------------------------------------


async def test_run_graph_end_to_end_reaches_final_response_with_real_agent_output() -> None:
    fake = FakeLLMClient(chat_content=_DSA_INTENT_JSON)

    result = await run_graph(RawInput(text=_DSA_PROBLEM_TEXT), llm=fake)

    state = result.state
    assert state.route == "dsa"
    assert state.agent_output is not None
    assert "stub" not in state.agent_output.text.lower()
    assert state.response is not None
    assert "stub" not in state.response.lower()


# ---------------------------------------------------------------------------
# safe_node fallback preserved when a subgraph raises
# ---------------------------------------------------------------------------


async def test_dsa_agent_falls_back_when_subgraph_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def raising_run_dsa(*args: object, **kwargs: object) -> NoReturn:
        del args, kwargs
        raise RuntimeError("dsa-secret")

    monkeypatch.setattr("app.graph.nodes.run_dsa", raising_run_dsa)
    wrapped = safe_node("dsa_agent", dsa_agent, FALLBACKS["dsa_agent"])
    state = _pipeline_state(route_key="dsa", intent=Intent.DSA_HINT)

    update = await wrapped(state, runtime=_runtime())

    assert update.get("agent_output") is not None
    errors = update.get("errors")
    assert errors is not None and len(errors) == 1
    assert errors[0].node == "dsa_agent"
    assert "dsa-secret" not in errors[0].message


async def test_debug_agent_falls_back_when_subgraph_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def raising_run_debug(*args: object, **kwargs: object) -> NoReturn:
        del args, kwargs
        raise RuntimeError("debug-secret")

    monkeypatch.setattr("app.graph.nodes.run_debug", raising_run_debug)
    wrapped = safe_node("debug_agent", debug_agent, FALLBACKS["debug_agent"])
    state = _pipeline_state(route_key="debug", intent=Intent.CODE_DEBUG)

    update = await wrapped(state, runtime=_runtime())

    assert update.get("agent_output") is not None
    errors = update.get("errors")
    assert errors is not None and len(errors) == 1
    assert errors[0].node == "debug_agent"
    assert "debug-secret" not in errors[0].message


async def test_explain_agent_falls_back_when_explainer_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def raising_run_explain(*args: object, **kwargs: object) -> NoReturn:
        del args, kwargs
        raise RuntimeError("explain-secret")

    monkeypatch.setattr("app.graph.nodes.run_explain", raising_run_explain)
    wrapped = safe_node("explain_agent", explain_agent, FALLBACKS["explain_agent"])
    state = _pipeline_state(route_key="explain", intent=Intent.CODE_EXPLAIN)

    update = await wrapped(state, runtime=_runtime())

    assert update.get("agent_output") is not None
    errors = update.get("errors")
    assert errors is not None and len(errors) == 1
    assert errors[0].node == "explain_agent"
    assert "explain-secret" not in errors[0].message


async def test_explain_agent_falls_back_when_reviewer_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def raising_review_code(*args: object, **kwargs: object) -> NoReturn:
        del args, kwargs
        raise RuntimeError("review-secret")

    monkeypatch.setattr("app.graph.nodes.review_code", raising_review_code)
    wrapped = safe_node("explain_agent", explain_agent, FALLBACKS["explain_agent"])
    state = _pipeline_state(route_key="explain", intent=Intent.CODE_REVIEW)

    update = await wrapped(state, runtime=_runtime())

    assert update.get("agent_output") is not None
    errors = update.get("errors")
    assert errors is not None and len(errors) == 1
    assert errors[0].node == "explain_agent"
    assert "review-secret" not in errors[0].message
