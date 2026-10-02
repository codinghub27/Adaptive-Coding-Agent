"""ADAPTIVE-upgrade P6: family-level skill, decay of stale evidence, honest focus."""

from datetime import UTC, datetime, timedelta

import pytest

from app.agents.planner import skill_for
from app.db.models import LearnerProfile
from app.memory.profile import DECAY_HALF_LIFE_DAYS, PRIOR, decayed, to_view
from app.schemas.profile import LearnerProfileView
from eval.adaptation_speed import measure

_NOW = datetime(2026, 10, 3, tzinfo=UTC)


def _profile(skills: dict[str, float], seen: dict[str, str]) -> LearnerProfile:
    return LearnerProfile(
        language=None,
        skill_levels=skills,
        learning_preferences={},
        common_errors={},
        skill_seen=seen,
    )


def test_view_splits_family_keys_and_reports_evidence_only_focus() -> None:
    recent = (_NOW - timedelta(hours=1)).isoformat()
    older = (_NOW - timedelta(hours=5)).isoformat()
    view = to_view(
        _profile(
            {"bfs": 0.73, "dfs": 0.5, "union_find": 0.6, "family:graphs": 0.8},
            {"bfs": older, "union_find": recent, "family:graphs": recent},
        ),
        now=_NOW,
    )
    assert set(view.skill_levels) == {"bfs", "dfs", "union_find"}
    assert "graphs" in view.family_levels
    # `dfs` was only ever exposure (never an outcome): not a focus candidate.
    assert "dfs" not in view.suggested_focus
    assert view.current_focus == "union_find"


def test_stale_evidence_decays_toward_prior_with_the_half_life() -> None:
    seen = (_NOW - timedelta(days=DECAY_HALF_LIFE_DAYS)).isoformat()
    assert decayed(0.9, seen, _NOW) == pytest.approx(PRIOR + 0.2)
    assert decayed(0.9, None, _NOW) == 0.9  # unknown age: left as is


def test_a_pattern_with_no_evidence_plans_from_its_family() -> None:
    profile = LearnerProfileView.empty().model_copy(
        update={"skill_levels": {"bfs": 0.8}, "family_levels": {"graphs": 0.75}}
    )
    assert skill_for(profile, "bfs") == 0.8
    assert skill_for(profile, "dijkstra") == 0.75  # never practised, family known
    assert skill_for(profile, "trie") == PRIOR


def test_family_aggregation_adapts_faster_than_per_pattern_keys() -> None:
    """The measured speedup recorded in the upgrade log, pinned."""
    rows = measure()
    before = rows["before (per-pattern key)"]
    after = rows["after (family-aware)"]
    assert after == [3, 1]
    assert before == [15, 5]


def test_a_failure_on_old_evidence_never_raises_the_shown_skill() -> None:
    """Code review P6: blend the outcome into the DECAYED value."""
    from app.memory.profile import apply_event, decay_for_outcome
    from app.schemas.event import LearningEventCreate

    seen_at = (_NOW - timedelta(days=120)).isoformat()
    stored = {"trees": 0.9}
    shown_before = decayed(0.9, seen_at, _NOW)
    event = LearningEventCreate(topic="trees", solved=False)
    baseline = decay_for_outcome(stored, {"trees": seen_at}, event, _NOW)
    after, _ = apply_event(baseline, {}, event)
    assert after["trees"] < shown_before
