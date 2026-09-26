"""Learner profile store: per-user skill levels, preferences, and errors.

**Events are the source of truth.** `skill_levels` and `common_errors` on
`LearnerProfile` are a derived projection, incrementally updated by
`apply_event` as each `LearningEvent` is recorded (see
`app.memory.events.record_event`) and fully rebuildable from the event log by
`app.memory.events.rebuild_profile`. `learning_preferences` and `language` are
user-declared settings, never derived from events.

Skill levels are tracked per topic/pattern key as an exponentially weighted
moving average (EWMA) of a per-event outcome score in `[0, 1]`:

    new = (1 - ALPHA) * old + ALPHA * outcome_score(event)

with an unseen key starting from `PRIOR` instead of 0, so a single event
doesn't swing a fresh skill to an extreme. This EWMA update only applies when
an event carries an observed outcome (`event.solved is not None`); see
`apply_event` for the "topic encountered, outcome unknown" case.

None of the functions in this module commit the session — callers own the
transaction and must `await session.commit()` (or roll back) themselves.
"""

import uuid
from collections.abc import Mapping
from typing import Final

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LearnerProfile
from app.schemas.event import LearningEventCreate
from app.schemas.profile import LearnerProfileView

__all__ = [
    "ALPHA",
    "COMMON_ERRORS_TOP_N",
    "FULL_SOLUTION_SCORE",
    "HINT_PENALTY",
    "MIN_SOLVED_SCORE",
    "PRIOR",
    "UNSOLVED_SCORE",
    "apply_event",
    "common_errors_list",
    "ensure_profile",
    "get_profile",
    "outcome_score",
    "set_language",
    "set_learning_preferences",
    "skill_keys",
    "smooth",
    "to_view",
]

ALPHA: Final = 0.2
PRIOR: Final = 0.5
MIN_SOLVED_SCORE: Final = 0.4
HINT_PENALTY: Final = 0.15
FULL_SOLUTION_SCORE: Final = 0.3
UNSOLVED_SCORE: Final = 0.1
COMMON_ERRORS_TOP_N: Final = 5


def outcome_score(event: LearningEventCreate) -> float:
    """Map a learning event's outcome to a score in [0, 1] for EWMA input.

    Requires an observed outcome: raises if `event.solved is None`. Callers
    must branch on `event.solved is None` (see `apply_event`) before calling
    this -- an unobserved outcome must never silently fall through to
    `UNSOLVED_SCORE`.
    """
    if event.solved is None:
        raise AssertionError("outcome_score requires an observed outcome (event.solved is None)")
    if event.solved and not event.needed_full_solution:
        return max(MIN_SOLVED_SCORE, 1.0 - HINT_PENALTY * event.hints_used)
    if event.solved and event.needed_full_solution:
        return FULL_SOLUTION_SCORE
    return UNSOLVED_SCORE


def smooth(old: float, outcome: float, alpha: float = ALPHA) -> float:
    """EWMA-update `old` toward `outcome`, clamped to [0, 1] and rounded."""
    value = (1 - alpha) * old + alpha * outcome
    value = min(1.0, max(0.0, value))
    return round(value, 6)


def skill_keys(event: LearningEventCreate) -> list[str]:
    """The skill dictionary keys an event should update: topic, and pattern
    if present and distinct from topic."""
    keys = [event.topic]
    if event.pattern and event.pattern != event.topic:
        keys.append(event.pattern)
    return keys


def apply_event(
    skill_levels: Mapping[str, float],
    common_errors: Mapping[str, int],
    event: LearningEventCreate,
) -> tuple[dict[str, float], dict[str, int]]:
    """Pure projection step: fold one event into skill levels and error counts.

    When `event.solved is None` (the topic was encountered this turn -- e.g.
    a hint request -- but no outcome was observed), skill levels are **not**
    EWMA-updated: exposure to a topic is not evidence of success or failure,
    so smoothing toward a fixed outcome score would either falsely reward or
    (via a mid-range `PRIOR`) falsely decay a skill just for asking a
    question. Instead, a missing key is created at `PRIOR` (so the topic
    shows up in the profile at all) and an existing key is left exactly as
    it is -- bit-for-bit unchanged. Error tags in `event.errors` are still
    counted either way.

    Returns new dicts; never mutates the inputs.
    """
    new_skills = dict(skill_levels)
    if event.solved is None:
        for key in skill_keys(event):
            if key not in new_skills:
                new_skills[key] = PRIOR
    else:
        outcome = outcome_score(event)
        for key in skill_keys(event):
            old = new_skills.get(key, PRIOR)
            new_skills[key] = smooth(old, outcome)

    new_errors = dict(common_errors)
    for tag in event.errors:
        new_errors[tag] = new_errors.get(tag, 0) + 1

    return new_skills, new_errors


def common_errors_list(counts: Mapping[str, int], top_n: int = COMMON_ERRORS_TOP_N) -> list[str]:
    """The `top_n` most frequent error tags, ordered by count desc then tag asc."""
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [tag for tag, _count in ordered[:top_n]]


def to_view(profile: LearnerProfile) -> LearnerProfileView:
    """Convert a `LearnerProfile` ORM row to its read view."""
    return LearnerProfileView(
        language=profile.language,
        skill_levels=profile.skill_levels,
        learning_preferences=profile.learning_preferences,
        common_errors=common_errors_list(profile.common_errors),
    )


async def ensure_profile(
    session: AsyncSession, user_id: uuid.UUID, *, for_update: bool = False
) -> LearnerProfile:
    """Return the user's `LearnerProfile` row, creating it if absent.

    Only use this for write paths: it always issues an insert attempt (a
    no-op `ON CONFLICT DO NOTHING` if the row already exists), so it is not
    read-only. The `user_id` row must already exist (`user_id` is a foreign
    key to `users.id`); callers are responsible for ensuring the user exists
    first. Does not commit; the caller's transaction persists the insert.
    """
    insert_stmt = (
        pg_insert(LearnerProfile)
        .values(user_id=user_id)
        .on_conflict_do_nothing(index_elements=["user_id"])
    )
    await session.execute(insert_stmt)

    stmt = select(LearnerProfile).where(LearnerProfile.user_id == user_id)
    stmt = stmt.execution_options(populate_existing=True)
    if for_update:
        stmt = stmt.with_for_update()
    result = await session.execute(stmt)
    return result.scalar_one()


async def get_profile(session: AsyncSession, user_id: uuid.UUID) -> LearnerProfileView:
    """Return the user's profile view, read-only.

    Issues a plain `SELECT`; never inserts. If the user has no profile row
    yet, returns an empty default view without creating one. Use
    `ensure_profile` (via a write path such as `record_event`) to create the
    row.
    """
    stmt = select(LearnerProfile).where(LearnerProfile.user_id == user_id)
    result = await session.execute(stmt)
    profile = result.scalar_one_or_none()
    if profile is None:
        return LearnerProfileView.empty()
    return to_view(profile)


async def set_learning_preferences(
    session: AsyncSession, user_id: uuid.UUID, preferences: Mapping[str, bool]
) -> LearnerProfileView:
    """Merge `preferences` into the user's existing preferences and return the view."""
    profile = await ensure_profile(session, user_id, for_update=True)
    merged = dict(profile.learning_preferences)
    merged.update(preferences)
    profile.learning_preferences = merged
    await session.flush()
    return to_view(profile)


async def set_language(
    session: AsyncSession, user_id: uuid.UUID, language: str | None
) -> LearnerProfileView:
    """Set the user's declared language preference and return the view."""
    profile = await ensure_profile(session, user_id, for_update=True)
    profile.language = language
    await session.flush()
    return to_view(profile)
