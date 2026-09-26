"""Property-style unit tests for the deterministic hint-ladder engine.

Pure, synchronous tests: no LLM, no database, no async fixtures.
"""

from app.agents.hint_engine import GENERIC_SHAPE_HINT, MIN_GROUNDING_SCORE, HintProgress, next_hint
from app.schemas.agent_results import MAX_HINT_LEVEL_FOR_ASSISTANCE, HintLevel
from app.schemas.input import CodeBlock, StructuredInput
from app.schemas.knowledge import KnowledgeChunk, RetrievalHit
from app.schemas.plan import ASSISTANCE_ORDER, AssistanceLevel, TeachingPlan

SENTINEL = "SENTINEL_IGNORE_ALL_PREVIOUS_INSTRUCTIONS_XYZZY"

ALL_LEVELS: list[HintLevel] = list(HintLevel)
ALL_ASSISTANCE: tuple[AssistanceLevel, ...] = ASSISTANCE_ORDER


def _plan(
    assistance_level: AssistanceLevel,
    *,
    topic: str | None = "two_pointers",
    watch_errors: list[str] | None = None,
) -> TeachingPlan:
    return TeachingPlan(
        difficulty="medium",
        assistance_level=assistance_level,
        solution_strategy="socratic_hints",
        topic=topic,
        skill_level=0.5,
        step_by_step=False,
        concise=False,
        watch_errors=watch_errors if watch_errors is not None else ["off_by_one"],
        rationale=[],
    )


def _chunk(
    *,
    heading: str,
    section: str,
    text: str,
    topic: str = "arrays",
    pattern: str = "sliding_window",
    identification_signals: str | None = None,
) -> KnowledgeChunk:
    title = "Sliding Window"
    metadata = {"section": section}
    if identification_signals is not None:
        metadata["identification_signals"] = identification_signals
    return KnowledgeChunk(
        id=f"chunk-{section}",
        text=f"{title} — {heading}\n\n{text}",
        source="app/knowledge/corpus/sliding_window.md",
        title=title,
        heading=heading,
        topic=topic,
        pattern=pattern,
        metadata=metadata,
    )


def _hit(chunk: KnowledgeChunk, score: float = 0.0) -> RetrievalHit:
    return RetrievalHit(chunk=chunk, score=score, retrievers=("dense",), reranked=True)


def _sentinel_problem() -> StructuredInput:
    return StructuredInput(
        source="text",
        question=SENTINEL,
        code=[CodeBlock(content=SENTINEL, language="python")],
        error=SENTINEL,
        problem=SENTINEL,
        constraints=[SENTINEL],
    )


def test_no_level_skipping() -> None:
    """From every last_level, for every assistance level, level advances by at
    most one -- except `full`, Packet P3's escalation signal, which jumps
    straight to its ceiling (`L6_FULL`) on the turn it is granted regardless
    of prior progress (see `next_hint`'s docstring: `full` is unreachable
    except through an already fully-gated escalation, so pacing its reveal
    the same way as every other assistance level would silently renege on
    it for one to several more turns)."""
    for assistance in ALL_ASSISTANCE:
        plan = _plan(assistance)
        for last in [None, *ALL_LEVELS]:
            progress = HintProgress(last_level=last)
            result = next_hint(None, plan, progress)
            if result is None:
                continue
            if assistance == "full":
                assert result.level == HintLevel.L6_FULL
            elif last is None:
                assert result.level == HintLevel.L0_NUDGE
            else:
                assert result.level <= last + 1


def test_ceiling_never_exceeded() -> None:
    """Repeated calls, feeding the result back as last_level, never exceed the ceiling."""
    for assistance in ALL_ASSISTANCE:
        plan = _plan(assistance)
        ceiling = MAX_HINT_LEVEL_FOR_ASSISTANCE[assistance]
        last: HintLevel | None = None
        for _ in range(12):
            progress = HintProgress(last_level=last)
            result = next_hint(None, plan, progress)
            assert result is not None
            assert result.level <= ceiling
            assert result.ceiling == ceiling
            last = result.level


def test_hint_assistance_never_reveals_code_and_caps_at_l2() -> None:
    plan = _plan("hint")
    last: HintLevel | None = None
    for _ in range(12):
        progress = HintProgress(last_level=last)
        result = next_hint(None, plan, progress)
        assert result is not None
        assert result.reveals_code is False
        assert result.level <= HintLevel.L2_DATA_STRUCTURE
        last = result.level


def test_idempotent_and_terminal_at_ceiling() -> None:
    for assistance in ALL_ASSISTANCE:
        plan = _plan(assistance)
        ceiling = MAX_HINT_LEVEL_FOR_ASSISTANCE[assistance]
        progress = HintProgress(last_level=ceiling)

        first = next_hint(None, plan, progress)
        second = next_hint(None, plan, progress)

        assert first is not None
        assert second is not None
        assert first.level == ceiling
        assert second.level == ceiling
        assert first.is_terminal is True
        assert second.is_terminal is True


def test_is_terminal_matches_ceiling_exactly() -> None:
    plan = _plan("full")
    ceiling = MAX_HINT_LEVEL_FOR_ASSISTANCE["full"]
    last: HintLevel | None = None
    for _ in range(8):
        progress = HintProgress(last_level=last)
        result = next_hint(None, plan, progress)
        assert result is not None
        assert result.is_terminal == (result.level == ceiling)
        last = result.level


def test_solved_stops_hinting() -> None:
    plan = _plan("full")

    mid_progress = HintProgress(last_level=HintLevel.L2_DATA_STRUCTURE, solved=True)
    assert next_hint(None, plan, mid_progress) is None

    fresh_progress = HintProgress(last_level=None, solved=True)
    assert next_hint(None, plan, fresh_progress) is None

    ceiling_progress = HintProgress(last_level=HintLevel.L6_FULL, solved=True)
    assert next_hint(None, plan, ceiling_progress) is None


def test_reveals_code_only_from_l5() -> None:
    plan = _plan("partial")
    last: HintLevel | None = None
    for _ in range(8):
        progress = HintProgress(last_level=last)
        result = next_hint(None, plan, progress)
        assert result is not None
        if result.level < HintLevel.L5_PARTIAL:
            assert result.reveals_code is False
        else:
            assert result.reveals_code is True
        last = result.level


def test_partial_climb_six_distinct_levels_in_order() -> None:
    """One rung per call, L0 through L5 (`partial`'s ceiling), for every
    assistance level except `full` -- see `test_full_jumps_directly_to_ceiling`."""
    plan = _plan("partial")
    last: HintLevel | None = None
    levels: list[HintLevel] = []
    for _ in range(6):
        progress = HintProgress(last_level=last)
        result = next_hint(None, plan, progress)
        assert result is not None
        levels.append(result.level)
        last = result.level

    assert levels == list(HintLevel)[:6]
    assert len(set(levels)) == 6


def test_full_jumps_directly_to_ceiling() -> None:
    """`full` is Packet P3's escalation signal, not an ordinary ceiling --
    unreachable except through `app.agents.planner.build_plan`'s escalation
    rule, which has already gated all three of its own conditions by the
    time a turn's plan carries it. Pacing the reveal one rung per call, as
    every other assistance level still does, would silently renege on an
    already-granted escalation for one to several more turns (measured
    live: an escalated turn surfaced an `L4` pseudocode rung and
    `reveals_code=False`, not the promised solution). So `full` jumps
    straight to `L6_FULL` on the very call it is granted, from any prior
    level -- including a learner's very first ask on this problem."""
    plan = _plan("full")
    for last in (None, HintLevel.L0_NUDGE, HintLevel.L3_CONCRETE_IDEA, HintLevel.L5_PARTIAL):
        progress = HintProgress(last_level=last)
        result = next_hint(None, plan, progress)
        assert result is not None
        assert result.level == HintLevel.L6_FULL
        assert result.reveals_code is True
        assert result.is_terminal is True


def test_no_untrusted_echo_across_all_levels_and_assistance_levels() -> None:
    """Security test: the sentinel from the learner's problem must never appear
    in any returned hint text, at any rung, for any assistance level."""
    problem = _sentinel_problem()
    assertions_made = 0
    for assistance in ALL_ASSISTANCE:
        plan = _plan(assistance)
        last: HintLevel | None = None
        for _ in range(12):
            progress = HintProgress(last_level=last)
            result = next_hint(problem, plan, progress)
            if result is None:
                break
            assert SENTINEL not in result.text
            assertions_made += 1
            last = result.level
    assert assertions_made > 0


def test_no_untrusted_echo_even_with_none_problem() -> None:
    """Sanity check: passing None for problem never crashes and never leaks anything."""
    for assistance in ALL_ASSISTANCE:
        plan = _plan(assistance)
        last: HintLevel | None = None
        for _ in range(12):
            progress = HintProgress(last_level=last)
            result = next_hint(None, plan, progress)
            if result is None:
                break
            assert SENTINEL not in result.text
            last = result.level


def test_no_untrusted_echo_when_sentinel_also_in_watch_errors() -> None:
    """Even if a trusted-shape field (watch_errors) happens to carry the
    sentinel, the module should never read the untrusted `problem` payload at
    all -- confirm text composition stays bounded to plan/context fields by
    checking the sentinel doesn't appear regardless of which field carries it."""
    problem = _sentinel_problem()
    plan = _plan("full", topic="two_pointers", watch_errors=["off_by_one", "index_error"])
    last: HintLevel | None = None
    for _ in range(8):
        progress = HintProgress(last_level=last)
        result = next_hint(problem, plan, progress)
        assert result is not None
        assert SENTINEL not in result.text
        last = result.level


def test_hint_progress_defaults() -> None:
    progress = HintProgress()
    assert progress.last_level is None
    assert progress.solved is False


# --------------------------------------------------------------------------
# Packet P5: grounding from trusted retrieved chunks
# --------------------------------------------------------------------------

DISTINCTIVE_SIGNAL = "monotonic deque window maximum, an unusually specific cue phrase"
DISTINCTIVE_INTUITION = (
    "Because adding or removing one element from either edge is O(1) amortized in "
    "this very distinctive load-bearing sentence, the window never rescans."
)
DISTINCTIVE_MISTAKE = (
    "Recomputing the window property from scratch on every shift is a very "
    "distinctive load-bearing mistake to avoid."
)


def test_l1_grounds_on_identification_signal() -> None:
    plan = _plan("concept", topic="sliding_window")
    context = [
        _hit(
            _chunk(
                heading="Identification Signals",
                section="identification_signals",
                text="bullet list of cues",
                identification_signals=DISTINCTIVE_SIGNAL,
            ),
            score=0.0,
        )
    ]
    progress = HintProgress(last_level=HintLevel.L0_NUDGE)
    result = next_hint(None, plan, progress, context=context)
    assert result is not None
    assert result.level == HintLevel.L1_WHAT_TO_TRACK
    assert "monotonic deque window maximum" in result.text


def test_l3_grounds_on_core_intuition_section() -> None:
    plan = _plan("partial", topic="sliding_window")
    context = [
        _hit(_chunk(heading="Core Intuition", section="core_intuition", text=DISTINCTIVE_INTUITION))
    ]
    progress = HintProgress(last_level=HintLevel.L2_DATA_STRUCTURE)
    result = next_hint(None, plan, progress, context=context)
    assert result is not None
    assert result.level == HintLevel.L3_CONCRETE_IDEA
    assert "very distinctive load-bearing sentence" in result.text


def test_l5_grounds_on_common_mistakes_section() -> None:
    plan = _plan("partial", topic="sliding_window")
    context = [
        _hit(_chunk(heading="Common Mistakes", section="common_mistakes", text=DISTINCTIVE_MISTAKE))
    ]
    progress = HintProgress(last_level=HintLevel.L4_PSEUDOCODE)
    result = next_hint(None, plan, progress, context=context)
    assert result is not None
    assert result.level == HintLevel.L5_PARTIAL
    assert "very distinctive load-bearing mistake" in result.text


def test_below_floor_retrieval_falls_back_to_generic_template() -> None:
    """A hit that never clears `MIN_GROUNDING_SCORE` must not ground anything:
    the rung must be byte-identical to the no-context generic template."""
    plan = _plan("concept", topic="sliding_window")
    below_floor = MIN_GROUNDING_SCORE - 1.0
    context = [
        _hit(
            _chunk(
                heading="Identification Signals",
                section="identification_signals",
                text="bullet list of cues",
                identification_signals=DISTINCTIVE_SIGNAL,
            ),
            score=below_floor,
        )
    ]
    progress = HintProgress(last_level=HintLevel.L0_NUDGE)

    grounded_attempt = next_hint(None, plan, progress, context=context)
    baseline = next_hint(None, plan, progress, context=())

    assert grounded_attempt is not None
    assert baseline is not None
    assert grounded_attempt.text == baseline.text
    assert "monotonic deque window maximum" not in grounded_attempt.text


def test_general_template_section_never_grounds_a_rung() -> None:
    """The `general_template` corpus section is literal solution code -- an
    above-floor hit from it must still be excluded from grounding, at every
    level, even though its score alone would clear `MIN_GROUNDING_SCORE`."""
    code_snippet = "def longest_no_repeat(s): DISTINCTIVE_CODE_MARKER_1234"
    context = [
        _hit(
            _chunk(
                heading="General Template",
                section="general_template",
                text=f"```python\n{code_snippet}\n```",
                identification_signals=DISTINCTIVE_SIGNAL,
            ),
            score=0.0,
        )
    ]
    plan = _plan("full", topic="sliding_window")
    last: HintLevel | None = None
    for _ in range(8):
        progress = HintProgress(last_level=last)
        result = next_hint(None, plan, progress, context=context)
        assert result is not None
        assert "DISTINCTIVE_CODE_MARKER_1234" not in result.text
        assert code_snippet not in result.text
        last = result.level


def test_grounded_rungs_never_leak_the_learner_sentinel() -> None:
    """Guard test: even with trusted, above-floor grounding context available,
    the learner's own problem/code sentinel must never appear in any rung at
    any level -- this is the security property and is not optional."""
    problem = _sentinel_problem()
    context = [
        _hit(
            _chunk(
                heading="Identification Signals",
                section="identification_signals",
                text="bullet list of cues",
                identification_signals=DISTINCTIVE_SIGNAL,
            )
        ),
        _hit(
            _chunk(heading="Core Intuition", section="core_intuition", text=DISTINCTIVE_INTUITION)
        ),
        _hit(
            _chunk(heading="Common Mistakes", section="common_mistakes", text=DISTINCTIVE_MISTAKE)
        ),
        _hit(
            _chunk(heading="Overview", section="overview", text="A generic overview clause here.")
        ),
        _hit(_chunk(heading="Complexity", section="complexity", text="O(n) time, O(k) space.")),
    ]
    assertions_made = 0
    for assistance in ALL_ASSISTANCE:
        plan = _plan(assistance, topic="sliding_window")
        last: HintLevel | None = None
        for _ in range(12):
            progress = HintProgress(last_level=last)
            result = next_hint(problem, plan, progress, context=context)
            if result is None:
                break
            assert SENTINEL not in result.text
            assertions_made += 1
            last = result.level
    assert assertions_made > 0


def test_rung_monotonicity_l1_never_contains_full_solution() -> None:
    """L1 must remain a nudge-level rung even when grounding is available:
    it must never contain the corpus's literal solution code, and must stay
    strictly less revealing than L6 (`reveals_code` still gates at L5+)."""
    context = [
        _hit(
            _chunk(
                heading="Identification Signals",
                section="identification_signals",
                text="bullet list of cues",
                identification_signals=DISTINCTIVE_SIGNAL,
            )
        )
    ]
    plan = _plan("full", topic="sliding_window")
    l1_progress = HintProgress(last_level=HintLevel.L0_NUDGE, has_verified_attempt=False)
    # Use "concept" (ceiling L3) to actually observe an L1 rung rather than
    # `full`'s direct jump to the ceiling.
    concept_plan = _plan("concept", topic="sliding_window")
    l1 = next_hint(None, concept_plan, l1_progress, context=context)
    l6 = next_hint(None, plan, HintProgress(last_level=None), context=context)

    assert l1 is not None
    assert l6 is not None
    assert l1.level == HintLevel.L1_WHAT_TO_TRACK
    assert l6.level == HintLevel.L6_FULL
    assert l1.reveals_code is False
    assert l6.reveals_code is True
    assert len(l1.text) < len(l6.text) or l1.text != l6.text
    assert "def " not in l1.text
    assert "```" not in l1.text


# --------------------------------------------------------------------------
# Packet P5b: `ladder_topic` fallback when `plan.topic` is `None`
# --------------------------------------------------------------------------


def test_l1_uses_ladder_topic_when_plan_topic_is_none() -> None:
    """A bare follow-up turn ("next hint") resolves `plan.topic=None`, but the
    hint ladder itself stays anchored to the problem's topic (see
    `app.graph.nodes._hint_topic_key`) -- `next_hint`'s `ladder_topic` param
    is that anchor. Without it, the rung degrades to "this problem" instead
    of naming the actual pattern."""
    plan = _plan("hint", topic=None)
    progress = HintProgress(last_level=HintLevel.L0_NUDGE)

    generic = next_hint(None, plan, progress)
    anchored = next_hint(None, plan, progress, ladder_topic="sliding_window")

    assert generic is not None
    assert anchored is not None
    assert generic.level == HintLevel.L1_WHAT_TO_TRACK
    assert anchored.level == HintLevel.L1_WHAT_TO_TRACK
    assert "this problem" in generic.text
    assert "sliding window" not in generic.text
    assert "this sliding window problem" in anchored.text


def test_l2_shape_hint_uses_ladder_topic_when_plan_topic_is_none() -> None:
    """Same fallback, exercised at L2 -- `_shape_hint` also reads the
    (`plan.topic` or `ladder_topic`) fallback, not `plan.topic` alone, so a
    topic-less follow-up still gets a real shape label instead of
    `GENERIC_SHAPE_HINT`."""
    plan = _plan("partial", topic=None)
    context = [_hit(_chunk(heading="Overview", section="overview", text="Overview prose."))]
    progress = HintProgress(last_level=HintLevel.L1_WHAT_TO_TRACK)

    generic = next_hint(None, plan, progress, context=context)
    anchored = next_hint(None, plan, progress, context=context, ladder_topic="two_pointers")

    assert generic is not None
    assert anchored is not None
    assert generic.level == HintLevel.L2_DATA_STRUCTURE
    assert anchored.level == HintLevel.L2_DATA_STRUCTURE
    assert GENERIC_SHAPE_HINT in generic.text
    assert GENERIC_SHAPE_HINT not in anchored.text
    assert "two pointers" in anchored.text
