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
the retrieved `KnowledgeChunk`s of `RetrievalHit`s (the knowledge corpus, not
user input) -- both their `topic` / `pattern` labels (used since before this
packet) and now, for Packet P5, a bounded amount of the chunk's own curated
prose. The learner's own free text -- `StructuredInput.question` / `.problem`
/ `.code` / `.error` -- is untrusted content and is **never** echoed into the
returned text, matching the convention documented in `app.graph.nodes`'s
module docstring. `problem` is accepted here only for API symmetry with
future callers; this module does not read any of its fields.

**Grounding (Packet P5).** A rung may quote a *bounded, single-sentence*
clause of trusted corpus text to make its guidance specific to the actual
topic/pattern, instead of a generic template. Trust is established two ways,
both required:

- Relevance: `hit.score >= MIN_GROUNDING_SCORE` (a retrieved-but-irrelevant
  chunk is noise, not evidence -- same calibration as
  `app.agents.planner.MIN_RETRIEVAL_TOPIC_SCORE`, duplicated here rather than
  imported to avoid a circular import: that module imports `HintProgress`
  from this one).
- Section: the chunk's corpus section (`KnowledgeChunk.metadata["section"]`)
  is not in `_UNGROUNDABLE_SECTIONS` -- never `general_template` (literal
  solution code) or `representative_problems` (just external links).

When no hit clears both bars this turn, every rung silently reverts to
today's generic, topic-agnostic phrasing -- a vague-but-honest hint beats a
confident wrong one built from noise. `_prose` additionally strips any
fenced code block defensively (in case a future corpus edit embeds one
outside `general_template`) and flattens markdown to plain prose, since
`app.response.format` applies no escaping/markdown layer of its own to hint
text -- this module must not hand it anything but plain sentences.

Even at L4/L5/L6 the text only *describes* the shape of a solution -- this
module never fabricates actual code, grounded or not. Generating real code
is the DSA agent's job (Phase 07 P3), and it is only permitted to do so once
the ladder has reached L6 (`DSAResult` enforces that pairing).
"""

import re
from collections.abc import Sequence
from typing import Final

from app.schemas.agent_results import MAX_HINT_LEVEL_FOR_ASSISTANCE, HintLevel, HintResult
from app.schemas.base import APIModel
from app.schemas.input import StructuredInput
from app.schemas.knowledge import KnowledgeChunk, RetrievalHit
from app.schemas.plan import AssistanceLevel, TeachingPlan

__all__ = ["HintProgress", "base_ladder_ceiling", "ladder_ceiling", "next_hint"]


class HintProgress(APIModel):
    """How far the learner has climbed the hint ladder on this problem so far.

    `has_verified_attempt` is a distinct signal from `solved`: it is set once
    a sandbox has actually produced a `Verdict` with status `pass` or `fail`
    (never `inconclusive`/`skipped`) for this problem, regardless of which
    way that verdict went -- a learner who submitted code that ran and
    *failed* has still demonstrated real effort, and `solved` alone can't
    tell you that (it can be `False` for "never attempted" and "attempted
    and failed" alike). Monotonic once persisted: see
    `app.memory.hint_progress.save_hint_progress`.
    """

    last_level: HintLevel | None = None
    solved: bool = False
    has_verified_attempt: bool = False
    #: The ladder's ceiling, fixed when the ladder started (ADAPTIVE-upgrade P1,
    #: F3), or `None` for a new ladder / a row written before it was stored.
    ceiling: HintLevel | None = None


def ladder_ceiling(plan: TeachingPlan, progress: HintProgress) -> HintLevel:
    """This turn's hint-ladder ceiling: the ladder's own, fixed one.

    The per-turn ceiling (`MAX_HINT_LEVEL_FOR_ASSISTANCE[assistance_level]`)
    moved with the intent of each message -- "next hint" classifies as
    `DSA_HINT` (ceiling L2) while the problem itself was `DSA_SOLVE` (L3) --
    so the learner saw "Hint 1 of 4", then "Hint 2 of 3" (F3). Once a ladder
    has a stored ceiling it is kept, with two exceptions that are both
    explicit decisions, never message phrasing: an escalation to `full`, and
    a client cap (`assistance_capped`), which may only lower it.
    """
    turn_ceiling = MAX_HINT_LEVEL_FOR_ASSISTANCE[plan.assistance_level]
    if plan.assistance_level == "full":
        return turn_ceiling
    base = base_ladder_ceiling(plan.assistance_level, progress)
    if "assistance_capped" in plan.rationale:
        return min(base, turn_ceiling)
    return base


def base_ladder_ceiling(assistance: AssistanceLevel, progress: HintProgress) -> HintLevel:
    """The ladder's stored ceiling, or this assistance level's for a new ladder.

    Shared by `ladder_ceiling` and the planner's escalation rule so both agree
    on when "the ceiling is reached". A capped turn can never escalate anyway
    (`clamp_assistance` runs after the plan and lowers `full` back down), so the
    cap itself is applied only in `ladder_ceiling`.
    """
    if progress.ceiling is not None:
        return progress.ceiling
    return MAX_HINT_LEVEL_FOR_ASSISTANCE[assistance]


#: Below this score a retrieved chunk is noise, not evidence -- same
#: calibration and rationale as `app.agents.planner.MIN_RETRIEVAL_TOPIC_SCORE`
#: (duplicated, not imported: that module imports `HintProgress` from this
#: one, so importing it back here would cycle). Re-measure alongside that
#: constant if the reranker model, the corpus, or retrieval query
#: construction changes.
MIN_GROUNDING_SCORE: Final = -5.0

#: Corpus sections this module will never quote from when grounding a rung:
#: `general_template` is literal solution code -- the one thing no rung
#: below L6 may ever reveal -- and `representative_problems` is just a list
#: of external problem links, not teaching prose.
_UNGROUNDABLE_SECTIONS: Final = frozenset({"general_template", "representative_problems"})

_CODE_FENCE_RE: Final = re.compile(r"```.*?```", re.DOTALL)
_SENTENCE_SPLIT_RE: Final = re.compile(r"(?<=[.!?])\s+")


def _trusted_hits(context: Sequence[RetrievalHit]) -> list[RetrievalHit]:
    """Retrieved hits this module is willing to ground rung text on.

    Filters by both relevance (`MIN_GROUNDING_SCORE`) and section
    (`_UNGROUNDABLE_SECTIONS`); failing either bar is the same fail-soft
    case -- no trusted evidence, so every rung reverts to its generic,
    topic-agnostic phrasing (see module docstring).
    """
    return [
        hit
        for hit in context
        if hit.score >= MIN_GROUNDING_SCORE
        and hit.chunk.metadata.get("section") not in _UNGROUNDABLE_SECTIONS
    ]


def _context_labels(trusted: Sequence[RetrievalHit]) -> list[str]:
    """Distinct, trusted-shape topic/pattern labels drawn from `trusted` hits.

    These come from the knowledge corpus (`app.schemas.knowledge.KnowledgeChunk`),
    not from the learner's own input, so they are safe to compose into hint text.
    Callers must pre-filter to `_trusted_hits` -- this function does not
    re-apply the relevance/section bars itself.
    """
    seen: list[str] = []
    for hit in trusted:
        for label in (hit.chunk.pattern, hit.chunk.topic):
            if label and label not in seen:
                seen.append(label)
    return seen


def _prose(chunk: KnowledgeChunk) -> str:
    """Flatten one chunk's corpus text into plain, single-line prose.

    Strips the `"{title} — {heading}\\n\\n"` prefix `chunk_document` always
    prepends (`app.knowledge.ingest`), drops any fenced code block (defence
    in depth: `_trusted_hits` already excludes the `general_template`
    section, but a future corpus edit could still embed a snippet inside
    another section), and collapses markdown line breaks/bullet markers so
    the result reads as a plain sentence stream -- `app.response.format`
    applies no markdown-escaping layer downstream, so this module must not
    hand it any markdown to begin with.
    """
    prefix = f"{chunk.title} — {chunk.heading}\n\n"
    text = chunk.text[len(prefix) :] if chunk.text.startswith(prefix) else chunk.text
    text = _CODE_FENCE_RE.sub(" ", text)
    text = text.replace("`", "").replace("*", "").replace("#", "")
    lines = (line.strip().lstrip("-").strip() for line in text.splitlines())
    return " ".join(line for line in lines if line)


def _first_clause(text: str, max_chars: int) -> str:
    """The first sentence of `text`, truncated at a word boundary if that
    sentence alone still exceeds `max_chars`.

    Bounding to one sentence (never a whole section) is deliberate: it caps
    how much corpus detail can land in any single rung, which is what keeps
    a grounded low rung from creeping into "hands over the algorithm"
    territory (see module docstring).
    """
    stripped = text.strip()
    if not stripped:
        return ""
    clause = _SENTENCE_SPLIT_RE.split(stripped, maxsplit=1)[0].strip()
    if len(clause) <= max_chars:
        return clause
    truncated = clause[:max_chars].rsplit(" ", 1)[0].rstrip(",;: ")
    return f"{truncated}..." if truncated else clause[:max_chars]


def _chunk_for_section(trusted: Sequence[RetrievalHit], section: str) -> KnowledgeChunk | None:
    """The best-ranked `trusted` hit whose corpus section is `section`, or `None`."""
    for hit in trusted:
        if hit.chunk.metadata.get("section") == section:
            return hit.chunk
    return None


def _grounded_clause(
    trusted: Sequence[RetrievalHit], section: str, *, max_chars: int = 200
) -> str | None:
    """One sentence of grounded, trusted prose from `section`, or `None` if
    no `trusted` hit carries that section this turn (fail-soft, per rung)."""
    chunk = _chunk_for_section(trusted, section)
    if chunk is None:
        return None
    clause = _first_clause(_prose(chunk), max_chars)
    return clause or None


def _identification_signal(trusted: Sequence[RetrievalHit]) -> str | None:
    """The first curated recognition cue for this topic, or `None`.

    `identification_signals` is document-level metadata (`CorpusDocument`,
    stamped onto every chunk of that document regardless of which section it
    came from -- `app.knowledge.ingest.chunk_document`): short, curated
    recognition phrases like "at most k distinct" or "monotonic deque window
    maximum". These name what the problem *looks like*, never what to *do*
    about it, so they are safe at the ladder's lowest rungs.
    """
    for hit in trusted:
        raw = hit.chunk.metadata.get("identification_signals", "")
        first = raw.split(",", 1)[0].strip()
        if first:
            return first
    return None


def _humanize(label: str) -> str:
    """Turn an internal slug (`sliding_window`) into prose (`sliding window`).

    Topic/pattern labels are storage slugs; showing them raw to a learner
    leaks internal formatting into the teaching voice.
    """
    return label.replace("_", " ").replace("-", " ").strip()


GENERIC_SHAPE_HINT: Final = "a structure that supports fast lookups or ordered access"


def _shape_hint(topic: str | None, context_labels: Sequence[str]) -> str:
    """Pick a data-structure hint that says something the topic has not.

    The first retrieved label is often *the topic itself*, which produced the
    self-referential "For a sliding_window problem, sliding_window is often
    the right shape". Only a label that differs from the topic adds
    information; otherwise fall back to the generic phrasing.

    When the planner could not infer a topic, retrieval had nothing to anchor
    on either, so its labels are not evidence about *this* problem -- they are
    whatever the corpus happened to return. Naming one then states a falsehood
    with total confidence ("for a two-sum question, trees is often the right
    shape"), which is worse for a learner than saying something general. So an
    unknown topic always gets the generic phrasing.
    """
    if not topic:
        return GENERIC_SHAPE_HINT
    topic_key = topic.replace("_", " ").replace("-", " ").strip().casefold()
    for label in context_labels:
        if _humanize(label).casefold() != topic_key:
            return _humanize(label)
    return GENERIC_SHAPE_HINT


def _rung_text(
    level: HintLevel,
    plan: TeachingPlan,
    context_labels: Sequence[str],
    trusted: Sequence[RetrievalHit],
    ladder_topic: str | None = None,
) -> str:
    """Compose the guidance-level text for one ladder rung.

    Built only from `plan`'s structured fields, `context_labels`, and a
    bounded, single-sentence grounded clause pulled from `trusted` (already
    relevance/section-filtered by `_trusted_hits`) -- never from the
    learner's raw question/problem/code/error text. When `trusted` is empty,
    every `_grounded_clause`/`_identification_signal` call below returns
    `None` and each rung is byte-identical to its pre-Packet-P5 generic text.

    `ladder_topic` (Packet P5b) is `plan.topic`'s fallback: `plan.topic` is
    this TURN's topic, which a bare follow-up ("next hint") often resolves to
    `None` even though the hint ladder itself stayed anchored to the
    problem's topic (see `app.graph.nodes._hint_topic_key`). Trusted the same
    way `plan.topic` is -- both are corpus-derived slugs, never learner
    prose -- and only consulted when `plan.topic` itself is `None`.
    """
    # Two phrasings, because the topic is a *modifier* ("sliding window
    # problem"), not a stand-in for the whole noun phrase. Substituting a
    # fallback noun here produced "this this problem problem".
    raw_topic = plan.topic or ladder_topic
    topic = _humanize(raw_topic) if raw_topic else None
    this_problem = f"this {topic} problem" if topic else "this problem"
    a_problem = f"a {topic} problem" if topic else "this problem"
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
            f"Think about what state you need to track while working through "
            f"{this_problem}: which values change at each step, and which ones you "
            "need to remember from earlier steps to make a later decision."
        )
        signal = _identification_signal(trusted)
        if signal:
            text += f" A cue worth noticing here: {signal}."
        if watch:
            text += f" Also watch for mistakes that have tripped you up before: {watch}."
        return text

    if level == HintLevel.L2_DATA_STRUCTURE:
        shape = _shape_hint(raw_topic, context_labels)
        text = (
            f"Consider what data structure would let you track that state "
            f"efficiently. For {a_problem}, {shape} is often the right shape "
            "-- ask yourself what operations you need it to support quickly."
        )
        overview = _grounded_clause(trusted, "overview")
        if overview:
            text += f" {overview}"
        return text

    if level == HintLevel.L3_CONCRETE_IDEA:
        text = (
            "Sketch a concrete approach: decide on the single pass or traversal "
            "you'd make over the input, and exactly what you'd read from and "
            "write to the data structure at each step."
        )
        intuition = _grounded_clause(trusted, "core_intuition")
        if intuition:
            text += f" Here's the core idea that makes this efficient: {intuition}"
        return text

    if level == HintLevel.L4_PSEUDOCODE:
        text = (
            "Write pseudocode for that approach, step by step: how you initialize "
            "your state, the loop or recursion structure, the update rule applied "
            "at each step, and what gets returned at the end. Keep it to steps, "
            "not real syntax yet."
        )
        complexity = _grounded_clause(trusted, "complexity")
        if complexity:
            text += f" Aim for this complexity: {complexity}"
        return text

    if level == HintLevel.L5_PARTIAL:
        text = (
            "Here is a partial structure to build from: set up the initial state, "
            "write the loop skeleton over the input, and leave the core update "
            "step for you to fill in yourself using the pseudocode from the "
            "previous hint."
        )
        mistake = _grounded_clause(trusted, "common_mistakes")
        if mistake:
            text += f" A mistake worth guarding against while you fill it in: {mistake}"
        return text

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
    ladder_topic: str | None = None,
) -> HintResult | None:
    """Compute the next hint-ladder rung for this turn, or `None` if done.

    `problem` is accepted for signature symmetry but deliberately unread --
    `del problem` runs immediately below, before any other statement in this
    function's body, and no other parameter (`plan`, `progress`, `context`,
    `ladder_topic`) ever carries the learner's own question/problem/code/error
    text: `plan` is `TeachingPlan`'s closed-vocabulary structured fields,
    `progress` is this call's ladder bookkeeping, `context` is retrieved
    `KnowledgeChunk`s from the curated corpus, and `ladder_topic` (Packet P5b)
    is a corpus-derived pattern slug (or `None`) -- the same trusted vocabulary
    as `plan.topic`, never learner prose. So regardless of how much of
    `context` Packet P5's grounding (`_trusted_hits` / `_grounded_clause` /
    `_identification_signal`, all called only below this docstring) folds
    into `HintResult.text`, the learner's raw input has structurally no path
    into that text -- this function never reads it, and nothing it does
    read can carry it. `context` is optional and may be empty; only
    trusted-shape chunk labels and a bounded grounded clause (per
    `MIN_GROUNDING_SCORE` and `_UNGROUNDABLE_SECTIONS`) are ever drawn from
    it, falling back to the pre-Packet-P5 generic template when nothing
    clears that bar this turn. `ladder_topic` is used only as `_rung_text`'s
    fallback when `plan.topic` is `None` (see its docstring) -- a bare
    follow-up turn ("next hint") that would otherwise lose the topic modifier
    ("this sliding window problem" -> "this problem") and the L2 shape hint's
    ability to name a specific structure instead of the generic fallback.

    Rules (see module docstring for the security rationale):
    - `progress.solved` -> `None` (stop hinting).
    - `plan.assistance_level == "full"` -> the ceiling directly (`L6_FULL`),
      on this same call. `"full"` is unreachable any other way --
      `app.agents.planner.build_plan` only ever sets it via Packet P3's
      escalation rule, gated on all three of its own conditions already
      having held for THIS turn -- so by the time this function ever sees
      it, the decision to reveal the solution now has already been made
      upstream, deliberately and narrowly. The one-rung-per-call limit below
      exists to pace *ordinary* hint requests; applying it here as well
      would turn a granted escalation into "one more rung today, ask again
      tomorrow" -- silently reneging on it for one to several more turns
      (measured live: an escalated turn surfaced an `L4` pseudocode rung and
      `reveals_code=False`, not the promised solution).
    - Otherwise -- first ask (`last_level is None`) -> L0, unless the
      ceiling is lower (defensive; never happens with the current ceiling
      mapping); after that -> `min(last_level + 1, ceiling)`: at most one
      rung per call, never above the ceiling, idempotent once the ceiling
      is reached.
    """
    del problem  # untrusted; deliberately not read -- see module docstring

    if progress.solved:
        return None

    ceiling = ladder_ceiling(plan, progress)

    if plan.assistance_level == "full":
        level = ceiling
    elif progress.last_level is None:
        level = HintLevel(min(int(HintLevel.L0_NUDGE), int(ceiling)))
    else:
        level = HintLevel(min(int(progress.last_level) + 1, int(ceiling)))

    trusted = _trusted_hits(context)
    context_labels = _context_labels(trusted)
    text = _rung_text(level, plan, context_labels, trusted, ladder_topic)

    return HintResult(
        level=level,
        text=text,
        is_terminal=level == ceiling,
        reveals_code=level >= HintLevel.L5_PARTIAL,
        ceiling=ceiling,
    )
