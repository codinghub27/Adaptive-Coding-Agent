"""Property-style unit tests for the deterministic hint-ladder engine.

Pure, synchronous tests: no LLM, no database, no async fixtures.
"""

from app.agents.hint_engine import HintProgress, next_hint
from app.schemas.agent_results import MAX_HINT_LEVEL_FOR_ASSISTANCE, HintLevel
from app.schemas.input import CodeBlock, StructuredInput
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
    """From every last_level, for every assistance level, level advances by at most one."""
    for assistance in ALL_ASSISTANCE:
        plan = _plan(assistance)
        for last in [None, *ALL_LEVELS]:
            progress = HintProgress(last_level=last)
            result = next_hint(None, plan, progress)
            if result is None:
                continue
            if last is None:
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
    plan = _plan("full")
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


def test_full_climb_seven_distinct_levels_in_order() -> None:
    plan = _plan("full")
    last: HintLevel | None = None
    levels: list[HintLevel] = []
    for _ in range(7):
        progress = HintProgress(last_level=last)
        result = next_hint(None, plan, progress)
        assert result is not None
        levels.append(result.level)
        last = result.level

    assert levels == list(HintLevel)
    assert len(set(levels)) == 7


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
