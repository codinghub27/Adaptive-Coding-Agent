"""Tests for the Phase 2 input/intent Pydantic schemas."""

import pytest
from pydantic import ValidationError

from app.schemas import Intent, IntentResult, StructuredInput
from app.schemas.intent import LOW_CONFIDENCE_THRESHOLD


def test_intent_has_exactly_eleven_members() -> None:
    assert len(Intent) == 11


def test_intent_result_rejects_confidence_above_one() -> None:
    with pytest.raises(ValidationError):
        IntentResult(intent=Intent.CODE_DEBUG, confidence=1.5, source="rule")


def test_intent_result_rejects_extra_field() -> None:
    with pytest.raises(ValidationError):
        IntentResult(
            intent=Intent.CODE_DEBUG,
            confidence=0.9,
            source="rule",
            bogus="nope",  # type: ignore[call-arg]
        )


def test_low_confidence_appears_in_model_dump() -> None:
    result = IntentResult(intent=Intent.CODE_DEBUG, confidence=0.9, source="rule")
    dumped = result.model_dump()
    assert "low_confidence" in dumped
    assert dumped["low_confidence"] is False


def test_low_confidence_flips_at_threshold() -> None:
    just_below = IntentResult(
        intent=Intent.CODE_DEBUG,
        confidence=LOW_CONFIDENCE_THRESHOLD - 0.01,
        source="rule",
    )
    at_threshold = IntentResult(
        intent=Intent.CODE_DEBUG,
        confidence=LOW_CONFIDENCE_THRESHOLD,
        source="rule",
    )
    assert just_below.low_confidence is True
    assert at_threshold.low_confidence is False


def test_structured_input_is_empty_true_when_all_blank() -> None:
    structured = StructuredInput(source="text")
    assert structured.is_empty is True


def test_structured_input_is_empty_false_when_question_set() -> None:
    structured = StructuredInput(source="text", question="Why does this loop?")
    assert structured.is_empty is False
