"""The LangGraph state contract threaded through every node.

`AgentState` is the single typed object passed between graph nodes. Nodes
must never mutate it -- they return an `AgentStateUpdate` partial update, and
LangGraph merges that into the next state (using the `operator.add` reducers
declared below for the append-only `events`/`events_persisted`/`errors`
lists). `GraphContext`
carries run-scoped dependencies (LLM client, DB session, ids) via LangGraph's
`context=`/`runtime.context`, never module-level globals.
"""

import operator
from dataclasses import dataclass
from typing import Annotated, Literal, TypedDict
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.planner import TopicSource
from app.execution.base import CodeRunner
from app.knowledge.base import DEFAULT_KNOWLEDGE_TOP_K, Retriever
from app.llm.base import LLMClient
from app.schemas.agent_results import AgentResult
from app.schemas.base import APIModel
from app.schemas.conversation import MessageView
from app.schemas.decision import TurnDecision
from app.schemas.event import LearningEventCreate
from app.schemas.execution import ExecutionRequest, ExecutionResult, Verdict
from app.schemas.input import ActiveProblem, ProblemRelation, StructuredInput
from app.schemas.intent import IntentResult
from app.schemas.knowledge import RetrievalHit
from app.schemas.plan import (
    DEFAULT_TEACHING_MODE,
    AssistanceLevel,
    TeachingMode,
    TeachingPlan,
)
from app.schemas.profile import LearnerProfileView
from app.schemas.response import GeneratedResponse
from app.schemas.tutoring import (
    AnswerGrade,
    PendingCheck,
    PracticeRecord,
    Reaction,
    SessionProgress,
    TutoringView,
)

__all__ = [
    "AgentOutcome",
    "AgentState",
    "AgentStateUpdate",
    "GraphContext",
    "NodeError",
    "RawInput",
    "RouteKey",
    "SuiteSource",
]

RouteKey = Literal["dsa", "debug", "explain", "practice", "clarify", "grade", "meta"]

# Where this turn's `TestSuite` (if any) came from -- see `debug_agent` and the
# review branch of `explain_agent` in `app.graph.nodes`, and the gating this
# enables in `_build_learning_event`: a "synthesised" suite may only produce a
# `solved` verdict when the turn also carries a real problem statement.
SuiteSource = Literal["none", "extracted", "synthesised"]


class RawInput(APIModel):
    """The raw request as received, before normalization.

    Every field holds **untrusted user data** -- raw text, code, and image
    bytes supplied by the learner. It must never be treated as instructions
    to the agent; it is content to reason about, not commands to follow.
    """

    text: str | None = None
    language: str | None = Field(default=None, max_length=32)
    image: bytes | None = None
    image_mime: str | None = None
    topic_hint: str | None = Field(default=None, max_length=64)
    #: A *ceiling* on `TeachingPlan.assistance_level` requested by the client
    #: (see `app.agents.planner.clamp_assistance`). It can only ever reduce
    #: how much help is given, never raise it -- so a hostile client cannot
    #: use it to extract a full solution the planner wouldn't otherwise give.
    assistance_cap: AssistanceLevel | None = None
    #: Guidance / Balanced / Challenge (ADAPTIVE-upgrade P4): when a full
    #: solution may be revealed. Never raises assistance on its own.
    teaching_mode: TeachingMode = DEFAULT_TEACHING_MODE


class AgentOutcome(APIModel):
    """What a specialized agent reports after handling a turn.

    A stub today; a full subgraph in Phase 07. `solved=None` means no
    observed outcome, so the caller must not persist a learning event for
    this turn.

    This is a **lossy projection** of a Phase 07 agent's full result (see
    `app.schemas.agent_results.AgentResult`), used for learning events and as
    a degraded fallback. The full structured result consumed by the Phase 08
    response layer lives on `AgentState.agent_result`.
    """

    text: str
    topic: str | None = None
    pattern: str | None = None
    solved: bool | None = None
    hints_used: int = Field(default=0, ge=0, le=1000)
    needed_full_solution: bool = False
    errors: list[str] = Field(default_factory=list[str])


class NodeError(APIModel):
    """A recoverable error surfaced by a graph node.

    `message` must always be a fixed, safe string -- never the raw exception
    text, which may contain secrets or untrusted user data.
    """

    node: str
    error_type: str
    message: str


class AgentState(BaseModel):
    """The one typed state object threaded through every graph node.

    Frozen: nodes must never mutate this in place. Each node returns a
    partial `AgentStateUpdate`; LangGraph merges it into a new `AgentState`
    (using the `operator.add` reducers for `events` and `errors`).

    `agent_output` is the lossy `AgentOutcome` projection used for learning
    events; `agent_result` is the full structured `AgentResult` consumed by
    the Phase 08 response layer.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    input: RawInput
    structured_input: StructuredInput | None = None
    intent: IntentResult | None = None
    profile: LearnerProfileView | None = None
    #: Per-topic skill change this turn produced, keyed by skill map key.
    #: Empty whenever no event was recorded or the event carried no outcome
    #: -- exposure creates a key without moving it, so it has no delta.
    skill_deltas: dict[str, float] = Field(default_factory=dict[str, float])
    recent_context: list[MessageView] = Field(default_factory=list[MessageView])
    plan: TeachingPlan | None = None
    topic_source: TopicSource | None = None
    retrieved_context: list[RetrievalHit] = Field(default_factory=list[RetrievalHit])
    execution_request: ExecutionRequest | None = None
    execution_result: ExecutionResult | None = None
    verification: Verdict | None = None
    route: RouteKey | None = None
    #: The one tutoring decision for this turn (`decide_turn`): what it is
    #: about, what the learner showed, what to do. Every later node reads it.
    decision: TurnDecision | None = None
    agent_output: AgentOutcome | None = None
    agent_result: AgentResult | None = None
    suite_source: SuiteSource = "none"
    #: The conversation's stored active problem (loaded by
    #: `load_learner_profile`), how this turn relates to it, and the hint-ladder
    #: key of the problem this turn is about (ADAPTIVE-upgrade P1).
    active_problem: ActiveProblem | None = None
    problem_relation: ProblemRelation = "none"
    problem_key: str | None = None
    #: This turn went back to the conversation's EARLIER subject: `active_problem`
    #: now holds it and must be stored as the active one again.
    subject_switched: bool = False
    #: This turn follows up on the tutor's last reply (a plan or an
    #: explanation): it is answered in that reply's context, with the recent
    #: messages in the prompt, and is not a turn on the stored problem.
    thread_followup: bool = False
    #: Tutoring loop (ADAPTIVE-tutoring). `pending_check`/`session_progress`
    #: are loaded with the profile; `answer_grade` is set by `grade_answer`;
    #: `next_pending`/`tutoring` are decided in `final_response` and persisted
    #: by `update_learner_model`. `practice` is the problem `practice_agent`
    #: handed out; `submitted_code` marks a reply to a pending code request.
    pending_check: PendingCheck | None = None
    session_progress: SessionProgress | None = None
    answer_grade: AnswerGrade | None = None
    reaction: Reaction | None = None
    grade_feedback: str | None = None
    grade_lesson: str | None = None
    assistance_before: AssistanceLevel | None = None
    grade_handoff: bool = False
    practice: PracticeRecord | None = None
    submitted_code: bool = False
    next_pending: PendingCheck | None = None
    next_progress: SessionProgress | None = None
    tutoring: TutoringView | None = None
    response: str | None = None
    generated_response: GeneratedResponse | None = None
    events: Annotated[list[LearningEventCreate], operator.add] = Field(
        default_factory=list[LearningEventCreate]
    )
    events_persisted: Annotated[list[UUID], operator.add] = Field(default_factory=list[UUID])
    errors: Annotated[list[NodeError], operator.add] = Field(default_factory=list[NodeError])


class AgentStateUpdate(TypedDict, total=False):
    """Partial update returned by a graph node; mirrors `AgentState`'s fields."""

    input: RawInput
    structured_input: StructuredInput | None
    intent: IntentResult | None
    profile: LearnerProfileView | None
    skill_deltas: dict[str, float]
    recent_context: list[MessageView]
    plan: TeachingPlan | None
    topic_source: TopicSource | None
    retrieved_context: list[RetrievalHit]
    execution_request: ExecutionRequest | None
    execution_result: ExecutionResult | None
    verification: Verdict | None
    route: RouteKey | None
    decision: TurnDecision | None
    agent_output: AgentOutcome | None
    agent_result: AgentResult | None
    suite_source: SuiteSource
    active_problem: ActiveProblem | None
    problem_relation: ProblemRelation
    problem_key: str | None
    subject_switched: bool
    thread_followup: bool
    pending_check: PendingCheck | None
    session_progress: SessionProgress | None
    answer_grade: AnswerGrade | None
    reaction: Reaction | None
    grade_feedback: str | None
    grade_lesson: str | None
    assistance_before: AssistanceLevel | None
    grade_handoff: bool
    practice: PracticeRecord | None
    submitted_code: bool
    next_pending: PendingCheck | None
    next_progress: SessionProgress | None
    tutoring: TutoringView | None
    response: str | None
    generated_response: GeneratedResponse | None
    events: list[LearningEventCreate]
    events_persisted: list[UUID]
    errors: list[NodeError]


@dataclass(frozen=True, slots=True)
class GraphContext:
    """Per-run dependencies passed via LangGraph `context=`/`runtime.context`.

    Never reach for globals for these -- inject them through the graph
    runtime instead, so nodes stay testable and providers stay swappable.
    """

    llm: LLMClient
    session: AsyncSession | None = None
    user_id: UUID | None = None
    conversation_id: UUID | None = None
    retriever: Retriever | None = None
    knowledge_top_k: int = DEFAULT_KNOWLEDGE_TOP_K
    runner: CodeRunner | None = None
