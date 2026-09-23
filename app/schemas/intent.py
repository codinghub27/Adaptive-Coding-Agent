"""Intent classification result types.

Intent classification acts on **untrusted** user-supplied content (see
`app.schemas.input`). The rationale field is a human-readable explanation of
the classification decision, not an instruction channel.
"""

from enum import StrEnum
from typing import Final, Literal

from pydantic import Field, computed_field

from app.schemas.base import APIModel
from app.schemas.input import StructuredInput

__all__ = [
    "Intent",
    "IntentResult",
    "IntentSource",
    "LOW_CONFIDENCE_THRESHOLD",
    "UnderstandResponse",
]


class Intent(StrEnum):
    """The set of intents the classifier can assign to a user request."""

    DSA_SOLVE = "DSA_SOLVE"
    DSA_HINT = "DSA_HINT"
    CODE_DEBUG = "CODE_DEBUG"
    CODE_EXPLAIN = "CODE_EXPLAIN"
    CODE_REVIEW = "CODE_REVIEW"
    ERROR_EXPLANATION = "ERROR_EXPLANATION"
    OPTIMIZATION = "OPTIMIZATION"
    CONCEPT_EXPLANATION = "CONCEPT_EXPLANATION"
    IMAGE_CODE_ANALYSIS = "IMAGE_CODE_ANALYSIS"
    TEST_CASE_ANALYSIS = "TEST_CASE_ANALYSIS"
    APPROACH_DISCUSSION = "APPROACH_DISCUSSION"


LOW_CONFIDENCE_THRESHOLD: Final = 0.6

IntentSource = Literal["rule", "llm", "fallback"]


class IntentResult(APIModel):
    """The outcome of classifying a `StructuredInput`'s intent."""

    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    source: IntentSource
    rationale: str | None = None

    @computed_field
    @property
    def low_confidence(self) -> bool:
        """True when confidence falls below `LOW_CONFIDENCE_THRESHOLD`."""
        return self.confidence < LOW_CONFIDENCE_THRESHOLD


class UnderstandResponse(APIModel):
    """Combined output of the input-understanding pipeline."""

    input: StructuredInput
    intent: IntentResult
