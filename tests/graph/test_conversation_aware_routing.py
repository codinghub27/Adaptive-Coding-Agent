"""Round 2 (docs/AUDIT_REPORT.md, A-08/A-10/A-15, F1-F5): the classifier reads
the conversation, an explicit ask for code is never refused, and the debugger
still helps without a sandbox.

Graph-level tests replay turns on ONE conversation through the real graph
against the in-memory store from `test_conversation_regression`; the model is
a scripted double that answers the classifier WITH the conversation flags a
real model returns.
"""

import json
from collections.abc import Sequence
from typing import cast
from uuid import UUID, uuid4

import pytest
from langgraph.runtime import Runtime
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.debugger import annotate_code
from app.agents.planner import wants_the_code
from app.execution.synth import verified_reference
from app.graph import nodes
from app.graph.build import GraphRunResult, run_graph
from app.graph.nodes import classifier_context, resolve_problem_relation
from app.graph.routing import meta_followup
from app.graph.state import AgentState, GraphContext, RawInput
from app.graph.subgraphs.debug import run_debug
from app.input.intent import classify_intent
from app.input.snippet import line_offset, repair_snippet
from app.llm.base import ChatMessage, ChatResult
from app.memory.conversation import progress_payload
from app.memory.test_suites import subject_key, title_slug
from app.response.generate import generate_response
from app.schemas.agent_results import DebugResult
from app.schemas.conversation import MessageView
from app.schemas.execution import ExecutionRequest, ExecutionResult, TestCase, TestSuite, Verdict
from app.schemas.input import ActiveProblem, CodeBlock, StructuredInput
from app.schemas.intent import Intent, IntentResult
from app.schemas.plan import TeachingPlan
from app.schemas.tutoring import PendingCheck, SessionProgress
from tests.graph import test_conversation_regression as reg
from tests.graph.test_conversation_regression import (
    _Store,  # pyright: ignore[reportPrivateUsage]
    store,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from tests.input.fakes import FakeLLMClient

_P678 = reg._PROBLEM_678  # pyright: ignore[reportPrivateUsage]
_TWO_SUM = (
    "Two Sum\n\nGiven an array of integers nums and an integer target, return indices of the "
    "two numbers such that they add up to target.\n\n"
    "Example 1:\nInput: nums = [2,7,11,15], target = 9\nOutput: [0,1]"
)
_ATTEMPT = (
    "here is my attempt\n\n```python\ndef two_sum(nums, target):\n    seen = {}\n"
    "    for i, num in enumerate(nums):\n        if num in seen:\n"
    "            return [seen[num], i]\n        seen[num] = i\n    return []\n```"
)
_REFERENCE = (
    "def two_sum(nums, target):\n    seen = {}\n    for i, num in enumerate(nums):\n"
    "        if target - num in seen:\n            return [seen[target - num], i]\n"
    "        seen[num] = i\n    return []\n"
)

#: What a real model answers for each of these messages, flags included.
_READINGS: dict[str, dict[str, object]] = {
    # Measured live: the label that came with this was PRACTICE_REQUEST, and the
    # learner was handed a brand-new problem.
    "go back to the earlier one": {"intent": "PRACTICE_REQUEST", "earlier_subject": True},
    "what was that problem called again": {
        "intent": "GENERAL_GUIDANCE",
        "about_conversation": True,
        "refers_to_previous": True,
    },
    "here is my attempt": {"intent": "CODE_DEBUG", "refers_to_previous": True},
    "where's the mistake": {"intent": "CODE_DEBUG", "refers_to_previous": True},
    "write it out": {"intent": "DSA_SOLVE", "refers_to_previous": True, "asks_for_code": True},
    "help me get started": {"intent": "DSA_HINT", "asks_for_code": True},
}


class _ReadingLLM(reg._ScriptedLLM):  # pyright: ignore[reportPrivateUsage]
    def __init__(self) -> None:
        super().__init__()
        self.classifier_users: list[str] = []

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        system, user = messages[0].content, messages[-1].content
        content: str | None = None
        if system.startswith("You are the intent classifier"):
            self.classifier_users.append(user)
            question = str(json.loads(user.split("\n")[1]).get("question", "")).lower()
            for phrase, reading in _READINGS.items():
                if phrase in question:
                    flags = {
                        "refers_to_previous": False,
                        "earlier_subject": False,
                        "asks_for_code": False,
                        "about_conversation": False,
                    }
                    content = json.dumps({**flags, **reading, "confidence": 0.9})
        elif "test-synthesis engine" in system:
            content = json.dumps(
                {
                    "entrypoint": "two_sum",
                    "reference_solution": _REFERENCE,
                    "cases": [{"name": "a", "args": [[3, 3], 6], "kwargs": {}, "expected": [0, 1]}],
                }
            )
        elif "No failure has been established" in system or "that failure is FACT" in system:
            content = json.dumps(
                {"inferred_approach": "You are using a hash map.", "bug_explanation": "READ"}
            )
        if content is None:
            return await super().chat(messages, temperature=temperature, max_tokens=max_tokens)
        self.systems.append(system)
        return ChatResult(content=content, provider="fake", model="fake")


class _Chat:
    def __init__(self) -> None:
        self.llm = _ReadingLLM()
        self.user_id: UUID = uuid4()
        self.conversation_id: UUID = uuid4()

    async def turn(self, text: str) -> GraphRunResult:
        return await run_graph(
            RawInput(text=text),
            llm=self.llm,
            session=cast("AsyncSession", reg._Session()),  # pyright: ignore[reportPrivateUsage]
            user_id=self.user_id,
            conversation_id=self.conversation_id,
        )


def _reply(result: GraphRunResult) -> str:
    assert result.state.response is not None
    return result.state.response


# --- F1: the classifier is shown the conversation --------------------------------------


async def test_the_classifier_sees_the_subject_the_pending_question_and_the_messages(
    store: _Store,  # noqa: F811
) -> None:
    del store
    chat = _Chat()
    await chat.turn("how to solve this prob\n\n" + _P678)
    assert "<conversation_context>" not in chat.llm.classifier_users[0]  # a first message
    await chat.turn("what was that problem called again?")
    second = chat.llm.classifier_users[-1]
    assert second.split("\n")[0] == "<user_input>"  # the payload is still line 2
    assert "<conversation_context>" in second
    assert "active_subject: problem: 678. Valid Parenthesis String" in second
    assert "recent_messages:" in second
    assert "- user: how to solve this prob" in second


def test_classifier_context_is_short_and_none_on_a_first_message() -> None:
    assert classifier_context(AgentState(input=RawInput(text="hi"))) is None
    active = ActiveProblem(
        problem=StructuredInput(source="text", code=[CodeBlock(content="def f():\n    return 1")]),
        key="_c1",
    )
    state = AgentState(
        input=RawInput(text="x"),
        active_problem=active,
        pending_check=PendingCheck.model_validate(
            {
                "kind": "question",
                "question_id": "q",
                "question": "Which structure gives O(1) lookup?",
                "created_at": "2026-10-05T00:00:00Z",
            }
        ),
        recent_context=[
            MessageView.model_validate(
                {
                    "id": uuid4(),
                    "conversation_id": uuid4(),
                    "seq": 1,
                    "role": "user",
                    "content": "x" * 900,
                    "intent": None,
                    "created_at": "2026-10-05T00:00:00Z",
                }
            )
        ],
    )
    context = classifier_context(state)
    assert context is not None
    assert "active_subject: code the learner shared (starts: def f():)" in context
    assert "pending_question: Which structure gives O(1) lookup?" in context
    assert len(context) < 700  # every message is cut to a line


async def test_an_unsure_reading_carries_no_flags() -> None:
    inp = StructuredInput(source="text", question="write it out")

    def reply(confidence: float) -> FakeLLMClient:
        return FakeLLMClient(
            chat_content=json.dumps(
                {"intent": "DSA_SOLVE", "confidence": confidence, "asks_for_code": True}
            )
        )

    sure = await classify_intent(inp, reply(0.9), "active_subject: problem: Two Sum")
    unsure = await classify_intent(inp, reply(0.4), "active_subject: problem: Two Sum")
    assert sure.asks_for_code is True
    assert unsure.asks_for_code is None  # the phrase lists decide instead


def _intent(intent: Intent = Intent.DSA_SOLVE, **flags: bool | None) -> IntentResult:
    return IntentResult(
        intent=intent,
        confidence=0.9,
        source="llm",
        refers_to_previous=flags.get("refers_to_previous"),
        asks_for_code=flags.get("asks_for_code"),
        about_conversation=flags.get("about_conversation"),
    )


def test_the_models_code_ask_is_believed_only_for_a_plain_demand() -> None:
    asked = _intent(asks_for_code=True)
    assert wants_the_code(asked, "write it out")
    assert wants_the_code(asked, "show me the solution")
    # Measured live: a beginner's first message came back asks_for_code=true.
    assert not wants_the_code(asked, "Can you help me solve Two Sum? I don't understand it.")
    assert not wants_the_code(asked, "how to solve this prob")
    assert not wants_the_code(asked, "give me step by step to solve the valid parenthesis problem")
    # The model said no: the phrase list does not overrule it.
    assert not wants_the_code(_intent(asks_for_code=False), "don't give me the code yet")
    # No reading from the model: the phrase lists are the fallback.
    assert wants_the_code(_intent(), "give python code")
    assert not wants_the_code(_intent(), "write it out")
    assert wants_the_code(_intent(Intent.CODE_DEBUG), "fix this code", fix=True)


def test_a_question_about_the_chat_is_meta_by_the_models_reading() -> None:
    def state(question: str, **flags: bool | None) -> AgentState:
        return AgentState(
            input=RawInput(text=question),
            structured_input=StructuredInput(source="text", question=question),
            intent=_intent(Intent.GENERAL_GUIDANCE, **flags),
        )

    assert meta_followup(state("remind me what we were on")) is None  # no phrase list has it
    assert meta_followup(state("remind me what we were on", about_conversation=True)) == "name"
    # The model read it and said no: the phrase list is not consulted.
    assert meta_followup(state("tell me name of that problem", about_conversation=False)) is None


def test_the_models_reading_decides_whether_a_turn_is_a_followup() -> None:
    active = ActiveProblem(problem=StructuredInput(source="text", problem=_P678), key="_p1")
    names_a_pattern = StructuredInput(source="text", question="is this a greedy problem?")
    # Fallback: the corpus vocabulary reads "greedy" as a subject of its own.
    assert resolve_problem_relation(names_a_pattern, active)[0] == "none"
    assert resolve_problem_relation(names_a_pattern, active, refers=True)[0] == "followup"
    plain = StructuredInput(source="text", question="tell me about your day")
    assert resolve_problem_relation(plain, active, refers=False)[0] == "none"
    stuck = StructuredInput(source="text", question="I don't know")
    assert resolve_problem_relation(stuck, active, refers=False)[0] == "followup"


async def test_going_back_to_the_earlier_problem_restores_it(store: _Store) -> None:  # noqa: F811
    chat = _Chat()
    await chat.turn("how to solve this prob\n\n" + _P678)
    await chat.turn(_TWO_SUM + "\n\nhelp me get started")
    assert store.active is not None
    assert store.active.problem.problem is not None
    assert store.active.problem.problem.startswith("Two Sum")
    assert store.progress.earlier_problem is not None
    assert store.pending is not None  # Two Sum's question is open

    back = await chat.turn("go back to the earlier one")
    assert back.state.subject_switched
    assert back.state.route == "dsa"  # resumed, not graded as an answer, not a meta reply
    assert back.state.answer_grade is None
    assert store.active.problem.problem == _P678
    assert store.progress.earlier_problem is not None
    assert (store.progress.earlier_problem.problem.problem or "").startswith("Two Sum")


def test_stored_progress_reads_back_with_an_earlier_subject() -> None:
    """`is_empty` (computed) used to be dumped with the earlier subject; the
    next read rejected it and the WHOLE progress record came back empty."""
    earlier = ActiveProblem(problem=StructuredInput(source="text", problem=_P678), key="_p1")
    progress = SessionProgress(earlier_problem=earlier, last_topic="greedy")
    restored = SessionProgress.model_validate(progress_payload(progress))
    assert restored.earlier_problem == earlier
    assert restored.last_topic == "greedy"


async def test_an_attempt_is_judged_by_the_problems_examples_and_remembered(
    store: _Store,  # noqa: F811
) -> None:
    chat = _Chat()
    await chat.turn(_TWO_SUM + "\n\nhelp me get started")
    attempt = await chat.turn(_ATTEMPT)
    assert attempt.state.route == "debug"
    assert attempt.state.problem_relation == "same"  # code with no statement, about THIS problem
    assert attempt.state.structured_input is not None
    assert attempt.state.structured_input.problem == _TWO_SUM
    assert store.progress.last_attempt

    asked = await chat.turn("where's the mistake?")
    assert asked.state.route == "debug"
    assert asked.state.structured_input is not None
    assert asked.state.structured_input.code  # the attempt, not "no code was executed"
    assert "def two_sum" in asked.state.structured_input.code[0].content


# --- A-10: an explicit ask for the code is never refused -------------------------------


async def test_the_code_is_shown_unverified_and_says_so_when_nothing_can_run_it(
    store: _Store,  # noqa: F811
) -> None:
    del store
    chat = _Chat()  # no sandbox in this conversation
    await chat.turn(_TWO_SUM + "\n\nhelp me get started")
    result = await chat.turn("write it out")
    reply = _reply(result)
    generated = result.state.generated_response
    assert generated is not None
    assert generated.reveals_code
    assert "def two_sum" in reply
    assert "**Not verified in sandbox**" in reply
    assert "not available" in reply
    assert "passed every test case" not in reply


async def test_a_reference_is_never_called_verified_without_a_sandbox_pass() -> None:
    llm = _ReadingLLM()
    statement = StructuredInput(source="text", problem=_TWO_SUM, question="give the code")
    solution = await verified_reference(statement, llm, None)
    assert solution is not None
    assert solution.verified is False
    assert solution.request is None


# --- A-15: no sandbox -> static review + ONE model call ----------------------------------


async def test_without_a_sandbox_the_code_is_read_once_and_labelled_not_executed() -> None:
    llm = _ReadingLLM()
    code = "def f(items):\n    return items[len(items)]\n"
    state = AgentState(
        input=RawInput(text="why does this fail"),
        structured_input=StructuredInput(
            source="text", question="why does this fail", code=[CodeBlock(content=code)]
        ),
    )
    run = await run_debug(state, Runtime(context=GraphContext(llm=llm)))
    result = run.result
    assert len(llm.systems) == 1  # exactly one model call
    assert result.not_executed
    assert result.bug_explanation == "READ"
    assert result.patched_code is None
    assert result.fixed is False
    assert run.execution_request is None
    text = generate_response(result=result, plan=None).text
    assert "Not executed" in text


# --- F2: an ask for the code gets the code back --------------------------------------------


def test_the_learners_own_code_is_handed_back_with_its_label() -> None:
    plan = TeachingPlan(
        difficulty="medium",
        assistance_level="full",
        solution_strategy="guided_debugging",
        topic=None,
        skill_level=0.5,
    )
    passed = Verdict(status="pass", summary="all 2 case(s) passed", cases_passed=2, cases_total=2)
    result = DebugResult(
        initial_verdict=passed,
        presented_code="def f():\n    return 1\n",
        presented_label="Verified in sandbox: it passes the same 2/2 test cases.",
        presented_notes=["O(1) time."],
    )
    generated = generate_response(result=result, plan=plan)
    assert generated.reveals_code
    assert "## Your code" in generated.text
    assert "Verified in sandbox" in generated.text
    assert "def f():" in generated.text
    # Never below `full`: the plan, not the result, decides whether code shows.
    hinted = generate_response(
        result=result, plan=plan.model_copy(update={"assistance_level": "hint"})
    )
    assert "def f():" not in hinted.text


async def test_a_tidied_version_that_renames_the_function_is_rejected() -> None:
    problem = StructuredInput(
        source="text", code=[CodeBlock(content="def two_sum(nums, target):\n    return []\n")]
    )
    renamed = json.dumps({"commented_code": "def solve(nums, target):\n    return []\n"})
    kept = json.dumps({"commented_code": "def two_sum(nums, target):\n    # scan\n    return []\n"})
    assert await annotate_code(problem, FakeLLMClient(chat_content=renamed)) is None
    annotated = await annotate_code(problem, FakeLLMClient(chat_content=kept))
    assert annotated is not None
    assert "# scan" in annotated.code


# --- F3: the same subject is judged by the same cases ----------------------------------------


def test_subject_keys() -> None:
    assert title_slug(_P678) == "valid-parenthesis-string"
    assert title_slug("Given an array of integers nums, return the answer.") is None
    assert (
        subject_key(StructuredInput(source="text", problem=_P678)) == "t:valid-parenthesis-string"
    )
    prose = StructuredInput(source="text", problem="Given an array, return its maximum.")
    assert (subject_key(prose) or "").startswith("s:")
    code_only = StructuredInput(source="text", code=[CodeBlock(content="def f():\n    return 1")])
    assert (subject_key(code_only) or "").startswith("c:")
    assert subject_key(StructuredInput(source="text", question="hi")) is None


async def test_validated_cases_are_reused_and_the_examples_are_always_included(
    store: _Store,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del store
    cached = [TestCase(name="dup", args=[[3, 3], 6], expected=[0, 1])]
    saved: list[str] = []

    async def get_cached_cases(session: object, user_id: UUID, key: str) -> object:
        del session, user_id
        return (cached, "synthesised") if key == "t:two-sum" else None

    async def save_cached_cases(*args: object) -> None:
        saved.append(str(args[2]))

    monkeypatch.setattr(nodes, "get_cached_cases", get_cached_cases)
    monkeypatch.setattr(nodes, "save_cached_cases", save_cached_cases)

    class _Sandbox:
        def __init__(self) -> None:
            self.suites: list[TestSuite | None] = []

        async def run(self, request: ExecutionRequest) -> ExecutionResult:
            self.suites.append(request.tests)
            return ExecutionResult(status="completed")

    sandbox = _Sandbox()
    chat = _Chat()
    await run_graph(
        RawInput(text=_TWO_SUM + "\n\n" + _ATTEMPT.split("\n\n", 1)[1] + "\n\ndebug this"),
        llm=chat.llm,
        session=cast("AsyncSession", reg._Session()),  # pyright: ignore[reportPrivateUsage]
        user_id=chat.user_id,
        conversation_id=chat.conversation_id,
        runner=sandbox,
    )
    suite = sandbox.suites[0]
    assert suite is not None
    # The statement's own example first, then the validated case from the cache.
    assert len(suite.cases) == 2
    assert suite.cases[0].expected == [0, 1]
    assert 9 in [*suite.cases[0].args, *suite.cases[0].kwargs.values()]
    assert suite.cases[1].args == [[3, 3], 6]
    assert not any("test-synthesis engine" in system for system in chat.llm.systems)
    assert saved == []


# --- F5: line numbers are the learner's own ---------------------------------------------------


def test_line_offset_counts_the_lines_put_above_the_learners_code() -> None:
    body = "if not xs:\n        return 0\n    return xs[0]\n"
    assert line_offset(body, repair_snippet(body)) == 1  # a `def` header was added
    with_sample = "xs = [1]\n" + body
    assert line_offset(with_sample, repair_snippet(with_sample)) == 0  # header replaced a line
    runnable = "def f(xs):\n    return xs[0]\n"
    assert line_offset(runnable, repair_snippet(runnable)) == 0


async def test_a_crash_is_reported_on_the_line_the_learner_typed() -> None:
    body = "if not xs:\n        return 0\n    return xs[5]\n"  # the learner's line 3
    repaired = repair_snippet(body)

    class _Crash:
        async def run(self, request: ExecutionRequest) -> ExecutionResult:
            del request
            from app.schemas.execution import HarnessError

            return ExecutionResult(
                status="runtime_error",
                error=HarnessError(type="IndexError", message="x", lineno=4),  # repaired line 4
            )

    state = AgentState(
        input=RawInput(text="fix"),
        structured_input=StructuredInput(
            source="text",
            question="what is wrong",
            code=[CodeBlock(content=repaired, line_offset=line_offset(body, repaired))],
        ),
    )
    run = await run_debug(state, Runtime(context=GraphContext(llm=_ReadingLLM(), runner=_Crash())))
    assert run.result.failing_case is not None
    assert "line 3" in run.result.failing_case
    assert "line 4" not in run.result.failing_case
