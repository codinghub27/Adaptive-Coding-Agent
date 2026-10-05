"""Tutoring-loop types (ADAPTIVE-tutoring G1..G5).

Trust boundaries, field by field:

- `PendingCheck` is AGENT-authored: its question, rubric and accepted answers
  come from the curated question bank (`app/knowledge/tutoring/`), which is
  anchored in the corpus. It never contains learner text.
- `AnswerGrade` is the grader's verdict on a learner reply. It names concepts
  and a misconception by CLOSED ids only (rubric concept ids, catalog ids); it
  never carries the reply itself.
- `SessionProgress` is per-conversation bookkeeping built from those two.
"""

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas.base import APIModel
from app.schemas.event import Difficulty
from app.schemas.execution import Verdict
from app.schemas.input import ActiveProblem, CodeBlock
from app.schemas.plan import AssistanceLevel

__all__ = [
    "AnswerGrade",
    "ConceptRubric",
    "ExecutionView",
    "Grade",
    "GradeMethod",
    "GradeRecord",
    "OptionRubric",
    "PendingCheck",
    "PendingKind",
    "PendingView",
    "PracticeRecord",
    "Reaction",
    "SessionProgress",
    "TutoringView",
]

Grade = Literal["correct", "partial", "incorrect", "dont_know"]

#: How a grade was reached. Everything except the two `llm*` values is a
#: deterministic rule; `llm_low_confidence` marks a judge "correct" that was
#: downgraded to "partial" because the judge was not confident.
GradeMethod = Literal[
    "injection_guard",
    "dont_know",
    "accepted",
    "wrong_answer",
    "option",
    "concepts",
    "recognition",
    "llm",
    "llm_terminology",
    "llm_low_confidence",
    "fallback",
]

#: The move a grade decides (brief Section 5, "Plan reaction").
Reaction = Literal["advance", "narrow", "reframe", "scaffold"]

PendingKind = Literal["question", "code_submission"]


class ConceptRubric(APIModel):
    """One concept a correct answer must show, with the words that evidence it."""

    id: str = Field(min_length=1, max_length=64)
    keywords: list[str] = Field(default_factory=list[str])


class OptionRubric(APIModel):
    """One answer option of a closed-choice question and what choosing it means."""

    label: str = Field(min_length=1, max_length=40)
    keywords: list[str] = Field(default_factory=list[str])
    grade: Grade
    misconception: str | None = None


class PendingCheck(APIModel):
    """The agent's own open question (or code request) awaiting the learner's reply."""

    kind: PendingKind
    question_id: str = Field(min_length=1, max_length=96)
    question: str = Field(min_length=1, max_length=600)
    topic: str | None = Field(default=None, max_length=64)
    #: Where the rubric is anchored: "corpus:<pattern>#<section>".
    rubric_ref: str | None = Field(default=None, max_length=120)
    expected_concepts: list[ConceptRubric] = Field(default_factory=list[ConceptRubric])
    accepted_answers: list[str] = Field(default_factory=list[str])
    options: list[OptionRubric] = Field(default_factory=list[OptionRubric])
    #: Answer phrase -> catalog misconception id ("" = wrong, no named misconception).
    wrong_answers: dict[str, str] = Field(default_factory=dict[str, str])
    #: Catalog misconception ids the LLM judge may name for this question.
    misconception_ids: list[str] = Field(default_factory=list[str])
    problem_key: str | None = Field(default=None, max_length=32)
    assistance_at_ask: AssistanceLevel | None = None
    #: The reusable takeaway (bank text) to end on once this check resolves --
    #: for a code request, shown with the reviewed or verified code.
    lesson: str | None = Field(default=None, max_length=400)
    created_at: datetime


class AnswerGrade(APIModel):
    """The grader's verdict on one learner reply. Closed ids only, never the reply."""

    grade: Grade
    method: GradeMethod
    matched_concepts: list[str] = Field(default_factory=list[str])
    missing_concepts: list[str] = Field(default_factory=list[str])
    misconception_id: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class GradeRecord(APIModel):
    question_id: str
    topic: str | None = None
    grade: Grade


class PracticeRecord(APIModel):
    title: str = Field(min_length=1, max_length=120)
    topic: str = Field(min_length=1, max_length=64)
    difficulty: Difficulty
    curated: bool = False
    #: Why this difficulty (progression rule code, e.g. "harder_after_success").
    reason: str | None = Field(default=None, max_length=40)
    #: The problem statement made the conversation's active problem: curated
    #: bank text, or the corpus title + link. Trusted, never learner text.
    statement: str | None = Field(default=None, max_length=2000)


class SessionProgress(APIModel):
    """What this conversation has shown so far (G5): drives in-session progression."""

    last_topic: str | None = Field(default=None, max_length=64)
    last_practice: PracticeRecord | None = None
    grades: list[GradeRecord] = Field(default_factory=list[GradeRecord])
    #: `len(grades)` when `last_practice` was handed out: grades after it are
    #: this problem's evidence for the next difficulty step.
    grades_at_practice: int = 0
    asked: list[str] = Field(default_factory=list[str])
    misconceptions: list[str] = Field(default_factory=list[str])
    #: Scaffolding floor per problem key (or topic): raised one step by a
    #: "don't know" (G1/G5) so later turns on that problem keep the extra help.
    assistance_floor: dict[str, AssistanceLevel] = Field(default_factory=dict[str, AssistanceLevel])
    #: The subject this conversation was on BEFORE the current one, so "go back
    #: to the earlier one" has something to go back to. Untrusted learner data.
    earlier_problem: ActiveProblem | None = None
    #: The learner's latest code for the subject `last_attempt_key` names, so
    #: "where's the mistake?" one turn later still has code to read.
    last_attempt: list[CodeBlock] = Field(default_factory=list[CodeBlock])
    last_attempt_key: str | None = Field(default=None, max_length=32)

    @classmethod
    def empty(cls) -> "SessionProgress":
        return cls()


class PendingView(APIModel):
    kind: PendingKind
    question_id: str
    question: str
    options: list[str] = Field(default_factory=list[str])


class ExecutionView(APIModel):
    """The verdicts this turn's "Execution" lines were rendered from."""

    #: Sandbox verdict on the LEARNER'S OWN submitted code (`initial_verdict`).
    learner: Verdict | None = None
    #: The turn's authoritative outer verification (`AgentState.verification`).
    final: Verdict | None = None


class TutoringView(APIModel):
    """The tutoring loop's per-turn outcome, for the UI and the replay instrument."""

    grade: AnswerGrade | None = None
    reaction: Reaction | None = None
    pending: PendingView | None = None
    misconceptions: list[str] = Field(default_factory=list[str])
    surfaced_misconceptions: list[str] = Field(default_factory=list[str])
    assistance_before: AssistanceLevel | None = None
    assistance_after: AssistanceLevel | None = None
    practice: PracticeRecord | None = None
    submission_reviewed: bool = False
    execution: ExecutionView | None = None
