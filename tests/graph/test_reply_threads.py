"""A follow-up belongs to the tutor's last reply, whatever that reply was.

Measured live (one conversation): a screenshot problem, then next day "give
roadmap to master stack,queue" -> a roadmap; "first where should i start" ->
Hint 2 of 4 on the problem from the day before; "im asking about the roadmap"
-> a brand-new 22-week plan for all of DSA, with "[14] Stack family" and
citation brackets printed in it.
"""

import json
from collections.abc import Sequence
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.concept import strip_markers
from app.graph import nodes
from app.graph.build import GraphRunResult, run_graph
from app.graph.state import RawInput
from app.llm.base import ChatMessage, ChatResult
from app.schemas.profile import LearnerProfileView
from tests.graph import test_conversation_regression as reg
from tests.graph.test_conversation_regression import (
    _Store,  # pyright: ignore[reportPrivateUsage]
    store,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)

_PROBLEM = "how to solve this prob\n\n" + reg._PROBLEM_678  # pyright: ignore[reportPrivateUsage]
_ROADMAP_ASK = "give roadmap to master stack,queue"

#: The classifier's answers, as a real model gives them. The two follow-ups
#: come back the way they did live: read as being about "the previous" thing,
#: under a hint label, with no word on WHICH previous thing.
_LABELS: dict[str, dict[str, object]] = {
    "how to solve this prob": {"intent": "DSA_SOLVE"},
    _ROADMAP_ASK: {"intent": "GENERAL_GUIDANCE"},
    "first where should i start": {"intent": "DSA_HINT", "refers_to_previous": True},
    "im asking about the roadmap": {"intent": "GENERAL_GUIDANCE", "refers_to_previous": True},
    "explain sliding window": {"intent": "CONCEPT_EXPLANATION"},
    "give another example": {"intent": "DSA_HINT", "continues_last_reply": True},
    "next hint on the parenthesis problem": {
        "intent": "DSA_HINT",
        "refers_to_previous": True,
        "continues_last_reply": False,
    },
}


class _LLM(reg._ScriptedLLM):  # pyright: ignore[reportPrivateUsage]
    def __init__(self) -> None:
        super().__init__()
        self.mentor_users: list[str] = []
        self.tutor_users: list[str] = []
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
            question = str(json.loads(user.split("\n")[1]).get("question", ""))
            for phrase, label in _LABELS.items():
                if phrase in question:
                    content = json.dumps({"confidence": 0.9, **label})
        elif system.startswith("You are a DSA mentor"):
            self.mentor_users.append(user)
            followup = "<conversation_so_far>" in user
            content = json.dumps(
                {
                    "answer": "Start with plain stack operations."
                    if followup
                    else "## Stacks and queues\n\nWeek 1: stacks [14] (Family [14]) 【1】.",
                    "used": [1],
                    "check": "Have you implemented a stack before? And a queue?",
                }
            )
        elif system.startswith("You are a DSA tutor"):
            self.tutor_users.append(user)
            content = json.dumps(
                {
                    "answer": "Another window example."
                    if "<conversation_so_far>" in user
                    else "A sliding window moves over the array.",
                    "check": "What leaves the window when it slides?",
                }
            )
        if content is None:
            return await super().chat(messages, temperature=temperature, max_tokens=max_tokens)
        self.systems.append(system)
        return ChatResult(content=content, provider="fake", model="fake")


class _Chat:
    def __init__(self) -> None:
        self.llm = _LLM()
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


async def test_a_followup_after_a_roadmap_is_about_the_roadmap(store: _Store) -> None:  # noqa: F811
    chat = _Chat()
    problem = await chat.turn(_PROBLEM)
    assert problem.state.route == "dsa"
    assert store.progress.last_thread == "problem"

    roadmap = await chat.turn(_ROADMAP_ASK)
    assert roadmap.state.route == "explain"
    assert store.progress.last_thread == "plan"
    solver_calls = chat.llm.solver_calls

    first = await chat.turn("first where should i start")
    # Not the next hint on the problem from before: a follow-up on the plan.
    assert first.state.route == "explain"
    assert first.state.thread_followup
    assert first.state.problem_relation == "none"
    assert chat.llm.solver_calls == solver_calls  # the hint ladder was not touched
    assert first.state.generated_response is not None
    assert first.state.generated_response.hint_level is None
    assert "Start with plain stack operations." in _reply(first)
    # ...and the mentor answered it WITH the roadmap in front of it.
    prompt = chat.llm.mentor_users[-1]
    assert "<conversation_so_far>" in prompt
    assert "learner: give roadmap to master stack,queue" in prompt
    assert "tutor: " in prompt
    assert "Stacks and queues" in prompt

    again = await chat.turn("im asking about the roadmap")
    assert again.state.route == "explain"
    assert again.state.thread_followup
    assert "learner: first where should i start" in chat.llm.mentor_users[-1]

    # The stored problem is still there, and saying so returns to it.
    assert store.active is not None
    back = await chat.turn("next hint on the parenthesis problem")
    assert back.state.route == "dsa"
    assert back.state.problem_relation == "followup"
    assert not back.state.thread_followup
    assert store.progress.last_thread == "problem"


async def test_the_classifier_is_told_what_the_last_reply_was(store: _Store) -> None:  # noqa: F811
    del store
    chat = _Chat()
    await chat.turn(_PROBLEM)
    await chat.turn(_ROADMAP_ASK)
    assert "last_reply: a step on the active_subject" in chat.llm.classifier_users[-1]
    await chat.turn("first where should i start")
    assert "last_reply: a study plan / roadmap" in chat.llm.classifier_users[-1]


async def test_a_followup_after_an_explanation_continues_it(store: _Store) -> None:  # noqa: F811
    chat = _Chat()
    await chat.turn(_PROBLEM)
    explained = await chat.turn("explain sliding window")
    assert store.progress.last_thread == "explanation"
    assert "**Your turn:** What leaves the window when it slides?" in _reply(explained)

    more = await chat.turn("give another example")
    assert more.state.route == "explain"
    assert more.state.thread_followup
    assert "Another window example." in _reply(more)
    assert "learner: explain sliding window" in chat.llm.tutor_users[-1]
    # A follow-up is not a fresh explanation: no "not from the curated material" footer.
    assert "Not from the curated material" not in _reply(more)


async def test_a_plan_ends_on_one_question_and_shows_no_reference_numbers(
    store: _Store,  # noqa: F811
) -> None:
    del store
    reply = _reply(await _Chat().turn(_ROADMAP_ASK))
    assert "[14]" not in reply
    assert "【" not in reply
    assert "Family" not in reply
    # The model offered two questions; the turn ends on the first.
    assert reply.rstrip().endswith("**Your turn:** Have you implemented a stack before?")


async def test_a_plan_is_built_for_this_learner(
    store: _Store,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del store

    async def get_profile(session: object, user_id: UUID) -> LearnerProfileView:
        del session, user_id
        return LearnerProfileView.empty().model_copy(
            update={
                "skill_levels": {"stack": 0.85, "bfs": 0.2, "hashing": 0.5, "family:graphs": 0.2},
                "common_errors": ["bfs.mark_visited_on_dequeue"],
            }
        )

    monkeypatch.setattr(nodes, "get_profile", get_profile)
    chat = _Chat()
    await chat.turn(_ROADMAP_ASK)
    prompt = chat.llm.mentor_users[-1]
    assert "<learner_profile>" in prompt
    assert "weak in: bfs" in prompt
    assert "strong in: stack" in prompt
    assert "recurring mistakes: bfs.mark_visited_on_dequeue" in prompt
    profile = prompt.split("<learner_profile>")[1].split("</learner_profile>")[0]
    assert "family:graphs" not in profile  # an internal aggregate, not a topic
    assert "hashing" not in profile  # still at the prior: no evidence either way


async def test_a_new_learners_plan_carries_no_invented_profile(store: _Store) -> None:  # noqa: F811
    del store
    chat = _Chat()
    await chat.turn(_ROADMAP_ASK)
    assert "<learner_profile>" not in chat.llm.mentor_users[-1]


def test_reference_numbers_are_stripped_but_list_literals_are_not() -> None:
    assert strip_markers("Stacks [14] first.") == "Stacks first."
    assert strip_markers("See Family [17] and family [3, 4].") == "See and."
    assert strip_markers("It is linear【4】.") == "It is linear."
    assert strip_markers("Take the array [2, 1, 5] and nums[0].") == (
        "Take the array [2, 1, 5] and nums[0]."
    )
    assert strip_markers("Week 1\\\n- Day 1\\ \nDone") == "Week 1\n- Day 1\nDone"
