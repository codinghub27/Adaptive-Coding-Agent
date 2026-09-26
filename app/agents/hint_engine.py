"""Deterministic hint-ladder engine: decides which rung and composes its text.

Pure and LLM-free by design (Phase 07 scope): given a `TeachingPlan` (this
turn's assistance-level ceiling) and a `HintProgress` (how far the learner
has already climbed on this problem), `next_hint` computes the next ladder
rung and composes guidance-level text for it. It never calls an LLM, never
performs I/O, and never mutates its inputs.

The 7-rung ladder (`HintLevel`, owned by `app.schemas.agent_results`) goes
from a bare nudge (L0) up to a full solution (L6):

    L0 nudge -> L1 what to track -> L2 data structure -> L3 concrete idea
    -> L4 pseudocode -> L5 partial -> L6 full

Climbing is monotonic and at most one rung per call, capped by
`MAX_HINT_LEVEL_FOR_ASSISTANCE[plan.assistance_level]` -- the single source
of truth for the ceiling, owned by `app.schemas.agent_results`. This module
never re-derives or overrides that mapping; it is the core security property
of the hint ladder (a low assistance level must never leak a full solution).

Hint text is composed only from trusted-shape fields: `TeachingPlan.topic`,
`TeachingPlan.watch_errors`, `TeachingPlan.difficulty`, and, where available,
the `chunk.topic` / `chunk.pattern` labels of retrieved `RetrievalHit`s (the
knowledge corpus, not user input). The learner's own free text --
`StructuredInput.question` / `.problem` / `.code` / `.error` -- is untrusted
content and is **never** echoed into the returned text, matching the
convention documented in `app.graph.nodes`'s module docstring. `problem` is
accepted here only for API symmetry with future callers; this module does
not read any of its fields.

Even at L4/L5/L6 the text only *describes* the shape of a solution -- this
module never fabricates actual code. Generating real code is the DSA agent's
job (Phase 07 P3), and it is only permitted to do so once the ladder has
reached L6 (`DSAResult` enforces that pairing).
"""

from collections.abc import Sequence

from app.schemas.agent_results import MAX_HINT_LEVEL_FOR_ASSISTANCE, HintLevel, HintResult
from app.schemas.base import APIModel
from app.schemas.input import StructuredInput
from app.schemas.knowledge import RetrievalHit
from app.schemas.plan import TeachingPlan

__all__ = ["HintProgress", "next_hint"]


class HintProgress(APIModel):
    """How far the learner has climbed the hint ladder on this problem so far."""

    last_level: HintLevel | None = None
    solved: bool = False


def _context_labels(context: Sequence[RetrievalHit]) -> list[str]:
    """Distinct, trusted-shape topic/pattern labels drawn from retrieved chunks.

    These come from the knowledge corpus (`app.schemas.knowledge.KnowledgeChunk`),
    not from the learner's own input, so they are safe to compose into hint text.
    """
    seen: list[str] = []
    for hit in context:
        for label in (hit.chunk.pattern, hit.chunk.topic):
            if label and label not in seen:
                seen.append(label)
    return seen


def _humanize(label: str) -> str:
    """Turn an internal slug (`sliding_window`) into prose (`sliding window`).

    Topic/pattern labels are storage slugs; showing them raw to a learner
    leaks internal formatting into the teaching voice.
    """
    return label.replace("_", " ").replace("-", " ").strip()


def _shape_hint(topic: str | None, context_labels: Sequence[str]) -> str:
    """Pick a data-structure hint that says something the topic has not.

    The first retrieved label is often *the topic itself*, which produced the
    self-referential "For a sliding_window problem, sliding_window is often
    the right shape". Only a label that differs from the topic adds
    information; otherwise fall back to the generic phrasing.
    """
    topic_key = (topic or "").replace("_", " ").replace("-", " ").strip().casefold()
    for label in context_labels:
        if _humanize(label).casefold() != topic_key:
            return _humanize(label)
    return "a structure that supports fast lookups or ordered access"


def _rung_text(level: HintLevel, plan: TeachingPlan, context_labels: Sequence[str]) -> str:
    """Compose the guidance-level text for one ladder rung.

    Built only from `plan`'s structured fields and `context_labels`; never
    from the learner's raw question/problem/code/error text.
    """
    topic = _humanize(plan.topic) if plan.topic else "this problem"
    watch = ", ".join(plan.watch_errors) if plan.watch_errors else None

    if level == HintLevel.L0_NUDGE:
        return (
            f"Before writing any code, restate the problem in your own words and "
            f"identify the inputs, outputs, and constraints. This is rated "
            f"'{plan.difficulty}' -- take a moment to make sure you understand what "
            "is actually being asked before you start."
        )

    if level == HintLevel.L1_WHAT_TO_TRACK:
        text = (
            f"Think about what state you need to track while working through this "
            f"{topic} problem: which values change at each step, and which ones you "
            "need to remember from earlier steps to make a later decision."
        )
        if watch:
            text += f" Also watch for mistakes that have tripped you up before: {watch}."
        return text

    if level == HintLevel.L2_DATA_STRUCTURE:
        shape = _shape_hint(plan.topic, context_labels)
        return (
            f"Consider what data structure would let you track that state "
            f"efficiently. For a {topic} problem, {shape} is often the right shape "
            "-- ask yourself what operations you need it to support quickly."
        )

    if level == HintLevel.L3_CONCRETE_IDEA:
        return (
            "Sketch a concrete approach: decide on the single pass or traversal "
            "you'd make over the input, and exactly what you'd read from and "
            "write to the data structure at each step."
        )

    if level == HintLevel.L4_PSEUDOCODE:
        return (
            "Write pseudocode for that approach, step by step: how you initialize "
            "your state, the loop or recursion structure, the update rule applied "
            "at each step, and what gets returned at the end. Keep it to steps, "
            "not real syntax yet."
        )

    if level == HintLevel.L5_PARTIAL:
        return (
            "Here is a partial structure to build from: set up the initial state, "
            "write the loop skeleton over the input, and leave the core update "
            "step for you to fill in yourself using the pseudocode from the "
            "previous hint."
        )

    # HintLevel.L6_FULL
    return (
        "You've reached the top of the hint ladder for this turn: a full worked "
        "solution is warranted now. This module only confirms that -- the actual "
        "code comes from the DSA solver, gated on reaching this level."
    )


def next_hint(
    problem: StructuredInput | None,
    plan: TeachingPlan,
    progress: HintProgress,
    *,
    context: Sequence[RetrievalHit] = (),
) -> HintResult | None:
    """Compute the next hint-ladder rung for this turn, or `None` if done.

    `problem` is accepted for signature symmetry but deliberately unread:
    its fields are untrusted learner text and must never be echoed into
    `HintResult.text` (see module docstring). `context` is optional and may
    be empty; only trusted-shape chunk labels are ever drawn from it.

    Rules (see module docstring for the security rationale):
    - `progress.solved` -> `None` (stop hinting).
    - First ask (`last_level is None`) -> L0, unless the ceiling is lower
      (defensive; never happens with the current ceiling mapping).
    - Otherwise -> `min(last_level + 1, ceiling)`: at most one rung per call,
      never above the ceiling, idempotent once the ceiling is reached.
    """
    del problem  # untrusted; deliberately not read -- see module docstring

    if progress.solved:
        return None

    ceiling = MAX_HINT_LEVEL_FOR_ASSISTANCE[plan.assistance_level]

    if progress.last_level is None:
        level = HintLevel(min(int(HintLevel.L0_NUDGE), int(ceiling)))
    else:
        level = HintLevel(min(int(progress.last_level) + 1, int(ceiling)))

    context_labels = _context_labels(context)
    text = _rung_text(level, plan, context_labels)

    return HintResult(
        level=level,
        text=text,
        is_terminal=level == ceiling,
        reveals_code=level >= HintLevel.L5_PARTIAL,
        ceiling=ceiling,
    )
