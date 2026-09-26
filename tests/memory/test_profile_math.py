"""Pure unit tests for `app.memory.profile` math and the profile/event schemas.

No database, no markers: these must always run.
"""

import pytest
from pydantic import ValidationError

from app.memory.events import INTENT_TO_HELP
from app.memory.profile import (
    PRIOR,
    apply_event,
    common_errors_list,
    outcome_score,
    skill_keys,
    smooth,
)
from app.schemas.event import LearningEventCreate
from app.schemas.intent import Intent
from app.schemas.profile import LearnerProfileView


def _event(**overrides: object) -> LearningEventCreate:
    params: dict[str, object] = {"topic": "arrays", "solved": True}
    params.update(overrides)
    return LearningEventCreate(**params)  # pyright: ignore[reportArgumentType]


# ---------------------------------------------------------------------------
# smooth
# ---------------------------------------------------------------------------


def test_smooth_basic_ewma_step() -> None:
    assert smooth(0.5, 1.0) == 0.6


def test_smooth_clamps_above_one() -> None:
    # alpha outside [0, 1] pushes the weighted average past 1.0; smooth must clamp.
    assert smooth(0.0, 1.0, alpha=2.0) == 1.0


def test_smooth_clamps_below_zero() -> None:
    assert smooth(1.0, 0.0, alpha=2.0) == 0.0


def test_smooth_alpha_zero_leaves_old_unchanged() -> None:
    assert smooth(0.7, 0.1, alpha=0.0) == 0.7


def test_smooth_alpha_one_equals_outcome() -> None:
    assert smooth(0.3, 0.9, alpha=1.0) == 0.9


# ---------------------------------------------------------------------------
# outcome_score
# ---------------------------------------------------------------------------


def test_outcome_score_solved_zero_hints_is_one() -> None:
    assert outcome_score(_event(solved=True, hints_used=0)) == 1.0


def test_outcome_score_solved_two_hints() -> None:
    assert outcome_score(_event(solved=True, hints_used=2)) == pytest.approx(0.7)


def test_outcome_score_solved_ten_hints_hits_floor() -> None:
    assert outcome_score(_event(solved=True, hints_used=10)) == pytest.approx(0.4)


def test_outcome_score_solved_needed_full_solution() -> None:
    assert outcome_score(
        _event(solved=True, needed_full_solution=True, hints_used=0)
    ) == pytest.approx(0.3)


def test_outcome_score_unsolved() -> None:
    assert outcome_score(_event(solved=False)) == pytest.approx(0.1)


def test_outcome_score_raises_for_unobserved_outcome() -> None:
    # Structural guard: `outcome_score` must never be reachable for a
    # `solved=None` event -- that fall-through (silently landing on
    # `UNSOLVED_SCORE`) is exactly the bug this module exists to prevent.
    with pytest.raises(AssertionError):
        outcome_score(_event(solved=None))


# ---------------------------------------------------------------------------
# skill_keys
# ---------------------------------------------------------------------------


def test_skill_keys_topic_and_distinct_pattern() -> None:
    event = _event(topic="arrays", pattern="sliding_window")
    assert skill_keys(event) == ["arrays", "sliding_window"]


def test_skill_keys_pattern_equal_to_topic_is_one_key() -> None:
    event = _event(topic="arrays", pattern="arrays")
    assert skill_keys(event) == ["arrays"]


def test_skill_keys_no_pattern_is_topic_only() -> None:
    event = _event(topic="arrays", pattern=None)
    assert skill_keys(event) == ["arrays"]


# ---------------------------------------------------------------------------
# apply_event
# ---------------------------------------------------------------------------


def test_apply_event_uses_prior_for_unseen_key() -> None:
    event = _event(topic="arrays", solved=True, hints_used=0)
    skills, _errors = apply_event({}, {}, event)
    assert skills == {"arrays": smooth(PRIOR, outcome_score(event))}


def test_apply_event_does_not_mutate_inputs() -> None:
    skills_in: dict[str, float] = {"arrays": 0.5}
    errors_in: dict[str, int] = {"off_by_one": 1}
    event = _event(topic="arrays", errors=["off_by_one"], solved=True)

    new_skills, new_errors = apply_event(skills_in, errors_in, event)

    assert skills_in == {"arrays": 0.5}
    assert errors_in == {"off_by_one": 1}
    assert new_skills != skills_in or new_errors != errors_in


def test_apply_event_counts_error_tags() -> None:
    event = _event(topic="arrays", errors=["off_by_one", "edge_cases"], solved=True)
    _skills, errors = apply_event({}, {}, event)
    assert errors == {"off_by_one": 1, "edge_cases": 1}


def test_apply_event_solved_true_regression() -> None:
    # Pins the current EWMA numbers for a plain solved event so a future
    # change to `outcome_score`/`smooth` is a deliberate, visible decision.
    event = _event(topic="arrays", solved=True, hints_used=0)
    skills, _errors = apply_event({"arrays": 0.5}, {}, event)
    assert skills == {"arrays": 0.6}


def test_apply_event_solved_false_regression() -> None:
    event = _event(topic="arrays", solved=False)
    skills, _errors = apply_event({"arrays": 0.5}, {}, event)
    assert skills == {"arrays": pytest.approx(0.42)}


# ---------------------------------------------------------------------------
# apply_event: solved=None ("topic encountered, outcome unknown")
# ---------------------------------------------------------------------------


def test_apply_event_unobserved_creates_missing_key_at_prior() -> None:
    event = _event(topic="arrays", solved=None)
    skills, _errors = apply_event({}, {}, event)
    assert skills == {"arrays": PRIOR}


def test_apply_event_unobserved_leaves_existing_high_skill_bit_identical() -> None:
    event = _event(topic="arrays", solved=None)
    skills, _errors = apply_event({"arrays": 0.9}, {}, event)
    assert skills == {"arrays": 0.9}


def test_apply_event_unobserved_leaves_existing_low_skill_bit_identical() -> None:
    event = _event(topic="arrays", solved=None)
    skills, _errors = apply_event({"arrays": 0.1}, {}, event)
    assert skills == {"arrays": 0.1}


def test_apply_event_unobserved_still_counts_error_tags() -> None:
    event = _event(topic="arrays", solved=None, errors=["off_by_one"])
    _skills, errors = apply_event({}, {}, event)
    assert errors == {"off_by_one": 1}


def test_apply_event_unobserved_does_not_mutate_inputs() -> None:
    skills_in: dict[str, float] = {"arrays": 0.9}
    event = _event(topic="arrays", solved=None)
    new_skills, _errors = apply_event(skills_in, {}, event)
    assert skills_in == {"arrays": 0.9}
    assert new_skills == {"arrays": 0.9}
    assert new_skills is not skills_in


def test_apply_event_repeated_application_converges_and_never_exceeds_one() -> None:
    event = _event(topic="arrays", solved=True, hints_used=0)  # outcome_score == 1.0
    skills: dict[str, float] = {}
    errors: dict[str, int] = {}
    previous = 0.0
    for _ in range(100):
        skills, errors = apply_event(skills, errors, event)
        current = skills["arrays"]
        assert current >= previous
        assert current <= 1.0
        previous = current
    assert previous == pytest.approx(1.0, abs=1e-4)


# ---------------------------------------------------------------------------
# common_errors_list
# ---------------------------------------------------------------------------


def test_common_errors_list_freq_desc_tie_alphabetical_top_n() -> None:
    counts = {"b": 2, "a": 2, "c": 1, "d": 3}
    assert common_errors_list(counts, top_n=3) == ["d", "a", "b"]


# ---------------------------------------------------------------------------
# INTENT_TO_HELP
# ---------------------------------------------------------------------------


def test_intent_to_help_covers_every_intent() -> None:
    assert set(INTENT_TO_HELP) == set(Intent)


# ---------------------------------------------------------------------------
# Schema validation: phase-doc target payloads
# ---------------------------------------------------------------------------

PROFILE_PAYLOAD = {
    "language": "Python",
    "skill_levels": {"arrays": 0.75, "sliding_window": 0.55, "dp": 0.30},
    "learning_preferences": {
        "prefers_hints": True,
        "likes_step_by_step": True,
        "wants_line_by_line_explanations": True,
    },
    "common_errors": ["off_by_one", "incorrect_window_shrinking", "edge_cases"],
}

EVENT_PAYLOAD = {
    "problem": "...",
    "topic": "arrays",
    "pattern": "sliding_window",
    "difficulty": "easy",
    "requested_help": "explanation",
    "hints_used": 2,
    "needed_full_solution": False,
    "errors": [],
    "solved": True,
    "time_spent": 12,
    "concepts": ["Chebyshev distance"],
}


def test_profile_payload_validates() -> None:
    view = LearnerProfileView.model_validate(PROFILE_PAYLOAD)
    assert view.skill_levels["arrays"] == 0.75
    assert view.common_errors == ["off_by_one", "incorrect_window_shrinking", "edge_cases"]


def test_event_payload_validates() -> None:
    event = LearningEventCreate.model_validate(EVENT_PAYLOAD)
    assert event.topic == "arrays"
    assert event.pattern == "sliding_window"
    assert event.concepts == ["Chebyshev distance"]


def test_event_solved_none_validates() -> None:
    event = LearningEventCreate.model_validate({**EVENT_PAYLOAD, "solved": None})
    assert event.solved is None


def test_profile_skill_value_above_one_rejected() -> None:
    payload = dict(PROFILE_PAYLOAD)
    payload["skill_levels"] = {"arrays": 1.5}
    with pytest.raises(ValidationError):
        LearnerProfileView.model_validate(payload)


def test_event_topic_slug_normalized() -> None:
    event = LearningEventCreate.model_validate({**EVENT_PAYLOAD, "topic": "Sliding Window"})
    assert event.topic == "sliding_window"


def test_event_problem_truncated_over_200_chars() -> None:
    long_problem = "x" * 250
    event = LearningEventCreate.model_validate({**EVENT_PAYLOAD, "problem": long_problem})
    assert event.problem is not None
    assert len(event.problem) == 200


def test_hints_used_over_1000_rejected() -> None:
    with pytest.raises(ValidationError):
        LearningEventCreate.model_validate({**EVENT_PAYLOAD, "hints_used": 1001})


def test_errors_with_non_string_item_rejected() -> None:
    with pytest.raises(ValidationError):
        LearningEventCreate.model_validate({**EVENT_PAYLOAD, "errors": [1, "off_by_one"]})


def test_pattern_blank_after_slug_becomes_none() -> None:
    event = LearningEventCreate.model_validate({**EVENT_PAYLOAD, "pattern": "  "})
    assert event.pattern is None


def test_concepts_stripped_and_empty_dropped() -> None:
    event = LearningEventCreate.model_validate({**EVENT_PAYLOAD, "concepts": [" a ", "  "]})
    assert event.concepts == ["a"]
