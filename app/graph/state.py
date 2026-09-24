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

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.base import LLMClient
from app.schemas.base import APIModel
from app.schemas.conversation import MessageView
from app.schemas.event import LearningEventCreate
from app.schemas.input import StructuredInput
from app.schemas.intent import IntentResult
from app.schemas.plan import TeachingPlan
from app.schemas.profile import LearnerProfileView

__all__ = [
    "AgentOutcome",
    "AgentState",
    "AgentStateUpdate",
    "GraphContext",
    "NodeError",
    "RawInput",
    "RouteKey",
]

RouteKey = Literal["dsa", "debug", "explain", "clarify"]


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


class AgentOutcome(APIModel):
    """What a specialized agent reports after handling a turn.

    A stub today; a full subgraph in Phase 07. `solved=None` means no
    observed outcome, so the caller must not persist a learning event for
    this turn.
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
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    input: RawInput
    structured_input: StructuredInput | None = None
    intent: IntentResult | None = None
    profile: LearnerProfileView | None = None
    recent_context: list[MessageView] = Field(default_factory=list[MessageView])
    plan: TeachingPlan | None = None
    retrieved_context: list[str] = Field(default_factory=list[str])
    execution_result: dict[str, JsonValue] | None = None
    route: RouteKey | None = None
    agent_output: AgentOutcome | None = None
    response: str | None = None
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
    recent_context: list[MessageView]
    plan: TeachingPlan | None
    retrieved_context: list[str]
    execution_result: dict[str, JsonValue] | None
    route: RouteKey | None
    agent_output: AgentOutcome | None
    response: str | None
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
