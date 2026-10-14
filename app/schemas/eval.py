"""The turn's decision record returned by `/chat` under `EVAL_MODE`.

A flat projection of what the graph already decided this turn, for the journey
replay (`eval/behavior/journey/replay_journey.py`) to compare against a
dataset's expected decision. It carries labels, ids, counts and the sandbox's
own values -- never the learner's message, their code, or a model's prose.
"""

from datetime import datetime

from pydantic import Field, JsonValue

from app.schemas.base import APIModel
from app.schemas.event import ConceptGrade, Difficulty, EvidenceSource
from app.schemas.plan import AssistanceLevel, TeachingMode
from app.schemas.tutoring import PendingKind

__all__ = [
    "EvalDecision",
    "EvalEscalation",
    "EvalEvidence",
    "EvalFailingCase",
    "EvalGrade",
    "EvalPending",
    "EvalPlan",
    "EvalSandbox",
]


class EvalPlan(APIModel):
    difficulty: Difficulty
    assistance_level: AssistanceLevel
    #: The hint ladder rung this reply reached, when it was a ladder reply.
    hint_rung: int | None = None
    topic: str | None = None
    skill_level: float
    rationale: list[str] = Field(default_factory=list[str])


class EvalGrade(APIModel):
    """How the learner's reply was judged, and by what."""

    grade: ConceptGrade
    #: `question`: a curated question was graded (`AnswerGrade`).
    #: `reading`: the tutor's reading of the reply (`TurnDecision.evidence`).
    source: str
    method: str | None = None


class EvalPending(APIModel):
    kind: PendingKind
    topic: str | None = None
    question_id: str


class EvalFailingCase(APIModel):
    name: str
    expected: JsonValue = None
    actual: JsonValue = None


class EvalSandbox(APIModel):
    """The verdict on the LEARNER's code (`initial_verdict`), never a patch's."""

    status: str
    passed: int
    total: int
    first_failing: EvalFailingCase | None = None
    #: Where the test cases came from: `extracted` | `synthesised` | `none`.
    provenance: str
    #: Whose code the verdict is on: `learner` | `reference`.
    subject: str
    #: The verdict on the tutor's own patch or reference, when one was run.
    final_status: str | None = None


class EvalEvidence(APIModel):
    topic: str
    source: EvidenceSource
    solved: bool | None = None
    concept_grade: ConceptGrade | None = None
    needed_full_solution: bool = False
    hints_used: int = 0
    errors: list[str] = Field(default_factory=list[str])


class EvalEscalation(APIModel):
    """Why code above the plan's starting level was shown this turn."""

    reason: str
    teaching_mode: TeachingMode
    verified_reference: bool


class EvalDecision(APIModel):
    intent: str | None = None
    intent_confidence: float | None = None
    intent_source: str | None = None
    route: str
    move: str | None = None
    subject: str | None = None
    scaffold: str | None = None
    wants_code: bool = False
    withhold: bool = False
    reasons: list[str] = Field(default_factory=list[str])
    teaching_mode: TeachingMode
    plan: EvalPlan | None = None
    grade: EvalGrade | None = None
    pending_check: EvalPending | None = None
    sandbox: EvalSandbox | None = None
    evidence: list[EvalEvidence] = Field(default_factory=list[EvalEvidence])
    misconceptions: list[str] = Field(default_factory=list[str])
    #: Stored facts this reply was shaped by, as fixed keys
    #: (`common_errors.<id>`, `skills.<topic>`); empty when none was.
    memory_used: list[str] = Field(default_factory=list[str])
    escalation: EvalEscalation | None = None
    reveals_code: bool = False
    skill_deltas: dict[str, float] = Field(default_factory=dict[str, float])
    adaptations: list[str] = Field(default_factory=list[str])
    last_thread: str | None = None
    #: The clock this turn ran on (the override, when one was sent).
    clock: datetime
