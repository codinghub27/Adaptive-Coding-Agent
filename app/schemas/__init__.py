"""Shared Pydantic base types for API boundary models."""

from typing import Literal

from app.schemas.base import APIModel
from app.schemas.execution import (
    DEFAULT_TIMEOUT_S,
    MAX_CODE_CHARS,
    MAX_TEST_CASES,
    MAX_TIMEOUT_S,
    MIN_TIMEOUT_S,
    CaseResult,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    FailureCategory,
    HarnessError,
    HarnessPhase,
    HarnessReport,
    Language,
    TestCase,
    TestSuite,
    Verdict,
    VerdictStatus,
)
from app.schemas.input import CodeBlock, InputSource, StructuredInput
from app.schemas.intent import (
    LOW_CONFIDENCE_THRESHOLD,
    Intent,
    IntentResult,
    IntentSource,
    UnderstandResponse,
)
from app.schemas.knowledge import CorpusDocument, KnowledgeChunk, RetrievalHit, RetrieverName
from app.schemas.plan import (
    ASSISTANCE_ORDER,
    AssistanceLevel,
    SolutionStrategy,
    TeachingPlan,
)

__all__ = [
    "APIModel",
    "ComponentStatus",
    "HealthResponse",
    "DEFAULT_TIMEOUT_S",
    "MAX_CODE_CHARS",
    "MAX_TEST_CASES",
    "MAX_TIMEOUT_S",
    "MIN_TIMEOUT_S",
    "CaseResult",
    "ExecutionRequest",
    "ExecutionResult",
    "ExecutionStatus",
    "FailureCategory",
    "HarnessError",
    "HarnessPhase",
    "HarnessReport",
    "Language",
    "TestCase",
    "TestSuite",
    "Verdict",
    "VerdictStatus",
    "CodeBlock",
    "InputSource",
    "StructuredInput",
    "Intent",
    "IntentResult",
    "IntentSource",
    "LOW_CONFIDENCE_THRESHOLD",
    "UnderstandResponse",
    "CorpusDocument",
    "KnowledgeChunk",
    "RetrievalHit",
    "RetrieverName",
    "ASSISTANCE_ORDER",
    "AssistanceLevel",
    "SolutionStrategy",
    "TeachingPlan",
]


ComponentStatus = Literal["ok", "error"]


class HealthResponse(APIModel):
    """Response body for `GET /health`."""

    status: Literal["ok", "degraded"]
    db: ComponentStatus
    qdrant: ComponentStatus
