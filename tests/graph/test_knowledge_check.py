"""A knowledge check is asked, answered, marked and recorded.

Owner's conversation, 2026-10-12: "ask me 10 dsa basic question to test how
much knowledge i have" got one medium graph problem, and in a fresh
conversation "Tell me which pattern you want to practise", five times, word
for word. Nothing reached the learner's profile.
"""

import json
from collections.abc import Sequence
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.assess import (
    MarkedQuiz,
    builtin_quiz,
    quiz_topics,
    render_marks,
    render_quiz,
    requested_count,
)
from app.graph import nodes
from app.graph.build import GraphRunResult, run_graph
from app.graph.state import RawInput
from app.llm.base import ChatMessage, ChatResult, LLMError
from app.memory.profile import CONCEPT_CEILING, FAMILY_PREFIX, PRIOR, apply_event
from app.schemas.event import LearningEventCreate
from app.schemas.intent import Intent
from app.schemas.tutoring import QuizItem, QuizState
from tests.graph import test_conversation_regression as reg
from tests.graph.test_conversation_regression import (
    _Store,  # pyright: ignore[reportPrivateUsage]
    store,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)

_ASK = "ask me 10 dsa basic question to test how much knowledge i have"
_ANSWERS = "1. O(1) because it is by index\n2. skip\n3. it must be sorted"
_REDIRECT = "start from basic not directly in DSA"

_WRITTEN = [
    {"question": "What is the cost of reading index i of an array?", "topic": "arrays",
     "expected": "O(1): the position is computed from the index."},
    {"question": "What two things does a recursive function need?", "topic": "recursion",
     "expected": "A base case and a step toward it."},
    {"question": "What must hold before binary search works?", "topic": "binary_search",
     "expected": "The input must be sorted."},
    # A tag outside the closed vocabulary: the question is dropped, never
    # stored as a skill key.
    {"question": "What is a monad?", "topic": "category_theory", "expected": "Not this."},
]  # fmt: skip
_BASICS = [
    {"question": "What does `x = [1]; y = x; y.append(2); print(x)` print?",
     "topic": "python_basics", "expected": "[1, 2]: both names refer to one list."},
]  # fmt: skip

_LABELS: dict[str, dict[str, object]] = {
    _ASK: {"intent": "KNOWLEDGE_CHECK"},
    "1. O(1)": {"intent": "DSA_HINT", "learner_showed": "partially_correct"},
    _REDIRECT: {"intent": "KNOWLEDGE_CHECK", "continues_last_reply": True},
    "explain binary search": {"intent": "CONCEPT_EXPLANATION"},
}


class _LLM(reg._ScriptedLLM):  # pyright: ignore[reportPrivateUsage]
    def __init__(self) -> None:
        super().__init__()
        self.writer_users: list[str] = []
        self.marker_users: list[str] = []
        self.classifier_users: list[str] = []
        self.write_fails = False
        self.mark_fails = False

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
        elif system.startswith("You write a short knowledge check"):
            self.writer_users.append(user)
            if self.write_fails:
                raise LLMError("rate limited")
            basics = "learner: " + _ASK in user  # a correction of the first set
            content = json.dumps({"questions": _BASICS if basics else _WRITTEN})
        elif system.startswith("You mark a learner's answers"):
            self.marker_users.append(user)
            if self.mark_fails:
                raise LLMError("rate limited")
            content = json.dumps(
                {
                    "marks": [
                        {"n": 1, "verdict": "correct", "feedback": "Right, direct indexing."},
                        {"n": 2, "verdict": "dont_know", "feedback": ""},
                        {"n": 3, "verdict": "correct", "feedback": "Yes, sorted."},
                        {"n": 9, "verdict": "correct", "feedback": "No such question."},
                    ]
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


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch, store: _Store) -> list[LearningEventCreate]:  # noqa: F811
    """Learning events the graph asked to have recorded, in order."""
    del store
    events: list[LearningEventCreate] = []

    class _Recorded:
        applied = True

        def __init__(self, event: LearningEventCreate) -> None:
            self.event = type("_Row", (), {"id": event.event_id})()

    async def record_event(session: Any, user_id: UUID, event: LearningEventCreate) -> Any:
        del session, user_id
        events.append(event)
        return _Recorded(event)

    monkeypatch.setattr(nodes, "record_event", record_event)
    return events


async def test_asking_to_be_tested_gets_questions(store: _Store) -> None:  # noqa: F811
    chat = _Chat()
    asked = await chat.turn(_ASK)

    assert asked.state.route == "assess"
    assert asked.state.decision is not None
    assert asked.state.decision.move == "assess"
    reply = _reply(asked)
    assert "1. What is the cost of reading index i of an array?" in reply
    assert "3. What must hold before binary search works?" in reply
    # The out-of-vocabulary question was dropped, and no key is shown.
    assert "monad" not in reply
    assert "computed from the index" not in reply
    assert "which pattern you want to practise" not in reply
    assert store.progress.quiz is not None
    assert [item.topic for item in store.progress.quiz.items] == [
        "arrays",
        "recursion",
        "binary_search",
    ]
    assert store.progress.last_thread == "quiz"
    # The writer was told how many, and what the learner asked for.
    assert "<count>10</count>" in chat.llm.writer_users[-1]
    assert _ASK in chat.llm.writer_users[-1]
    # Asking is not evidence of anything.
    assert not asked.state.events


async def test_the_answers_are_marked_and_recorded(
    store: _Store,  # noqa: F811
    recorded: list[LearningEventCreate],
) -> None:
    chat = _Chat()
    await chat.turn(_ASK)
    # The ask itself is read by rule: it does not wait on the classifier.
    assert not chat.llm.classifier_users

    marked = await chat.turn(_ANSWERS)

    # The classifier was told a quiz was waiting.
    assert (
        "pending_question: (the tutor asked a set of numbered quiz"
        in (chat.llm.classifier_users[-1])
    )
    assert marked.state.route == "assess"
    assert marked.state.decision is not None
    assert marked.state.decision.subject == "last_reply"
    reply = _reply(marked)
    assert "You got **2 of 3** right." in reply
    # The marker opened with "Right,": the verdict is not said twice.
    assert "1. Correct. Direct indexing." in reply
    # A skipped question is answered from its key.
    assert "2. Skipped. A base case and a step toward it." in reply
    assert "You're solid on arrays and binary search." in reply
    assert "Recursion needs work." in reply
    # The marker saw the keys and the learner's reply as data.
    assert "key: The input must be sorted." in chat.llm.marker_users[-1]
    assert "<learner_reply>" in chat.llm.marker_users[-1]

    # One event per answered question, on its own topic, soft evidence.
    assert [(e.topic, e.concept_grade) for e in recorded] == [
        ("arrays", "correct"),
        ("recursion", "dont_know"),
        ("binary_search", "correct"),
    ]
    assert all(e.evidence_source == "quiz" and e.solved is None for e in recorded)
    assert all(e.intent is Intent.KNOWLEDGE_CHECK for e in recorded)
    deltas = marked.state.skill_deltas
    assert deltas["arrays"] > 0 > deltas["recursion"]
    assert deltas["binary_search"] > 0

    # Marked once: the quiz is closed, and a later message is not its answers.
    assert store.progress.quiz is None
    assert store.progress.last_thread == "explanation"


async def test_a_correction_gets_a_new_set_not_a_marking(store: _Store) -> None:  # noqa: F811
    chat = _Chat()
    await chat.turn(_ASK)
    redirected = await chat.turn(_REDIRECT)

    assert redirected.state.route == "assess"
    assert not redirected.state.quiz_grades
    assert not chat.llm.marker_users
    reply = _reply(redirected)
    assert "y.append(2)" in reply
    # The writer was shown the conversation it is correcting.
    assert "<conversation_so_far>" in chat.llm.writer_users[-1]
    assert store.progress.quiz is not None
    assert [item.topic for item in store.progress.quiz.items] == ["python_basics"]


async def test_asking_twice_never_repeats_a_refusal(store: _Store) -> None:  # noqa: F811
    del store
    chat = _Chat()
    first = await chat.turn(_ASK)
    second = await chat.turn(_ASK)

    assert first.state.route == second.state.route == "assess"
    assert "1. " in _reply(second)


async def test_with_no_model_the_fundamentals_are_asked(store: _Store) -> None:  # noqa: F811
    chat = _Chat()
    chat.llm.write_fails = True
    asked = await chat.turn(_ASK)

    reply = _reply(asked)
    assert "on the fundamentals" in reply
    assert "10. " in reply
    assert store.progress.quiz is not None
    assert len(store.progress.quiz.items) == 10


async def test_answers_that_could_not_be_marked_record_nothing(
    store: _Store,  # noqa: F811
    recorded: list[LearningEventCreate],
) -> None:
    chat = _Chat()
    await chat.turn(_ASK)
    chat.llm.mark_fails = True
    unmarked = await chat.turn(_ANSWERS)

    assert unmarked.state.route == "assess"
    assert "couldn't mark" in _reply(unmarked)
    assert not recorded
    # Still open: sending the answers again marks them.
    assert store.progress.quiz is not None
    assert store.progress.last_thread == "quiz"
    chat.llm.mark_fails = False
    again = await chat.turn(_ANSWERS)
    assert "You got **2 of 3** right." in _reply(again)
    assert len(recorded) == 3


async def test_a_question_asked_during_a_quiz_is_not_marked(store: _Store) -> None:  # noqa: F811
    chat = _Chat()
    await chat.turn(_ASK)
    asked = await chat.turn("explain binary search")

    assert asked.state.route == "explain"
    assert not chat.llm.marker_users
    assert store.progress.quiz is not None


# --- the pieces ---------------------------------------------------------------


def test_requested_count() -> None:
    assert requested_count("ask me 10 dsa basic question") == 10
    assert requested_count("ask me questions to check how much knowledge i have") == 5
    assert requested_count("ask me 50 questions") == 10
    assert requested_count("give me 3 questions on arrays") == 3
    assert requested_count(None) == 5


def test_the_builtin_set_uses_only_known_topics() -> None:
    quiz = builtin_quiz(10)
    assert len(quiz.items) == 10
    assert {item.topic for item in quiz.items} <= set(quiz_topics())
    assert len(builtin_quiz(4).items) == 4


def test_the_questions_never_show_their_keys() -> None:
    quiz = builtin_quiz(3)
    shown = render_quiz(quiz)
    for item in quiz.items:
        assert item.question in shown
        assert item.expected not in shown


def _quiz() -> QuizState:
    return QuizState(
        items=[
            QuizItem(question="Q1?", topic="arrays", expected="K1"),
            QuizItem(question="Q2?", topic="recursion", expected="K2"),
            QuizItem(question="Q3?", topic="recursion", expected="K3"),
        ]
    )


def test_an_unanswered_question_is_not_evidence_and_keeps_its_answer() -> None:
    marked = MarkedQuiz(
        quiz=_quiz(),
        verdicts=("correct", "incorrect", "unanswered"),
        feedback=("Yes.", "It needs a base case.", ""),
    )

    assert [(g.topic, g.grade) for g in marked.grades] == [
        ("arrays", "correct"),
        ("recursion", "incorrect"),
    ]
    said = render_marks(marked)
    assert "You got **1 of 2** right." in said
    assert "3. Not answered." in said
    assert "K3" not in said
    assert "You're solid on arrays." in said
    assert "Recursion needs work." in said
    assert "Shall we start with recursion" in said


def _quiz_event(topic: str, grade: str) -> LearningEventCreate:
    return LearningEventCreate(
        topic=topic,
        solved=None,
        evidence_source="quiz",
        concept_grade=cast("Any", grade),
    )


def test_a_quiz_answer_moves_the_skill_it_was_asked_about() -> None:
    skills, _ = apply_event({}, {}, _quiz_event("bfs", "correct"))
    # The topic the learner sees, and its family.
    assert skills["bfs"] > PRIOR
    assert skills[f"{FAMILY_PREFIX}graphs"] > PRIOR

    lower, _ = apply_event({}, {}, _quiz_event("bfs", "incorrect"))
    assert lower["bfs"] < PRIOR


def test_a_foundation_topic_keeps_its_own_estimate() -> None:
    # `recursion` has no corpus family: there is nowhere else to keep it.
    skills, _ = apply_event({}, {}, _quiz_event("recursion", "dont_know"))
    assert skills["recursion"] < PRIOR
    assert list(skills) == ["recursion"]


def test_answers_in_words_never_make_a_learner_strong() -> None:
    skills: dict[str, float] = {}
    for _ in range(40):
        skills, _ = apply_event(skills, {}, _quiz_event("bfs", "correct"))
    assert skills["bfs"] <= CONCEPT_CEILING


async def test_a_practice_request_with_no_topic_is_given_a_problem(store: _Store) -> None:  # noqa: F811
    """It used to be answered "Tell me which pattern you want to practise":
    the choice handed back to a learner who had asked to be given one."""
    del store
    chat = _Chat()
    first = await chat.turn("give me a problem")

    assert first.state.route == "practice"
    assert first.state.practice is not None
    assert first.state.practice.difficulty == "easy"
    reply = _reply(first)
    assert "which pattern you want to practise" not in reply
    assert "let's start with an easy one" in reply
    assert first.state.practice.title in reply


async def test_answers_are_marked_when_the_conversation_is_about_code(
    store: _Store,  # noqa: F811
    recorded: list[LearningEventCreate],
) -> None:
    """The owner's own order: code first, then the quiz. Read as a follow-up
    on the code, ten "skip" lines became a bug report on it."""
    chat = _Chat()
    _LABELS["what does this print"] = {"intent": "CODE_EXPLAIN"}
    # A skip shows nothing the classifier has a label for.
    _LABELS["1. skip"] = {"intent": "DSA_HINT", "refers_to_previous": True}
    try:
        await chat.turn("what does this print\na=b=[]\na.append(1)\nprint(b)")
        assert store.active is not None
        await chat.turn(_ASK)
        marked = await chat.turn("1. skip\n2. skip\n3. skip")
    finally:
        del _LABELS["what does this print"], _LABELS["1. skip"]

    assert marked.state.route == "assess"
    assert marked.state.problem_relation == "none"
    assert marked.state.decision is not None
    assert marked.state.decision.subject == "last_reply"
    # One label for the whole message is evidence about no subject.
    assert marked.state.decision.evidence == "none"
    assert len(recorded) == 3


async def test_a_message_that_matches_no_question_closes_the_wait(store: _Store) -> None:  # noqa: F811
    """Unread and unmatched, it used to get the same line again on every
    later message: the word-for-word repetition this feature replaced."""

    class _Unmatched(_LLM):
        async def chat(
            self,
            messages: Sequence[ChatMessage],
            *,
            temperature: float | None = None,
            max_tokens: int | None = None,
        ) -> ChatResult:
            if messages[0].content.startswith("You mark a learner's answers"):
                marks = [{"n": n, "verdict": "unanswered", "feedback": ""} for n in (1, 2, 3)]
                return ChatResult(
                    content=json.dumps({"marks": marks}), provider="fake", model="fake"
                )
            return await super().chat(messages, temperature=temperature, max_tokens=max_tokens)

    chat = _Chat()
    chat.llm = _Unmatched()
    await chat.turn(_ASK)
    other = await chat.turn("hmm wait a second")

    assert other.state.route == "assess"
    assert "set them aside" in _reply(other)
    # No longer waiting: the next message is read on its own.
    assert store.progress.last_thread == "explanation"
    later = await chat.turn("hmm wait a second")
    assert later.state.route != "assess"


def test_a_quiz_answer_starts_from_the_familys_estimate() -> None:
    # Strong sandbox evidence on graphs; one wrong quiz answer on bfs must not
    # put BFS back at a beginner's level.
    skills, _ = apply_event({f"{FAMILY_PREFIX}graphs": 0.8}, {}, _quiz_event("bfs", "incorrect"))

    assert 0.7 < skills["bfs"] < 0.8
