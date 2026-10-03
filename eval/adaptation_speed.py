"""Adaptation speed: how many observed outcomes before the planner's difficulty
for a pattern FAMILY leaves the PRIOR default (ADAPTIVE-upgrade P6).

    .\\venv\\Scripts\\python.exe -m eval.adaptation_speed

Deterministic and LLM-free: it replays learning events through the real
`app.memory.profile.apply_event` and asks the real planner skill lookup what
difficulty the NEXT problem would be planned at. A learner works through the
graph family the way a schedule rotates through it (bfs, dfs, union_find,
topological_sort, dijkstra, ...), solving each with one hint, or failing each.

The number reported is the first turn on which the next problem's planned
difficulty differs from the PRIOR default -- i.e. the first turn on which the
learner's evidence visibly changes what the agent does.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping, Sequence

from app.agents.planner import difficulty_for, skill_for
from app.memory.profile import FAMILY_PREFIX, PRIOR, apply_event
from app.schemas.event import LearningEventCreate
from app.schemas.profile import LearnerProfileView

ROTATION: tuple[str, ...] = ("bfs", "dfs", "union_find", "topological_sort", "dijkstra")
MAX_TURNS = 30

SkillLookup = Callable[[LearnerProfileView, str], float]


def _event(topic: str, solved: bool) -> LearningEventCreate:
    return LearningEventCreate(topic=topic, solved=solved, hints_used=1)


def per_key_skill(profile: LearnerProfileView, topic: str) -> float:
    """The pre-P6 lookup: the topic's own key, else PRIOR."""
    return profile.skill_levels.get(topic, PRIOR)


def turns_to_adapt(lookup: SkillLookup, solved: bool, families: bool) -> int | None:
    skills: dict[str, float] = {}
    errors: dict[str, int] = {}
    default = difficulty_for(PRIOR)
    for turn in range(1, MAX_TURNS + 1):
        topic = ROTATION[(turn - 1) % len(ROTATION)]
        skills, errors = apply_event(skills, errors, _event(topic, solved))
        if not families:
            skills = {k: v for k, v in skills.items() if not k.startswith(FAMILY_PREFIX)}
        # The same split `app.memory.profile.to_view` makes: family keys are
        # stored with the topics and served separately.
        topics = {k: v for k, v in skills.items() if not k.startswith(FAMILY_PREFIX)}
        families_view = {
            k[len(FAMILY_PREFIX) :]: v for k, v in skills.items() if k.startswith(FAMILY_PREFIX)
        }
        profile = LearnerProfileView.empty().model_copy(
            update={"skill_levels": topics, "family_levels": families_view}
        )
        upcoming = ROTATION[turn % len(ROTATION)]
        if difficulty_for(lookup(profile, upcoming)) != default:
            return turn
    return None


def measure() -> Mapping[str, Sequence[int | None]]:
    rows: dict[str, list[int | None]] = {}
    for label, lookup, families in (
        ("before (per-pattern key)", per_key_skill, False),
        ("after (family-aware)", skill_for, True),
    ):
        rows[label] = [
            turns_to_adapt(lookup, solved=True, families=families),
            turns_to_adapt(lookup, solved=False, families=families),
        ]
    return rows


# --- ADAPTIVE-tutoring Q4: conceptual evidence ---------------------------------
#
# Same rotation, family-aware lookup. Each turn the learner answers one of the
# agent's guiding questions (a `concept_check` event); every third turn they
# also get a sandbox verdict. "sandbox only" drops the concept events -- the
# behaviour before Q4, when graded answers were not evidence at all.

SANDBOX_EVERY = 3


def _concept_event(topic: str, correct: bool) -> LearningEventCreate:
    return LearningEventCreate(
        topic=topic,
        solved=None,
        evidence_source="concept_check",
        concept_grade="correct" if correct else "incorrect",
    )


def turns_to_adapt_with_concepts(good: bool, concepts: bool) -> int | None:
    skills: dict[str, float] = {}
    errors: dict[str, int] = {}
    default = difficulty_for(PRIOR)
    for turn in range(1, MAX_TURNS + 1):
        topic = ROTATION[(turn - 1) % len(ROTATION)]
        if concepts:
            skills, errors = apply_event(skills, errors, _concept_event(topic, good))
        if turn % SANDBOX_EVERY == 0:
            skills, errors = apply_event(skills, errors, _event(topic, good))
        topics = {k: v for k, v in skills.items() if not k.startswith(FAMILY_PREFIX)}
        families_view = {
            k[len(FAMILY_PREFIX) :]: v for k, v in skills.items() if k.startswith(FAMILY_PREFIX)
        }
        profile = LearnerProfileView.empty().model_copy(
            update={"skill_levels": topics, "family_levels": families_view}
        )
        upcoming = ROTATION[turn % len(ROTATION)]
        if difficulty_for(skill_for(profile, upcoming)) != default:
            return turn
    return None


def concept_only_ceiling(good: bool) -> str:
    """The difficulty conceptual evidence ALONE can reach (never past HARD)."""
    skills: dict[str, float] = {}
    errors: dict[str, int] = {}
    for turn in range(1, MAX_TURNS + 1):
        topic = ROTATION[(turn - 1) % len(ROTATION)]
        skills, errors = apply_event(skills, errors, _concept_event(topic, good))
    topics = {k: v for k, v in skills.items() if not k.startswith(FAMILY_PREFIX)}
    families_view = {
        k[len(FAMILY_PREFIX) :]: v for k, v in skills.items() if k.startswith(FAMILY_PREFIX)
    }
    profile = LearnerProfileView.empty().model_copy(
        update={"skill_levels": topics, "family_levels": families_view}
    )
    return difficulty_for(skill_for(profile, ROTATION[0]))


def main() -> int:
    print(f"{'lookup':28s} {'all solved':>11s} {'all failed':>11s}")
    for label, (up, down) in measure().items():
        print(f"{label:28s} {str(up):>11s} {str(down):>11s}")
    print()
    print(f"{'evidence (1 sandbox / 3 turns)':34s} {'all good':>9s} {'all bad':>9s}")
    for label, concepts in (
        ("sandbox only (before Q4)", False),
        ("sandbox + concept checks", True),
    ):
        good = turns_to_adapt_with_concepts(True, concepts)
        bad = turns_to_adapt_with_concepts(False, concepts)
        print(f"{label:34s} {str(good):>9s} {str(bad):>9s}")
    print(
        f"concept checks alone, {MAX_TURNS} turns: "
        f"all correct -> {concept_only_ceiling(True)}, all wrong -> {concept_only_ceiling(False)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
