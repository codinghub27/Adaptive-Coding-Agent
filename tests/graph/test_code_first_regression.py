"""The code-first session (docs/current-behavior.md) replayed on ONE conversation.

The learner pasted a Trapping Rain Water body with no problem statement and
asked for "the correct code". Measured live: Hint 1 of 4 (the code was never
read), then a pattern survey around a refusal, then Hint 2 for "give python
code", then a greeting for "i asked for python code", and finally an
indentation complaint instead of the real bug.

Every turn runs through the real graph against the in-memory conversation
store from `test_conversation_regression`; the sandbox is a scripted
`CodeRunner` so the replay needs no Docker.
"""

import json
from collections.abc import Sequence
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.planner import asks_for_fix, explicit_ask_phrase
from app.graph import nodes
from app.graph.build import GraphRunResult, run_graph
from app.graph.routing import meta_followup
from app.graph.state import AgentState, RawInput
from app.llm.base import ChatMessage, ChatResult
from app.response.format import render_execution_line
from app.response.generate import generate_response
from app.schemas.agent_results import DebugResult
from app.schemas.execution import ExecutionRequest, ExecutionResult, HarnessError, Verdict
from app.schemas.input import StructuredInput
from app.schemas.intent import Intent
from app.schemas.plan import TeachingPlan
from app.schemas.profile import LearnerProfileView
from app.tutoring.grader import asks_for_help
from tests.graph import test_conversation_regression as reg
from tests.graph.test_conversation_regression import (
    _Store,  # pyright: ignore[reportPrivateUsage]
    store,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)

_BODY = (
    "if not height:\n"
    "            return 0\n\n"
    "        left, right = 0, len(height) - 1\n"
    "        left_max, right_max = 0, 0\n"
    "        trapped_water = 0\n\n"
    "        while left < right:\n"
    "            if height[left] < height[right]:\n"
    "                if height[left] >= left_max:\n"
    "                    left_max = height[left]\n"
    "                else:\n"
    "                    trapped_water += left_max - height[left]\n"
    "                left {left}= 1\n"
    "            else:\n"
    "                if height[right] >= right_max:\n"
    "                    right_max = height[right]\n"
    "                else:\n"
    "                    trapped_water += right_max - height[right]\n"
    "                right {right}= 1\n\n"
    "        return trapped_water\n"
)
_SAMPLE = "height = [0,1,0,2,1,0,1,3,2,1,2,1]\n"
_C1 = (
    "give correct code of this.\n"
    + _SAMPLE
    + "\n```python\n"
    + _BODY.format(left="+", right="-")
    + "```"
)
_C5 = "fix this code.\n" + _SAMPLE + _BODY.format(left="-", right="+")

#: What the live classifier stored for each turn (`messages.intent`).
_LIVE_INTENTS: dict[str, Intent] = {
    "give correct code of this": Intent.DSA_SOLVE,
    "give full code and tell me": Intent.DSA_SOLVE,
    "give python code": Intent.DSA_SOLVE,
    "i asked for python code": Intent.GENERAL_GUIDANCE,
    "fix this code": Intent.CODE_DEBUG,
}


class _DebugLLM(reg._ScriptedLLM):  # pyright: ignore[reportPrivateUsage]
    """The regression double, plus the debugger's three prompts."""

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
            question = str(json.loads(user.split("\n")[1]).get("question", ""))
            for phrase, intent in _LIVE_INTENTS.items():
                if phrase in question:
                    content = json.dumps({"intent": intent.value, "confidence": 0.9})
        elif "infer, in one or two sentences" in system:
            content = json.dumps({"inferred_approach": "You are moving two pointers inward."})
        elif "that failure is FACT" in system:
            content = json.dumps({"bug_explanation": "`left -= 1` moves left off the array."})
        elif "No failure has been established" in system:
            content = json.dumps({"bug_explanation": "I traced [0, 1, 0, 2] and found no bug."})
        elif "Produce a corrected, complete, runnable" in system:
            code = str(json.loads(user.split("\n")[1])["code"][0]["content"])
            fixed = code.replace("left -= 1", "left += 1").replace("right += 1", "right -= 1")
            content = json.dumps({"patched_code": fixed})
        if content is None:
            return await super().chat(messages, temperature=temperature, max_tokens=max_tokens)
        self.systems.append(system)
        return ChatResult(content=content, provider="fake", model="fake")

    def used(self, marker: str) -> bool:
        return any(marker in system for system in self.systems)


class _ScriptedSandbox:
    """Runs nothing: the buggy body crashes, anything else completes."""

    def __init__(self) -> None:
        self.ran: list[str] = []

    async def run(self, request: ExecutionRequest) -> ExecutionResult:
        self.ran.append(request.code)
        if "left -= 1" in request.code:
            return ExecutionResult(
                status="runtime_error",
                error=HarnessError(type="IndexError", message="list index", lineno=10),
            )
        return ExecutionResult(status="completed")


class _Session:
    def __init__(self) -> None:
        self.llm = _DebugLLM()
        self.sandbox = _ScriptedSandbox()
        self.user_id: UUID = uuid4()
        self.conversation_id: UUID = uuid4()

    async def turn(self, text: str, *, topic: str | None = None) -> GraphRunResult:
        return await run_graph(
            RawInput(text=text, topic_hint=topic),
            llm=self.llm,
            session=cast("AsyncSession", reg._Session()),  # pyright: ignore[reportPrivateUsage]
            user_id=self.user_id,
            conversation_id=self.conversation_id,
            runner=self.sandbox,
        )


def _reply(result: GraphRunResult) -> str:
    assert result.state.response is not None
    return result.state.response


async def test_pasted_code_is_read_and_stays_the_subject(store: _Store) -> None:  # noqa: F811
    chat = _Session()

    # C1: code + "give correct code of this" -> the code is read and run.
    c1 = await chat.turn(_C1)
    assert c1.state.route == "debug"
    assert c1.state.intent is not None
    assert c1.state.intent.intent is Intent.CODE_DEBUG
    assert c1.state.generated_response is not None
    assert c1.state.generated_response.hint_level is None  # not the hint ladder
    # The ragged paste was made runnable before anything looked at it.
    assert chat.sandbox.ran[0].startswith("def solve(height):\n    if not height:")
    assert "syntax error" not in _reply(c1)
    # Working code is not told it is broken.
    assert chat.llm.used("No failure has been established")
    assert not chat.llm.used("that failure is FACT")
    assert "found no bug" in _reply(c1)
    # ... and an unproven run (no test cases here) is not called a pass either.
    assert "passed every test case" not in _reply(c1)
    assert "not verified" in _reply(c1)
    # The code is now what this conversation is about.
    assert store.active is not None
    assert store.active.key.startswith("_c")
    assert store.active.problem.code

    # C2-C4: follow-ups with no code of their own are about THAT code.
    for text in (
        "give full code and tell me where is the bug",
        "give python code",
        "i asked for python code",
    ):
        result = await chat.turn(text)
        assert result.state.route == "debug", text
        assert result.state.problem_relation == "followup", text
        assert result.state.structured_input is not None
        assert result.state.structured_input.code, text
        assert result.state.plan is not None
        assert "fix_requested" in result.state.plan.rationale, text
        reply = _reply(result)
        assert not reply.startswith("Hi!"), text
        assert "hint" not in reply.lower(), text
        assert "couldn't verify" not in reply, text
    assert not chat.llm.asked_for_a_study_plan()
    assert chat.llm.solver_calls == 0  # the hint ladder was never involved


async def test_the_real_bug_is_found_and_the_fix_is_shown_as_unverified(
    store: _Store,  # noqa: F811
) -> None:
    del store
    chat = _Session()
    c5 = await chat.turn(_C5)
    reply = _reply(c5)

    assert c5.state.route == "debug"
    # The sample-input line became a default, so the body ran as pasted.
    assert chat.sandbox.ran[0].startswith("def solve(height=[0, 1, 0, 2, 1, 0, 1, 3, 2, 1, 2, 1]):")
    assert "IndentationError" not in reply
    assert "syntax error" not in reply
    assert "IndexError" in reply
    assert "`left -= 1`" in reply  # the exact line, from the failure-grounded prompt
    assert chat.llm.used("that failure is FACT")

    generated = c5.state.generated_response
    assert generated is not None
    assert generated.reveals_code  # "fix this code" gets the fix
    assert "left += 1" in reply
    # Executed is not verified, and the reply says which one it is.
    assert "executed, not verified" in reply
    assert "0/0" not in reply
    assert "verified in the sandbox:" not in reply
    assert c5.state.verification is not None
    assert c5.state.verification.status == "inconclusive"
    assert all(event.solved is not True for event in c5.state.events)


async def test_challenge_mode_keeps_the_fix_hidden(store: _Store) -> None:  # noqa: F811
    del store
    chat = _Session()
    result = await run_graph(
        RawInput(text=_C5, teaching_mode="challenge"),
        llm=chat.llm,
        session=cast("AsyncSession", reg._Session()),  # pyright: ignore[reportPrivateUsage]
        user_id=chat.user_id,
        conversation_id=chat.conversation_id,
        runner=chat.sandbox,
    )
    generated = result.state.generated_response
    assert generated is not None
    assert not generated.reveals_code
    assert "left += 1" not in _reply(result)
    assert "IndexError" in _reply(result)  # the diagnosis is still given


async def test_example_input_alone_is_not_the_learners_code(store: _Store) -> None:  # noqa: F811
    """`nums = [2, 7, 11, 15]` parses as Python; it is the problem's example."""
    del store
    chat = _Session()
    result = await chat.turn(reg._TWO_SUM_BEGINNER)  # pyright: ignore[reportPrivateUsage]
    assert result.state.route == "dsa"


async def test_a_reveal_with_no_code_produced_is_one_honest_note_not_a_survey(
    store: _Store,  # noqa: F811
) -> None:
    del store
    chat = reg._Conversation()  # pyright: ignore[reportPrivateUsage]
    await chat.turn(reg._PARTITION_LABELS)  # pyright: ignore[reportPrivateUsage]
    result = await chat.turn("give python code")  # no sandbox here: nothing can be verified
    assert result.state.plan is not None
    assert result.state.plan.assistance_level == "full"
    generated = result.state.generated_response
    assert generated is not None
    assert [section.kind for section in generated.sections] == ["next_hint"]
    assert "did not return usable code" in _reply(result)
    assert "Understanding the problem" not in _reply(result)


async def test_profile_from_earlier_sessions_changes_the_plan(
    store: _Store,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cross-session: the same ask in a NEW conversation is planned from what
    the learner's profile recorded before it."""
    del store
    levels: dict[str, float] = {}

    async def get_profile(session: object, user_id: UUID) -> LearnerProfileView:
        del session, user_id
        return LearnerProfileView.empty().model_copy(update={"skill_levels": dict(levels)})

    monkeypatch.setattr(nodes, "get_profile", get_profile)
    ask = "how to solve this prob\n\n" + reg._PARTITION_LABELS  # pyright: ignore[reportPrivateUsage]

    levels["greedy"] = 0.2
    weak = await _Session().turn(ask, topic="greedy")
    levels["greedy"] = 0.9
    strong = await _Session().turn(ask, topic="greedy")

    assert weak.state.plan is not None
    assert strong.state.plan is not None
    assert "weak_skill" in weak.state.plan.rationale
    assert weak.state.plan.assistance_level == "hint"
    assert weak.state.plan.difficulty == "easy"
    assert "strong_skill" in strong.state.plan.rationale
    assert strong.state.plan.assistance_level != "hint"  # one level more help
    assert strong.state.plan.concise
    assert strong.state.plan.difficulty == "hard"


# --- the phrase lists ----------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "give python code",
        "give correct code of this.",
        "i asked for python code",
        "I want the full code",
        "show me the working solution",
        "give full code and tell me where is the bug",
    ],
)
def test_plain_asks_for_the_code_are_explicit(text: str) -> None:
    assert explicit_ask_phrase(text)
    assert asks_for_fix(text)
    assert asks_for_help(text)  # a request, never an answer to grade


@pytest.mark.parametrize(
    "text",
    [
        "give me a hint about the code",
        "can you review this code",
        "I want to write the code myself",
        "I need help with my code",
        "show my code some love",
        "what does this code do",
        "show me how the code works",
        "show me where the code fails",
    ],
)
def test_other_sentences_about_code_are_not_asks_for_the_solution(text: str) -> None:
    assert not explicit_ask_phrase(text)


@pytest.mark.parametrize(
    "text",
    [
        "is my code correct for this input",
        "is it correct that the loop stops early",
        "is this the right solution",
    ],
)
def test_asking_whether_code_is_right_is_not_asking_for_the_fix(text: str) -> None:
    assert not asks_for_fix(text)


@pytest.mark.parametrize(
    "answer", ["we need to store the answer", "I want to return the answer", "a hash map"]
)
def test_an_answer_that_mentions_the_answer_is_still_graded(answer: str) -> None:
    assert not asks_for_help(answer)


def test_nothing_to_fix_is_said_only_when_the_code_passed() -> None:
    plan = TeachingPlan(
        difficulty="medium",
        assistance_level="full",
        solution_strategy="guided_debugging",
        topic=None,
        skill_level=0.5,
    )
    passed = Verdict(status="pass", summary="all 2 case(s) passed", cases_passed=2, cases_total=2)
    unproven = Verdict(status="inconclusive", category="no_tests", summary="ran cleanly")
    said = generate_response(result=DebugResult(initial_verdict=passed), plan=plan).text
    unsaid = generate_response(
        result=DebugResult(initial_verdict=unproven, bug_explanation="`i < n` should be `i <= n`."),
        plan=plan,
    ).text
    assert "there was nothing to fix" in said
    assert "there was nothing to fix" not in unsaid


@pytest.mark.parametrize(
    "text", ["fix this code.", "can you correct it", "where is the fixed code"]
)
def test_fix_asks(text: str) -> None:
    assert asks_for_fix(text)


def _state_asking(question: str) -> AgentState:
    return AgentState(
        input=RawInput(text=question),
        structured_input=StructuredInput(source="text", question=question),
    )


@pytest.mark.parametrize(
    ("question", "kind"),
    [
        ("tell me name of that problem", "name"),
        ("what's its name", "name"),
        ("what is that problem called", "name"),
        ("can you see previous messages", "history"),
        ("so you can't read previous conversations?", "history"),
    ],
)
def test_vague_followups_about_the_conversation_are_meta(question: str, kind: str) -> None:
    assert meta_followup(_state_asking(question)) == kind


@pytest.mark.parametrize(
    "question", ["what's the name of the algorithm for this", "explain that problem", "next hint"]
)
def test_questions_about_the_solution_are_not_meta(question: str) -> None:
    assert meta_followup(_state_asking(question)) is None


def test_a_crash_before_any_case_is_not_reported_as_a_score() -> None:
    crashed = Verdict(
        status="fail", category="runtime_error", summary="runtime error IndexError on line 10"
    )
    line = render_execution_line(crashed, " (your code)")
    assert "0/0" not in line
    assert "before any test case ran" in line
    assert "IndexError" in line
