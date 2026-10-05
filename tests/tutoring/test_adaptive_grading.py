"""Acceptance cases for answer diagnosis: a terminology slip is not a conceptual
miss, a wrong technique is taught once rather than re-asked, and a refused
request for code never quotes a hint quota.

The observed session: asked which technique fits a binary-search problem, the
learner answered "two pointers?" and then "using two pointer to find the mid
value and based on target value we change either left or right" -- and got
"Not quite" plus the same question both times.
"""

import json

from app.agents.hint_engine import HintProgress
from app.agents.planner import ProblemAnalysis, build_plan
from app.graph.subgraphs.dsa import _refused_ask_note  # pyright: ignore[reportPrivateUsage]
from app.schemas.agent_results import HintLevel, HintResult
from app.schemas.input import StructuredInput
from app.schemas.intent import Intent, IntentResult
from app.schemas.plan import TeachingMode
from app.schemas.profile import LearnerProfileView
from app.schemas.tutoring import AnswerGrade, GradeRecord, PendingCheck, SessionProgress
from app.tutoring.bank import pending_for
from app.tutoring.grader import grade_deterministic, grade_reply
from app.tutoring.turn import react
from tests.input.fakes import FakeLLMClient

_RECOGNITION = "recognition.binary_search"
_RIGHT_MECHANICS_WRONG_NAME = (
    "using two pointer to find the mid value and based on target value we change either "
    "left or right."
)
_WRONG_MECHANICS = "I use two pointers because left moves toward right one step at a time."


def _check() -> PendingCheck:
    check = pending_for(_RECOGNITION, assistance="hint")
    assert check is not None
    return check


def _judge(grade: str, *, wrong_name: bool = False) -> FakeLLMClient:
    return FakeLLMClient(
        chat_content=json.dumps(
            {
                "grade": grade,
                "matched_concepts": ["technique", "reason"] if grade == "correct" else [],
                "wrong_name": wrong_name,
                "confidence": 0.9,
            }
        )
    )


def _react(grade: AnswerGrade, progress: SessionProgress | None = None):  # noqa: ANN202
    return react(
        _check(),
        grade,
        mode="balanced",
        cap=None,
        progress=progress or SessionProgress.empty(),
        has_active_problem=True,
    )


# --- CASE 1: terminology mistake ------------------------------------------------


def test_naming_the_technique_and_describing_it_is_correct_without_a_judge() -> None:
    grade = grade_deterministic(
        _check(), "Binary search uses two pointers. We check mid and move left or right."
    )
    assert grade is not None
    assert grade.grade == "correct"


def test_a_described_answer_under_another_name_is_left_to_the_judge() -> None:
    """Keywords cannot tell a terminology slip from a wrong idea, so no rule decides it."""
    assert grade_deterministic(_check(), _RIGHT_MECHANICS_WRONG_NAME) is None
    assert grade_deterministic(_check(), _WRONG_MECHANICS) is None


async def test_right_mechanics_under_the_wrong_name_is_credited_and_moves_on() -> None:
    llm = _judge("correct", wrong_name=True)
    grade = await grade_reply(_check(), _RIGHT_MECHANICS_WRONG_NAME, llm)
    assert grade.grade == "correct"
    assert grade.method == "llm_terminology"
    # The judge was told which technique was expected and how to treat a wrong name.
    system, user = llm.chat_calls[0][0].content, llm.chat_calls[0][-1].content
    assert "wrong_name" in system
    assert "Expected technique: binary search" in user

    reacted = _react(grade)
    assert reacted.reaction == "advance"
    assert "reasoning is right" in reacted.feedback
    assert "binary search" in reacted.feedback
    assert "Not quite" not in reacted.feedback
    # It moves on to the next step instead of asking "which technique?" again.
    assert reacted.next_pending is not None
    assert reacted.next_pending.question_id != _check().question_id


# --- CASE 2: genuine misconception ----------------------------------------------


async def test_wrong_mechanics_under_the_wrong_name_is_incorrect() -> None:
    grade = await grade_reply(_check(), _WRONG_MECHANICS, _judge("incorrect"))
    assert grade.grade == "incorrect"
    reacted = _react(grade)
    # Corrected (it names the right technique) -- and not asked the same thing again.
    assert "binary search" in reacted.feedback.lower()
    assert reacted.next_pending is None or (
        reacted.next_pending.question_id != _check().question_id
    )


# --- repeating the same question --------------------------------------------------


def test_a_bare_wrong_technique_is_corrected_once_not_reasked() -> None:
    grade = grade_deterministic(_check(), "two pointers?")
    assert grade is not None
    assert grade.grade == "incorrect"
    reacted = _react(grade)
    assert reacted.reaction == "scaffold"
    assert reacted.next_pending is None or (
        reacted.next_pending.question_id != _check().question_id
    )


def test_a_second_miss_on_any_question_is_taught_and_moves_on() -> None:
    pending = pending_for("hashing.two_sum.complement", assistance="hint")
    assert pending is not None
    missed_once = SessionProgress.empty().model_copy(
        update={
            "grades": [
                GradeRecord(question_id=pending.question_id, topic="hashing", grade="incorrect")
            ]
        }
    )
    again = AnswerGrade.model_validate({"grade": "incorrect", "method": "wrong_answer"})
    reacted = react(
        pending, again, mode="balanced", cap=None, progress=missed_once, has_active_problem=True
    )
    assert reacted.reaction == "scaffold"
    assert reacted.next_pending is not None
    assert reacted.next_pending.question_id != pending.question_id


# --- CASE 4: an explicit ask for the code -----------------------------------------


def _plan(mode: TeachingMode, progress: HintProgress):  # noqa: ANN202
    return build_plan(
        IntentResult(intent=Intent.DSA_SOLVE, confidence=0.9, source="llm"),
        LearnerProfileView.empty(),
        ProblemAnalysis(topic="binary_search", skill_level=0.5, topic_source="title"),
        hint_progress=progress,
        structured_input=StructuredInput(
            source="text", question="give me the code", problem="Search a sorted array."
        ),
        teaching_mode=mode,
    )


def _hint() -> HintResult:
    return HintResult(
        level=HintLevel.L0_NUDGE,
        text="x",
        is_terminal=False,
        reveals_code=False,
        ceiling=HintLevel.L3_CONCRETE_IDEA,
    )


def test_a_refused_ask_never_quotes_a_hint_quota() -> None:
    first_message = _plan("balanced", HintProgress())
    note = _refused_ask_note(first_message, _hint())
    assert note is not None
    assert "more hint" not in note
    assert "just ask" in note

    challenge = _plan("challenge", HintProgress(last_level=HintLevel.L1_WHAT_TO_TRACK))
    note = _refused_ask_note(challenge, _hint())
    assert note is not None
    assert "Challenge mode" in note
    assert "more hint" not in note


def test_a_granted_ask_carries_no_refusal_note() -> None:
    granted = _plan("balanced", HintProgress(last_level=HintLevel.L0_NUDGE))
    assert granted.assistance_level == "full"
    assert _refused_ask_note(granted, _hint()) is None
