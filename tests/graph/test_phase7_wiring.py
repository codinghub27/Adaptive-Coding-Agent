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
from collections.abc import Callable
from datetime import timedelta
from typing import Final, NoReturn

import pytest
from langgraph.runtime import Runtime
from pydantic import JsonValue
from sqlalchemy import func, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.hint_engine import HintProgress
from app.agents.reviewer import ReviewRunResult
from app.db.models import HintProgress as HintProgressRow
from app.execution.base import CodeRunner
from app.execution.synth import VerifiedSolution
from app.graph.build import NODE_FUNCTIONS, build_graph, run_graph
from app.graph.nodes import (
    DEFAULT_HINT_TOPIC,
    FALLBACKS,
    _problem_fingerprint,  # pyright: ignore[reportPrivateUsage]
    debug_agent,
    dsa_agent,
    explain_agent,
    resolve_hint_progress,
    safe_node,
    update_learner_model,
)
from app.graph.state import AgentState, GraphContext, RawInput
from app.graph.subgraphs.explain import ExplainRunResult
from app.llm.base import LLMClient
from app.memory.conversation import start_conversation
from app.memory.hint_progress import get_hint_progress, save_hint_progress
from app.schemas.agent_results import DebugResult, DSAResult, ExplainResult, HintLevel, ReviewResult
from app.schemas.execution import (
    CaseResult,
    ExecutionRequest,
    ExecutionResult,
    TestCase,
    TestSuite,
    Verdict,
)
from app.schemas.input import CodeBlock, StructuredInput
from app.schemas.intent import Intent, IntentResult
from app.schemas.plan import AssistanceLevel, TeachingPlan
from tests.graph.test_execute_verify_nodes import (
    FakeRunner,
    _sha256,  # pyright: ignore[reportPrivateUsage]
)
from tests.input.fakes import FakeLLMClient

_DSA_PROBLEM_TEXT: Final = (
    "Given an array of integers nums and an integer target, return indices "
    "of the two numbers such that they add up to target."
)
_DSA_PROBLEM_TEXT_B: Final = (
    "You are given the head of a singly linked list. Reverse the list, and "
    "return the reversed list's head."
)
_DSA_INTENT_JSON: Final = '{"intent": "DSA_HINT", "confidence": 0.9, "rationale": "clear"}'
_DSA_WORKED_EXAMPLE: Final = "\nExample 1:\nInput: nums = [2,7,11,15], target = 9\nOutput: [0,1]\n"


async def _hint_progress_row_count(session: AsyncSession, conversation_id: uuid.UUID) -> int:
    """How many `hint_progress` rows exist for this conversation (any user/topic)."""
    stmt = select(HintProgressRow).where(HintProgressRow.conversation_id == conversation_id)
    return len((await session.execute(stmt)).scalars().all())


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
    db_session: AsyncSession, user_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
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

    # ADAPTIVE-upgrade P4 (AD-4): a granted escalation reveals only the
    # sandbox-verified reference from `synth.verified_reference`; the solver's
    # own `{"code": "print(1)"}` is discarded. Stub the verified reference.
    verified = "def solve():\n    return 1\n"
    suite = TestSuite(entrypoint="solve", cases=[TestCase(name="c", args=[], expected=1)])
    solution = VerifiedSolution(
        code=verified,
        request=ExecutionRequest(code=verified, tests=suite),
        verdict=Verdict(status="pass", cases_passed=1, cases_total=1, summary="ok"),
    )

    async def _fake_reference(*args: object, **kwargs: object) -> VerifiedSolution:
        del args, kwargs
        return solution

    monkeypatch.setattr("app.graph.nodes.verified_reference", _fake_reference)

    update = await dsa_agent(
        state,
        _runtime(llm=llm, session=db_session, user_id=user_id, conversation_id=conversation_id),
    )

    request = update.get("execution_request")
    assert request is not None
    assert request.code == verified
    outcome = update.get("agent_output")
    assert outcome is not None
    assert outcome.needed_full_solution is True
    assert outcome.hints_used == 7


def _fake_extract_test_suite(
    suite: TestSuite | None,
) -> Callable[[StructuredInput | None], TestSuite | None]:
    def _extract(problem: StructuredInput | None) -> TestSuite | None:
        del problem
        return suite

    return _extract


def _dsa_passed_result(case_name: str, actual: JsonValue) -> ExecutionResult:
    return ExecutionResult(
        status="passed",
        phase="tests",
        cases=[
            CaseResult(
                name=case_name,
                passed=True,
                actual=actual,
                actual_repr=repr(actual),
                actual_sha256=_sha256(actual),
                duration_ms=1.0,
            )
        ],
    )


def _dsa_failed_result(case_name: str, wrong_actual: JsonValue) -> ExecutionResult:
    return ExecutionResult(
        status="failed",
        phase="tests",
        cases=[
            CaseResult(
                name=case_name,
                passed=False,
                actual=wrong_actual,
                actual_repr=repr(wrong_actual),
                actual_sha256=_sha256(wrong_actual),
                duration_ms=1.0,
            )
        ],
    )


async def test_dsa_agent_code_submitted_and_suite_passes_sets_solved_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A learner who submits code this turn gets it verified in the sandbox
    against a validated `TestSuite`, and a passing verdict becomes `solved`."""
    suite = TestSuite(entrypoint="two_sum", cases=[TestCase(name="c1", args=[], expected=[0, 1])])
    monkeypatch.setattr("app.graph.nodes.extract_test_suite", _fake_extract_test_suite(suite))
    code = "def two_sum(nums, target):\n    return [0, 1]\n"
    state = _pipeline_state(
        structured=StructuredInput(
            source="text",
            problem="Return indices of the two numbers that add up to target.",
            code=[CodeBlock(content=code)],
        ),
        plan=_plan(topic="arrays", assistance_level="hint"),
    )
    runner = FakeRunner(_dsa_passed_result("c1", [0, 1]))
    llm = FakeLLMClient(chat_content="{}")

    update = await dsa_agent(state, _runtime(llm=llm, runner=runner))

    assert update.get("suite_source") == "extracted"
    result = update.get("agent_result")
    assert isinstance(result, DSAResult)
    assert result.initial_verdict is not None
    assert result.initial_verdict.status == "pass"
    outcome = update.get("agent_output")
    assert outcome is not None
    assert outcome.solved is True
    request = update.get("execution_request")
    assert request is not None
    assert request.code == code
    assert runner.calls and runner.calls[0].tests == suite


async def test_dsa_agent_code_submitted_and_suite_fails_sets_solved_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same shape, but the sandbox reports a mismatched case -> `solved=False`."""
    suite = TestSuite(entrypoint="two_sum", cases=[TestCase(name="c1", args=[], expected=[0, 1])])
    monkeypatch.setattr("app.graph.nodes.extract_test_suite", _fake_extract_test_suite(suite))
    code = "def two_sum(nums, target):\n    return None\n"
    state = _pipeline_state(
        structured=StructuredInput(
            source="text",
            problem="Return indices of the two numbers that add up to target.",
            code=[CodeBlock(content=code)],
        ),
        plan=_plan(topic="arrays", assistance_level="hint"),
    )
    runner = FakeRunner(_dsa_failed_result("c1", None))
    llm = FakeLLMClient(chat_content="{}")

    update = await dsa_agent(state, _runtime(llm=llm, runner=runner))

    assert update.get("suite_source") == "extracted"
    result = update.get("agent_result")
    assert isinstance(result, DSAResult)
    assert result.initial_verdict is not None
    assert result.initial_verdict.status == "fail"
    outcome = update.get("agent_output")
    assert outcome is not None
    assert outcome.solved is False


async def test_dsa_agent_hint_only_turn_skips_synthesis_and_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No code submitted this turn -> no suite lookup at all: no synthesis
    LLM call, no sandbox run, and `solved` stays `None` (asking for a hint is
    exposure, never failure)."""

    async def fail_synthesize(*args: object, **kwargs: object) -> None:
        raise AssertionError("synthesis must never be attempted on a hint-only turn")

    monkeypatch.setattr("app.graph.nodes.synthesize_test_suite", fail_synthesize)
    state = _pipeline_state(
        structured=StructuredInput(
            source="text",
            problem="Given an array of integers, ...",
            question="can I get a hint?",
        ),
        plan=_plan(topic="arrays", assistance_level="hint"),
    )
    runner = FakeRunner()
    llm = FakeLLMClient(chat_content="{}")

    update = await dsa_agent(state, _runtime(llm=llm, runner=runner))

    assert update.get("suite_source") == "none"
    outcome = update.get("agent_output")
    assert outcome is not None
    assert outcome.solved is None
    assert "execution_request" not in update
    assert runner.calls == []
    assert len(llm.chat_calls) == 1


async def test_dsa_agent_no_suite_survives_solved_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Code submitted, but neither extraction nor synthesis produces a
    validated suite -> nothing ran, so `solved` stays `None`."""
    monkeypatch.setattr("app.graph.nodes.extract_test_suite", _fake_extract_test_suite(None))

    async def no_synthesis(
        problem: StructuredInput | None, llm: object, runner: object
    ) -> TestSuite | None:
        del problem, llm, runner
        return None

    monkeypatch.setattr("app.graph.nodes.synthesize_test_suite", no_synthesis)
    state = _pipeline_state(
        structured=StructuredInput(
            source="text",
            question="why is this wrong",
            code=[CodeBlock(content="def f():\n    return 1\n")],
        ),
        plan=_plan(topic="arrays", assistance_level="hint"),
    )
    runner = FakeRunner()
    llm = FakeLLMClient(chat_content="{}")

    update = await dsa_agent(state, _runtime(llm=llm, runner=runner))

    assert update.get("suite_source") == "none"
    outcome = update.get("agent_output")
    assert outcome is not None
    assert outcome.solved is None
    assert runner.calls == []


async def test_dsa_agent_synthesised_suite_without_problem_is_gated_by_learning_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A synthesised suite built from the learner's own (possibly buggy) code
    alone must never certify `solved` -- `_build_learning_event`'s gate
    applies to the DSA route exactly as it does to `debug_agent`, even
    though `dsa_agent` itself faithfully reports the sandbox's own verdict."""
    monkeypatch.setattr("app.graph.nodes.extract_test_suite", _fake_extract_test_suite(None))
    synthesised = TestSuite(entrypoint="f", cases=[TestCase(name="c1", args=[], expected=1)])

    async def fake_synthesize(
        problem: StructuredInput | None, llm: object, runner: object
    ) -> TestSuite | None:
        del problem, llm, runner
        return synthesised

    monkeypatch.setattr("app.graph.nodes.synthesize_test_suite", fake_synthesize)
    state = _pipeline_state(
        structured=StructuredInput(
            source="text",
            question="why is this wrong",
            code=[CodeBlock(content="def f():\n    return 1\n")],
        ),
        plan=_plan(topic="arrays", assistance_level="hint"),
    )
    runner = FakeRunner(_dsa_passed_result("c1", 1))
    llm = FakeLLMClient(chat_content="{}")

    dsa_update = await dsa_agent(state, _runtime(llm=llm, runner=runner))

    assert dsa_update.get("suite_source") == "synthesised"
    outcome = dsa_update.get("agent_output")
    assert outcome is not None
    assert outcome.solved is True  # dsa_agent's own faithful report

    merged_state = state.model_copy(update=dsa_update)
    learning_update = await update_learner_model(merged_state, _runtime())

    events = learning_update.get("events")
    assert events is not None and len(events) == 1
    assert events[0].solved is None


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


async def test_debug_agent_falls_back_to_synthesis_when_extraction_finds_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No worked examples for `extract_test_suite` to recover -> falls back to
    `synthesize_test_suite`, and `suite_source` records that it did."""
    synthesised = TestSuite(entrypoint="f", cases=[TestCase(name="c1", args=[], expected=1)])

    async def fake_synthesize(
        problem: StructuredInput | None, llm: object, runner: object
    ) -> TestSuite | None:
        del problem, llm, runner
        return synthesised

    monkeypatch.setattr("app.graph.nodes.synthesize_test_suite", fake_synthesize)
    state = _pipeline_state(
        route_key="debug",
        intent=Intent.CODE_DEBUG,
        structured=StructuredInput(
            source="text", question="why does this fail", code=[CodeBlock(content="def f(): 1/0")]
        ),
    )
    runner = FakeRunner(ExecutionResult(status="completed", phase="script"))
    llm = FakeLLMClient(chat_content="ok")

    update = await debug_agent(state, _runtime(llm=llm, runner=runner))

    assert update.get("suite_source") == "synthesised"
    assert runner.calls[0].tests == synthesised


async def test_debug_agent_suite_source_extracted_when_worked_examples_present() -> None:
    """Worked examples in the problem statement mean `extract_test_suite`
    succeeds, so synthesis is never reached and `suite_source` says so."""
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

    update = await debug_agent(state, _runtime(llm=llm, runner=runner))

    assert update.get("suite_source") == "extracted"


async def test_debug_agent_suite_source_none_when_nothing_found() -> None:
    """No worked examples and no runner (synthesis needs a sandbox) -> no
    suite at all, and `suite_source` says so."""
    state = _pipeline_state(
        route_key="debug",
        intent=Intent.CODE_DEBUG,
        structured=StructuredInput(
            source="text", question="why does this fail", code=[CodeBlock(content="def f(): 1/0")]
        ),
    )

    update = await debug_agent(state, _runtime())

    assert update.get("suite_source") == "none"


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

    cases: list[tuple[Intent, str | None]] = [
        (Intent.CODE_REVIEW, "review"),
        (Intent.OPTIMIZATION, "review"),
        (Intent.CODE_EXPLAIN, "explain"),
        # ADAPTIVE-upgrade P3: a concept question with no code is answered from
        # the corpus first (`_concept_answer`); this state has no topic and no
        # relevant hit, so there is nothing to ground on and it falls back to
        # the explainer (see test_concept.py for the grounded path).
        (Intent.CONCEPT_EXPLANATION, "explain"),
    ]
    for intent_value, expected in cases:
        calls.clear()
        state = _pipeline_state(route_key="explain", intent=intent_value)

        update = await explain_agent(state, _runtime())

        assert calls == ([expected] if expected is not None else [])
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


async def test_explain_agent_review_intent_falls_back_to_synthesis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The review branch resolves its `TestSuite` the same way `debug_agent`
    does: extraction first, synthesis only when extraction finds nothing."""
    synthesised = TestSuite(entrypoint="f", cases=[TestCase(name="c1", args=[], expected=1)])

    async def fake_synthesize(
        problem: StructuredInput | None, llm: object, runner: object
    ) -> TestSuite | None:
        del problem, llm, runner
        return synthesised

    monkeypatch.setattr("app.graph.nodes.synthesize_test_suite", fake_synthesize)
    code = "def f():\n    return 1\n"
    structured = StructuredInput(
        source="text", question="review this", code=[CodeBlock(content=code)]
    )
    state = _pipeline_state(
        route_key="explain",
        intent=Intent.CODE_REVIEW,
        structured=structured,
    )
    runner = FakeRunner(ExecutionResult(status="runtime_error", phase="tests"))
    llm = FakeLLMClient(chat_content='{"findings": []}')

    update = await explain_agent(state, _runtime(llm=llm, runner=runner))

    assert update.get("suite_source") == "synthesised"


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
    original bug, where every turn returned L0 again. `assistance_level`
    is `"partial"`, not `"full"`: `full` is Packet P3's escalation signal
    and jumps straight to its ceiling on the granting turn (see
    `next_hint`'s docstring), which would collapse this test's three
    distinct climbing steps into one."""
    conversation_id = await start_conversation(db_session, user_id)
    plan = _plan(topic="arrays", assistance_level="partial")
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
    hint-progress store (F8 regression guard). `assistance_level` is
    `"partial"`, not `"full"` -- see the sibling climbing test's docstring
    for why `full` would not exercise this scenario the same way."""
    conversation_id = await start_conversation(db_session, user_id)
    plan = _plan(topic="arrays", assistance_level="partial")
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
# Packet P3b regression: Cause A -- a verified attempt on a non-DSA-routed
# turn (`CODE_DEBUG`/`CODE_REVIEW`) must still count as "demonstrated
# effort" for the DSA escalation rule.
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_debug_agent_records_verified_attempt_for_dsa_ladder(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    """A learner whose sandbox-verified code got classified `CODE_DEBUG`, not
    a DSA intent, must still have that effort recorded against this
    problem's ladder -- only `dsa_agent` used to ever write
    `has_verified_attempt` (measured live: a `pass` verdict on a debug turn,
    then several `escalation_denied_...` turns that could never have been
    anything but denied)."""
    conversation_id = await start_conversation(db_session, user_id)
    runtime = _runtime(
        llm=FakeLLMClient(chat_content="{}"),
        session=db_session,
        user_id=user_id,
        conversation_id=conversation_id,
    )

    dsa_state = _pipeline_state(
        plan=_plan(topic="arrays", assistance_level="partial"),
        structured=StructuredInput(source="text", problem=_DSA_PROBLEM_TEXT),
    )
    await dsa_agent(dsa_state, runtime)
    before = await get_hint_progress(db_session, user_id, conversation_id, "arrays")
    assert before.has_verified_attempt is False

    statement = _DSA_PROBLEM_TEXT + _DSA_WORKED_EXAMPLE
    debug_state = _pipeline_state(
        route_key="debug",
        intent=Intent.CODE_DEBUG,
        plan=_plan(topic="arrays"),
        structured=StructuredInput(
            source="text",
            problem=statement,
            code=[CodeBlock(content="def two_sum(nums, target):\n    return None\n")],
        ),
    )
    debug_runtime = _runtime(
        llm=FakeLLMClient(chat_content="ok"),
        session=db_session,
        user_id=user_id,
        conversation_id=conversation_id,
        runner=FakeRunner(_dsa_failed_result("c1", None)),
    )
    debug_update = await debug_agent(debug_state, debug_runtime)
    result = debug_update.get("agent_result")
    assert isinstance(result, DebugResult)
    assert result.initial_verdict is not None
    assert result.initial_verdict.status == "fail"

    after = await get_hint_progress(db_session, user_id, conversation_id, "arrays")
    assert after.has_verified_attempt is True
    # Only `has_verified_attempt` moved -- the ladder's own level is untouched.
    assert after.last_level == before.last_level


@pytest.mark.db
async def test_review_agent_records_verified_attempt_for_dsa_ladder(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    """The same effort-recording as the debug turn above, but for
    `explain_agent`'s `CODE_REVIEW`/`OPTIMIZATION` dispatch to the reviewer,
    whose `ReviewResult.correctness_verdict` is likewise sandbox ground
    truth on the learner's own (never patched) submitted code."""
    conversation_id = await start_conversation(db_session, user_id)
    runtime = _runtime(
        llm=FakeLLMClient(chat_content="{}"),
        session=db_session,
        user_id=user_id,
        conversation_id=conversation_id,
    )

    dsa_state = _pipeline_state(
        plan=_plan(topic="arrays", assistance_level="partial"),
        structured=StructuredInput(source="text", problem=_DSA_PROBLEM_TEXT),
    )
    await dsa_agent(dsa_state, runtime)
    before = await get_hint_progress(db_session, user_id, conversation_id, "arrays")
    assert before.has_verified_attempt is False

    statement = _DSA_PROBLEM_TEXT + _DSA_WORKED_EXAMPLE
    review_state = _pipeline_state(
        route_key="explain",
        intent=Intent.CODE_REVIEW,
        plan=_plan(topic="arrays"),
        structured=StructuredInput(
            source="text",
            problem=statement,
            code=[CodeBlock(content="def two_sum(nums, target):\n    return None\n")],
        ),
    )
    review_runtime = _runtime(
        llm=FakeLLMClient(chat_content="ok"),
        session=db_session,
        user_id=user_id,
        conversation_id=conversation_id,
        runner=FakeRunner(_dsa_failed_result("c1", None)),
    )
    review_update = await explain_agent(review_state, review_runtime)
    result = review_update.get("agent_result")
    assert isinstance(result, ReviewResult)
    assert result.correctness_verdict is not None
    assert result.correctness_verdict.status == "fail"

    after = await get_hint_progress(db_session, user_id, conversation_id, "arrays")
    assert after.has_verified_attempt is True
    assert after.last_level == before.last_level


# ---------------------------------------------------------------------------
# Packet P3b regression: Cause B -- a bare follow-up's own turn-local,
# retrieval-inferred topic must never fork away from this conversation's
# already-established ladder.
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_unstable_topic_follow_up_continues_existing_ladder(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    """A bare follow-up (no problem statement of its own) whose OWN turn's
    topic came back different from history via `analyze_problem`'s
    "retrieval" fallback (`topic_source="retrieval"`) must still continue
    the conversation's existing ladder, not fork a fresh, empty one --
    `topic_source="retrieval"` is a turn-local, thin-prose guess, not a
    deliberate signal (see `_topic_is_stable`). Measured live: 6+
    consecutive "walk me through" turns all denied via
    `escalation_denied_ceiling_not_reached`, because every one of them
    forked its own empty ladder instead of continuing the real one."""
    conversation_id = await start_conversation(db_session, user_id)
    await save_hint_progress(db_session, user_id, conversation_id, "hashing", level=2, solved=False)

    state = _pipeline_state(
        plan=_plan(topic="two_pointers"),
        structured=StructuredInput(source="text", question="walk me through the approach"),
    ).model_copy(update={"topic_source": "retrieval"})
    ctx = GraphContext(
        llm=FakeLLMClient(), session=db_session, user_id=user_id, conversation_id=conversation_id
    )

    progress = await resolve_hint_progress(state, ctx)

    assert progress.last_level == HintLevel.L2_DATA_STRUCTURE
    assert await _hint_progress_row_count(db_session, conversation_id) == 1


@pytest.mark.db
async def test_stable_topic_follow_up_still_forks_its_own_ladder(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    """Control for the test above: when this turn's topic is a *deliberate*
    signal (`topic_source="profile_match"`, or untracked/`None` -- the
    historical default), a bare follow-up naming a genuinely different topic
    still gets its own fresh ladder, exactly as before Packet P3b."""
    conversation_id = await start_conversation(db_session, user_id)
    await save_hint_progress(db_session, user_id, conversation_id, "hashing", level=2, solved=False)

    state = _pipeline_state(
        plan=_plan(topic="two_pointers"),
        structured=StructuredInput(source="text", question="walk me through the approach"),
    ).model_copy(update={"topic_source": "profile_match"})
    ctx = GraphContext(
        llm=FakeLLMClient(), session=db_session, user_id=user_id, conversation_id=conversation_id
    )

    progress = await resolve_hint_progress(state, ctx)

    assert progress == HintProgress()


# ---------------------------------------------------------------------------
# DEFAULT_HINT_TOPIC last-resort fallback + problem-fingerprint keying
# (F-bugfix: a new problem in a topic-less turn no longer silently continues
# a previous, unrelated problem's ladder -- see `_problem_fingerprint` and
# `_hint_topic_key` in `app.graph.nodes`).
# ---------------------------------------------------------------------------


def test_default_hint_topic_fits_the_hint_progress_topic_column() -> None:
    """`hint_progress.topic` is `String(64)` in the DB (see
    `app.db.models.hint_progress.HintProgress`); `DEFAULT_HINT_TOPIC` must fit
    or every write keyed under it would silently fail (swallowed by
    `dsa_agent`'s `except Exception: pass`), which is worse than the bug this
    packet fixes."""
    assert len(DEFAULT_HINT_TOPIC) <= 64


@pytest.mark.db
async def test_dsa_agent_hint_level_climbs_via_problem_fingerprint_when_no_topic(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    """A learner whose plan never gets a topic (fresh profile, no
    `topic_hint`), restating the *same* problem each turn, still climbs the
    hint ladder turn over turn -- keyed under that problem's fingerprint, not
    the shared `DEFAULT_HINT_TOPIC` bucket (which stays untouched) -- and
    stops advancing once the ceiling for `"hint"` assistance
    (`L2_DATA_STRUCTURE`) is reached."""
    conversation_id = await start_conversation(db_session, user_id)
    plan = _plan(topic=None)
    structured = StructuredInput(source="text", problem=_DSA_PROBLEM_TEXT)
    runtime = _runtime(
        llm=FakeLLMClient(chat_content="{}"),
        session=db_session,
        user_id=user_id,
        conversation_id=conversation_id,
    )

    levels: list[int] = []
    for _ in range(3):
        state = _pipeline_state(plan=plan, structured=structured)
        update = await dsa_agent(state, runtime)
        outcome = update.get("agent_output")
        assert outcome is not None
        levels.append(outcome.hints_used - 1)

    assert levels == [0, 1, 2]

    fingerprint = _problem_fingerprint(structured)
    assert fingerprint is not None
    progress = await get_hint_progress(db_session, user_id, conversation_id, fingerprint)
    assert progress.last_level == HintLevel.L2_DATA_STRUCTURE
    default_progress = await get_hint_progress(
        db_session, user_id, conversation_id, DEFAULT_HINT_TOPIC
    )
    assert default_progress == HintProgress()


@pytest.mark.db
async def test_dsa_agent_two_different_problems_no_topic_get_independent_ladders(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    """Two *different* problem statements in one topic-less conversation get
    independent ladders: the second starts fresh at L0 while the first stays
    where it was -- this is the exact bug this packet fixes (a brand new
    problem no longer silently inherits a previous problem's rung)."""
    conversation_id = await start_conversation(db_session, user_id)
    plan = _plan(topic=None)
    runtime = _runtime(
        llm=FakeLLMClient(chat_content="{}"),
        session=db_session,
        user_id=user_id,
        conversation_id=conversation_id,
    )

    def _state(problem: str) -> AgentState:
        return _pipeline_state(
            plan=plan, structured=StructuredInput(source="text", problem=problem)
        )

    # Problem A climbs to L1 over two turns.
    await dsa_agent(_state(_DSA_PROBLEM_TEXT), runtime)
    second_a = await dsa_agent(_state(_DSA_PROBLEM_TEXT), runtime)
    second_a_outcome = second_a.get("agent_output")
    assert second_a_outcome is not None
    assert second_a_outcome.hints_used - 1 == 1

    # A brand new, unrelated problem B starts fresh at L0, not L2.
    first_b = await dsa_agent(_state(_DSA_PROBLEM_TEXT_B), runtime)
    first_b_outcome = first_b.get("agent_output")
    assert first_b_outcome is not None
    assert first_b_outcome.hints_used - 1 == 0

    # Problem A's ladder is untouched by B and resumes at L2.
    third_a = await dsa_agent(_state(_DSA_PROBLEM_TEXT), runtime)
    third_a_outcome = third_a.get("agent_output")
    assert third_a_outcome is not None
    assert third_a_outcome.hints_used - 1 == 2


@pytest.mark.db
async def test_dsa_agent_whitespace_and_case_differences_do_not_fork_the_ladder(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    """The same problem, re-pasted with different case/whitespace, stays on
    one ladder instead of forking a new one."""
    conversation_id = await start_conversation(db_session, user_id)
    plan = _plan(topic=None)
    runtime = _runtime(
        llm=FakeLLMClient(chat_content="{}"),
        session=db_session,
        user_id=user_id,
        conversation_id=conversation_id,
    )
    reworded = "  " + _DSA_PROBLEM_TEXT.upper().replace(" ", "   \n") + "  "

    first = await dsa_agent(
        _pipeline_state(
            plan=plan, structured=StructuredInput(source="text", problem=_DSA_PROBLEM_TEXT)
        ),
        runtime,
    )
    second = await dsa_agent(
        _pipeline_state(plan=plan, structured=StructuredInput(source="text", problem=reworded)),
        runtime,
    )

    first_outcome = first.get("agent_output")
    second_outcome = second.get("agent_output")
    assert first_outcome is not None
    assert second_outcome is not None
    assert first_outcome.hints_used - 1 == 0
    assert second_outcome.hints_used - 1 == 1
    assert await _hint_progress_row_count(db_session, conversation_id) == 1


@pytest.mark.db
async def test_dsa_agent_bare_followup_continues_latest_ladder_same_row(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    """A bare follow-up (no problem statement of its own, e.g. "Give me the
    next hint.") continues the most recently updated ladder in this
    conversation and writes back to that *same* row -- it must never fork a
    new one. `updated_at` is transaction-scoped `now()` (see
    `HintProgress.updated_at`), so within this test's single wrapped
    transaction the two problems' rows would otherwise tie on timestamp; the
    first row's `updated_at` is explicitly backdated to make "problem B is
    the most recent ladder" unambiguous, the way it naturally would be across
    two separate real requests."""
    conversation_id = await start_conversation(db_session, user_id)
    plan = _plan(topic=None)
    runtime = _runtime(
        llm=FakeLLMClient(chat_content="{}"),
        session=db_session,
        user_id=user_id,
        conversation_id=conversation_id,
    )

    await dsa_agent(
        _pipeline_state(
            plan=plan, structured=StructuredInput(source="text", problem=_DSA_PROBLEM_TEXT)
        ),
        runtime,
    )
    await db_session.execute(
        sa_update(HintProgressRow)
        .where(HintProgressRow.conversation_id == conversation_id)
        .values(updated_at=func.now() - timedelta(minutes=5))
    )

    await dsa_agent(
        _pipeline_state(
            plan=plan, structured=StructuredInput(source="text", problem=_DSA_PROBLEM_TEXT_B)
        ),
        runtime,
    )

    assert await _hint_progress_row_count(db_session, conversation_id) == 2

    followup_state = _pipeline_state(
        plan=plan, structured=StructuredInput(source="text", question="Give me the next hint.")
    )
    followup = await dsa_agent(followup_state, runtime)
    followup_outcome = followup.get("agent_output")
    assert followup_outcome is not None
    assert followup_outcome.hints_used - 1 == 1

    # Still exactly two rows: the follow-up updated problem B's row in place.
    assert await _hint_progress_row_count(db_session, conversation_id) == 2
    fingerprint_a = _problem_fingerprint(StructuredInput(source="text", problem=_DSA_PROBLEM_TEXT))
    fingerprint_b = _problem_fingerprint(
        StructuredInput(source="text", problem=_DSA_PROBLEM_TEXT_B)
    )
    assert fingerprint_a is not None
    assert fingerprint_b is not None
    progress_a = await get_hint_progress(db_session, user_id, conversation_id, fingerprint_a)
    progress_b = await get_hint_progress(db_session, user_id, conversation_id, fingerprint_b)
    assert progress_a.last_level == HintLevel.L0_NUDGE
    assert progress_b.last_level == HintLevel.L1_WHAT_TO_TRACK


@pytest.mark.db
async def test_dsa_agent_fingerprint_ladder_independent_across_conversations(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    """Two different conversations for the same user keep independent
    fingerprint-keyed ladders for the same problem text."""
    conversation_a = await start_conversation(db_session, user_id)
    conversation_b = await start_conversation(db_session, user_id)
    plan = _plan(topic=None)

    runtime_a = _runtime(
        llm=FakeLLMClient(chat_content="{}"),
        session=db_session,
        user_id=user_id,
        conversation_id=conversation_a,
    )
    runtime_b = _runtime(
        llm=FakeLLMClient(chat_content="{}"),
        session=db_session,
        user_id=user_id,
        conversation_id=conversation_b,
    )

    def _dsa_state() -> AgentState:
        return _pipeline_state(
            plan=plan, structured=StructuredInput(source="text", problem=_DSA_PROBLEM_TEXT)
        )

    # Advance conversation A twice; conversation B never touched.
    await dsa_agent(_dsa_state(), runtime_a)
    second_a = await dsa_agent(_dsa_state(), runtime_a)
    second_a_outcome = second_a.get("agent_output")
    assert second_a_outcome is not None
    assert second_a_outcome.hints_used - 1 == 1

    first_b = await dsa_agent(_dsa_state(), runtime_b)
    first_b_outcome = first_b.get("agent_output")
    assert first_b_outcome is not None
    assert first_b_outcome.hints_used - 1 == 0


@pytest.mark.db
async def test_dsa_agent_fingerprint_ladder_ownership_isolated_between_users(
    db_session: AsyncSession, user_id: uuid.UUID, other_user_id: uuid.UUID
) -> None:
    """Two different users cannot see each other's fingerprint-keyed
    progress, even under the same nominal `conversation_id`."""
    conversation_id = await start_conversation(db_session, user_id)
    plan = _plan(topic=None)

    runtime_user = _runtime(
        llm=FakeLLMClient(chat_content="{}"),
        session=db_session,
        user_id=user_id,
        conversation_id=conversation_id,
    )
    runtime_other = _runtime(
        llm=FakeLLMClient(chat_content="{}"),
        session=db_session,
        user_id=other_user_id,
        conversation_id=conversation_id,
    )

    def _dsa_state() -> AgentState:
        return _pipeline_state(
            plan=plan, structured=StructuredInput(source="text", problem=_DSA_PROBLEM_TEXT)
        )

    await dsa_agent(_dsa_state(), runtime_user)
    await dsa_agent(_dsa_state(), runtime_user)

    # The other user, same nominal conversation id, still starts fresh at L0.
    other_first = await dsa_agent(_dsa_state(), runtime_other)
    other_first_outcome = other_first.get("agent_output")
    assert other_first_outcome is not None
    assert other_first_outcome.hints_used - 1 == 0


@pytest.mark.db
async def test_dsa_agent_real_topic_keys_on_slug_tag_not_fallback(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    """A turn with a real `plan.topic` still keys/stores on
    `slug_tag(plan.topic)`, unaffected by the `DEFAULT_HINT_TOPIC` fallback
    or the problem-fingerprint scheme -- an explicit regression check on the
    stored row's `topic` column, and that no second, fingerprint-keyed row is
    ever created for the same turn."""
    conversation_id = await start_conversation(db_session, user_id)
    plan = _plan(topic="arrays")
    runtime = _runtime(
        llm=FakeLLMClient(chat_content="{}"),
        session=db_session,
        user_id=user_id,
        conversation_id=conversation_id,
    )
    structured = StructuredInput(source="text", problem=_DSA_PROBLEM_TEXT)
    state = _pipeline_state(plan=plan, structured=structured)

    await dsa_agent(state, runtime)

    stored = await get_hint_progress(db_session, user_id, conversation_id, "arrays")
    assert stored.last_level == HintLevel.L0_NUDGE
    fallback = await get_hint_progress(db_session, user_id, conversation_id, DEFAULT_HINT_TOPIC)
    assert fallback == HintProgress()
    fingerprint = _problem_fingerprint(structured)
    assert fingerprint is not None
    fingerprint_progress = await get_hint_progress(
        db_session, user_id, conversation_id, fingerprint
    )
    assert fingerprint_progress == HintProgress()
    assert await _hint_progress_row_count(db_session, conversation_id) == 1


@pytest.mark.db
async def test_no_topic_fingerprint_never_touches_an_unrelated_real_topic_row(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    """A no-topic turn's fingerprint-keyed write never collides with (or
    moves) a real, differently-named topic's row on the same conversation."""
    conversation_id = await start_conversation(db_session, user_id)
    runtime = _runtime(
        llm=FakeLLMClient(chat_content="{}"),
        session=db_session,
        user_id=user_id,
        conversation_id=conversation_id,
    )

    # A real "arrays" topic climbs once.
    arrays_state = _pipeline_state(
        plan=_plan(topic="arrays"),
        structured=StructuredInput(source="text", problem=_DSA_PROBLEM_TEXT),
    )
    await dsa_agent(arrays_state, runtime)

    # Two no-topic (fingerprint-keyed) turns on the same problem climb
    # independently of "arrays".
    no_topic_plan = _plan(topic=None)
    no_topic_structured = StructuredInput(source="text", problem=_DSA_PROBLEM_TEXT)
    for _ in range(2):
        no_topic_state = _pipeline_state(plan=no_topic_plan, structured=no_topic_structured)
        await dsa_agent(no_topic_state, runtime)

    arrays_progress = await get_hint_progress(db_session, user_id, conversation_id, "arrays")
    fingerprint = _problem_fingerprint(no_topic_structured)
    assert fingerprint is not None
    fingerprint_progress = await get_hint_progress(
        db_session, user_id, conversation_id, fingerprint
    )
    assert arrays_progress.last_level == HintLevel.L0_NUDGE
    assert fingerprint_progress.last_level == HintLevel.L1_WHAT_TO_TRACK


async def test_problem_fingerprint_never_appears_in_any_response_payload(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The fingerprint is a hash key for internal storage only -- it must
    never be rendered, logged, or returned to the client."""
    structured = StructuredInput(source="text", problem=_DSA_PROBLEM_TEXT)
    fingerprint = _problem_fingerprint(structured)
    assert fingerprint is not None

    state = _pipeline_state(plan=_plan(topic=None), structured=structured)
    with caplog.at_level("DEBUG"):
        update = await dsa_agent(state, _runtime(llm=FakeLLMClient(chat_content="{}")))

    outcome = update.get("agent_output")
    assert outcome is not None
    assert fingerprint not in outcome.text
    assert fingerprint not in repr(update)
    assert all(fingerprint not in record.getMessage() for record in caplog.records)


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
