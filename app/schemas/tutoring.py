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
from typing import Final, Literal

from pydantic import Field

from app.schemas.base import APIModel
from app.schemas.event import Difficulty
from app.schemas.execution import Verdict
from app.schemas.input import ActiveProblem, CodeBlock
from app.schemas.plan import AssistanceLevel

__all__ = [
    "FOUNDATION_TOPICS",
    "MAX_QUIZ_QUESTIONS",
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
    "QuizGrade",
    "QuizItem",
    "QuizState",
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


LastThread = Literal["problem", "plan", "explanation", "quiz", "code", "discussion"]

#: The most questions one knowledge check asks.
MAX_QUIZ_QUESTIONS: Final = 10

#: Topics a knowledge check may be about that the corpus has no page for.
#: They are skill-map keys like any pattern slug, but they are also ordinary
#: words ("strings", "arrays", "sorting"): `app.agents.planner` never reads
#: one out of a problem statement or code as that turn's topic.
FOUNDATION_TOPICS: Final[tuple[str, ...]] = (
    "python_basics",
    "complexity",
    "arrays",
    "strings",
    "recursion",
    "sorting",
)


class QuizItem(APIModel):
    """One question of a knowledge check, with the answer it is marked against.

    `topic` is a key of the learner's skill map (a corpus pattern or a
    foundation topic, `app.agents.assess.quiz_topics`): a closed vocabulary.
    `question` and `expected` are the tutor's own text; `expected` is never
    shown before the learner has answered.
    """

    question: str = Field(min_length=1, max_length=600)
    topic: str = Field(min_length=1, max_length=64)
    expected: str = Field(min_length=1, max_length=800)


class QuizState(APIModel):
    """The knowledge check the tutor asked and is waiting on."""

    items: list[QuizItem] = Field(min_length=1, max_length=MAX_QUIZ_QUESTIONS)
    #: Asked one question per turn (a drill: no number of questions was asked
    #: for) instead of as one numbered list.
    one_at_a_time: bool = False
    #: The question waiting for an answer, when asked one at a time.
    current: int = Field(default=0, ge=0)
    #: Verdicts on the questions answered so far, in order (one at a time).
    verdicts: list[str] = Field(default_factory=list[str])


class QuizGrade(APIModel):
    """How one answered quiz question was marked: evidence for one topic."""

    topic: str = Field(min_length=1, max_length=64)
    grade: Grade


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
    #: What the tutor's LAST reply was: a step on the conversation's problem,
    #: a study plan, or a concept explanation. A short follow-up ("where
    #: should I start?") is about THAT reply, which is not always the stored
    #: problem -- measured: asked after a roadmap, it got the next hint on a
    #: problem from the day before.
    last_thread: LastThread | None = None
    #: The subject (problem key) of a debugging exercise the tutor set: code it
    #: wrote with one planted bug. The learner's fix to it is THEIR work, even
    #: though most of its lines are the tutor's.
    bug_exercise_key: str | None = Field(default=None, max_length=96)
    #: What the learner showed on recent turns of ONE subject (`evidence_key`),
    #: oldest first, in the section 2.2 labels. Requests are not logged. It is
    #: what lets the tutor change representation after repeated struggle and
    #: skip steps after repeated success (`app.tutoring.adaptation`).
    evidence_log: list[str] = Field(default_factory=list[str])
    evidence_key: str | None = Field(default=None, max_length=96)
    #: The subject (`evidence_key`) on which the learner asked not to be given
    #: the answer. Cleared when they ask for the code or move to a new subject.
    withhold_key: str | None = Field(default=None, max_length=96)
    #: The knowledge check the tutor asked last and has not marked yet. It is
    #: open only while it is also the last thing the tutor said
    #: (`last_thread == "quiz"`); cleared once it is marked.
    quiz: QuizState | None = None

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
