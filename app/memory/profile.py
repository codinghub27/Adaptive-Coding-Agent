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
doesn't swing a fresh skill to an extreme.

`ALPHA` was 0.2 until it was calibrated against the evaluation harness. At 0.2
a learner needed two verified failures or three verified successes on ONE topic
before `difficulty_for` returned anything new, so the numbers moved while the
teaching did not -- adaptive in the data, invisible to the learner. At 0.3,
paired with the thresholds in `app.agents.planner`, one verified failure drops
the topic to `easy` (0.5 -> 0.38) and two verified successes raise it to `hard`
(0.5 -> 0.65 -> 0.755). Recovery is symmetric and quick: one success from 0.38
returns 0.566. Raising it further (0.35) made a single turn swing two buckets,
which is jumpy on evidence this sparse. This EWMA update only applies when
an event carries an observed outcome (`event.solved is not None`); see
`apply_event` for the "topic encountered, outcome unknown" case.

None of the functions in this module commit the session — callers own the
transaction and must `await session.commit()` (or roll back) themselves.
"""

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from functools import cache
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

ALPHA: Final = 0.3
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


#: Skill-map keys of this prefix hold a whole pattern FAMILY's estimate
#: (ADAPTIVE-upgrade P6, B5). They live in the same stored map so the event
#: log stays the single source of truth, and are split out by `to_view`.
FAMILY_PREFIX: Final = "family:"

#: Stale evidence decays back toward PRIOR with this half-life: a skill last
#: demonstrated months ago should not still set today's difficulty at full
#: strength. Applied at READ time, so the stored EWMA is never rewritten.
DECAY_HALF_LIFE_DAYS: Final = 30.0


@cache
def _families() -> dict[str, str]:
    # Lazy: keeps this module free of the knowledge stack at import time.
    from app.knowledge.ingest import load_corpus  # noqa: PLC0415

    return {doc.pattern: doc.pattern_family or doc.pattern for doc in load_corpus()}


def family_of(topic: str) -> str | None:
    """The corpus `pattern_family` of `topic`, or `None` for an unknown slug."""
    return _families().get(topic)


def family_key(topic: str) -> str | None:
    family = family_of(topic)
    return f"{FAMILY_PREFIX}{family}" if family is not None else None


def skill_keys(event: LearningEventCreate) -> list[str]:
    """The skill keys an event updates: its topic, and that topic's family.

    The event's `pattern` is no longer a second key (P6, F10): it was
    LLM-proposed (only retrieval-vouched), and it filled profiles with
    patterns the learner never discussed ("Union find", "Hashing" on a trees
    conversation). The family key is what makes evidence on `bfs` inform a
    first `dfs` problem -- graph evidence used to fragment across seven keys
    and accumulate seven times slower (B5).
    """
    keys = [event.topic]
    family = family_key(event.topic)
    if family is not None and family != event.topic:
        keys.append(family)
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


SUGGESTED_FOCUS_TOP_N: Final = 3


def suggested_focus(
    skill_levels: Mapping[str, float], top_n: int = SUGGESTED_FOCUS_TOP_N
) -> list[str]:
    """The topics worth working on next, weakest first.

    Just the lowest-scoring keys the learner has actually been exposed to --
    deliberately not a recommendation engine. A topic sitting at `PRIOR` is
    included: it means the topic was encountered and never demonstrated, which
    is exactly as worth revisiting as one that was failed. Ties break
    alphabetically so the order is stable between requests.
    """
    ordered = sorted(skill_levels.items(), key=lambda item: (item[1], item[0]))
    return [topic for topic, _level in ordered[:top_n]]


def decayed(level: float, seen_at: str | None, now: datetime) -> float:
    """`level` pulled back toward PRIOR by the age of its last evidence."""
    if seen_at is None:
        return level
    try:
        seen = datetime.fromisoformat(seen_at)
    except ValueError:
        return level
    age_days = max(0.0, (now - seen).total_seconds() / 86_400)
    weight = 0.5 ** (age_days / DECAY_HALF_LIFE_DAYS)
    return round(PRIOR + (level - PRIOR) * weight, 6)


def decay_for_outcome(
    skill_levels: Mapping[str, float],
    seen: Mapping[str, str],
    event: LearningEventCreate,
    now: datetime,
) -> dict[str, float]:
    """`skill_levels` with this outcome event's keys decayed to `now` first.

    Decay is applied at read time, so the stored value of an old key is still
    its full-strength EWMA. Blending a new outcome into THAT value (and then
    re-stamping it as fresh) could make a failure RAISE the displayed skill
    (stored 0.9 shown as ~0.53 after 120 days; one failure -> stored 0.63, shown
    0.63). The outcome is blended into what the learner was actually shown.
    Exposure events are left alone: they move nothing.
    """
    out = dict(skill_levels)
    if event.solved is None:
        return out
    for key in skill_keys(event):
        if key in out:
            out[key] = decayed(out[key], seen.get(key), now)
    return out


def to_view(profile: LearnerProfile, now: datetime | None = None) -> LearnerProfileView:
    """Convert a `LearnerProfile` ORM row to its read view.

    Family keys are split into `family_levels`; every level is decayed by the
    age of its last observed outcome; `suggested_focus` lists only topics with
    real evidence (moved off PRIOR); `current_focus` is the topic most recently
    backed by an outcome -- never merely the weakest key (F10).
    """
    moment = now or datetime.now(UTC)
    seen = profile.skill_seen or {}
    topics: dict[str, float] = {}
    families: dict[str, float] = {}
    for key, level in profile.skill_levels.items():
        value = decayed(level, seen.get(key), moment)
        if key.startswith(FAMILY_PREFIX):
            families[key[len(FAMILY_PREFIX) :]] = value
        else:
            topics[key] = value
    evidence = {k: v for k, v in topics.items() if k in seen}
    recent = sorted((at, key) for key, at in seen.items() if key in topics)
    return LearnerProfileView(
        language=profile.language,
        skill_levels=topics,
        family_levels=families,
        learning_preferences=profile.learning_preferences,
        common_errors=common_errors_list(profile.common_errors),
        suggested_focus=suggested_focus(evidence),
        current_focus=recent[-1][1] if recent else None,
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
