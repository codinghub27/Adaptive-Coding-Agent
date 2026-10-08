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
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from functools import cache
from types import MappingProxyType
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
    "CONCEPT_ALPHA",
    "CONCEPT_CEILING",
    "CONCEPT_SCORES",
    "HELP_ALPHA",
    "SOFT_SOURCES",
    "apply_event",
    "concept_update",
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

# --- Conceptual evidence (ADAPTIVE-tutoring G2, AD-T5) -----------------------
#: EWMA weight of one graded conceptual answer. A third of `ALPHA`: answering
#: the agent's question shows understanding of ONE idea, not that the learner
#: can produce working code, so three conceptual answers move a skill about as
#: far as one sandbox-verified outcome. Large enough that a session of answers
#: still visibly moves the estimate (0.5 -> ~0.58 after three correct ones).
CONCEPT_ALPHA: Final = 0.1
#: Conceptual evidence alone may never lift a skill into the HARD band
#: (`app.agents.planner.HARD_SKILL`, 0.68): only sandbox evidence can.
#: A test pins CONCEPT_CEILING < HARD_SKILL.
CONCEPT_CEILING: Final = 0.67
CONCEPT_SCORES: Final[Mapping[str, float]] = MappingProxyType(
    {"correct": 0.85, "partial": 0.55, "incorrect": 0.2, "dont_know": 0.25}
)


#: EWMA weight of one "needed the full solution" event. Asking for the code
#: is weak evidence: it says the learner did not get there alone this time,
#: not that they cannot. Five such problems on a topic take a learner from the
#: prior to weak (0.5 -> 0.48 -> 0.462 -> 0.446 -> 0.431 -> 0.418); one sandbox
#: failure does it in one step. Before this, no number moved at all for a
#: learner who asks for code instead of answering questions: 59 real events,
#: none carrying evidence (docs/BEHAVIOR_GAP.md, section 4).
HELP_ALPHA: Final = 0.1

#: Evidence sources that are soft: folded in at a low weight, on the family
#: estimate first, and never able to lift a skill into the HARD band.
SOFT_SOURCES: Final = frozenset({"concept_check", "tutor_reply", "help_needed"})


def help_update(old: float) -> float:
    """One "needed the full solution" event folded into a skill estimate."""
    return round(min(old, smooth(old, FULL_SOLUTION_SCORE, HELP_ALPHA)), 6)


def concept_update(old: float, grade: str) -> float:
    """One graded conceptual answer folded into a skill estimate.

    Rising is capped at `CONCEPT_CEILING` (and never above where the skill
    already was, if sandbox evidence put it higher); falling is not capped.
    """
    new = smooth(old, CONCEPT_SCORES[grade], CONCEPT_ALPHA)
    if new > old:
        new = min(new, max(old, CONCEPT_CEILING))
    return round(new, 6)


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


def _soft_step(event: LearningEventCreate) -> Callable[[float], float] | None:
    """The update one soft-evidence event applies to a skill, or `None` when
    the event is exposure only or carries a sandbox outcome."""
    if event.solved is not None:
        return None
    if event.evidence_source in ("concept_check", "tutor_reply") and event.concept_grade:
        grade = event.concept_grade
        return lambda old: concept_update(old, grade)
    if event.evidence_source == "help_needed":
        return help_update
    return None


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
    soft = _soft_step(event)
    if soft is not None:
        # Soft evidence lands on the FAMILY estimate, and on the topic's own
        # key only once that key carries real (sandbox) evidence. A single
        # answer must not create a topic key that shadows a stronger family
        # estimate in `skill_for` (measured: it slowed adaptation 9 -> 22 turns).
        for key in skill_keys(event):
            current = new_skills.get(key)
            if key.startswith(FAMILY_PREFIX) or (current is not None and current != PRIOR):
                new_skills[key] = soft(current if current is not None else PRIOR)
            elif current is None:
                new_skills[key] = PRIOR
    elif event.solved is None:
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
