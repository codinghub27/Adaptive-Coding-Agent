"""Invariant tests for `app.response.generate.generate_response`.

These are the tests that matter most: a hint-level turn must never leak
code, no matter which result type or plan combination is fed in.
"""

import pytest
from pydantic import ValidationError

from app.response.format import SAFE_FALLBACK_RESPONSE
from app.response.generate import generate_response
from app.schemas.agent_results import (
    BugLocation,
    CodeStructureNode,
    DebugResult,
    DSAResult,
    ExplainResult,
    HintLevel,
    HintResult,
    LineExplanation,
    ReviewFinding,
    ReviewResult,
)
from app.schemas.execution import Verdict
from app.schemas.plan import ASSISTANCE_ORDER, AssistanceLevel, TeachingPlan
from app.schemas.response import GeneratedResponse, ResponseSection

_SOLUTION_CODE = "def solve(nums):\n    return sum(nums)  # UNIQUE_SENTINEL_CODE\n"
_PATCHED_CODE = "def f():\n    return 1  # UNIQUE_PATCH_SENTINEL\n"


def _plan(assistance_level: AssistanceLevel) -> TeachingPlan:
    return TeachingPlan(
        difficulty="easy",
        assistance_level=assistance_level,
        solution_strategy="socratic_hints",
        topic="arrays",
        skill_level=0.5,
    )


def _dsa_result_with_full_hint_and_code() -> DSAResult:
    hint = HintResult(
        level=HintLevel.L6_FULL,
        text="Here's the full solution approach.",
        is_terminal=True,
        reveals_code=True,
        ceiling=HintLevel.L6_FULL,
    )
    return DSAResult(
        topic="arrays",
        understanding="Sum the array.",
        key_insight="Just add everything up.",
        pseudocode="for each num, add to total",
        code=_SOLUTION_CODE,
        hint=hint,
        complexity_time="O(n)",
        complexity_space="O(1)",
    )


# ---------------------------------------------------------------------------
# Headline invariant: DSA code section only at "full"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("level", ASSISTANCE_ORDER)
def test_dsa_code_only_revealed_at_full_assistance(level: AssistanceLevel) -> None:
    result = _dsa_result_with_full_hint_and_code()
    response = generate_response(result=result, plan=_plan(level))

    has_code_section = any(section.kind == "code" for section in response.sections)
    if level == "full":
        assert has_code_section
        assert response.reveals_code is True
        assert _SOLUTION_CODE.strip() in response.text
    else:
        assert not has_code_section
        assert response.reveals_code is False
        assert _SOLUTION_CODE.strip() not in response.text
        assert "UNIQUE_SENTINEL_CODE" not in response.text


# ---------------------------------------------------------------------------
# Same invariant for DebugResult.patched_code
# ---------------------------------------------------------------------------


def _debug_result(*, fixed: bool) -> DebugResult:
    final_verdict = (
        Verdict(status="pass", summary="all tests pass", cases_passed=2, cases_total=2)
        if fixed
        else Verdict(status="fail", category="wrong_answer", summary="still failing")
    )
    return DebugResult(
        bug_explanation="Off-by-one error in the loop bound.",
        bug_location=BugLocation(lineno=2),
        patched_code=_PATCHED_CODE,
        final_verdict=final_verdict,
        fixed=fixed,
    )


@pytest.mark.parametrize("level", ASSISTANCE_ORDER)
def test_debug_patch_only_revealed_at_full_when_fixed(level: AssistanceLevel) -> None:
    result = _debug_result(fixed=True)
    response = generate_response(result=result, plan=_plan(level))

    has_patch_section = any(section.kind == "patch" for section in response.sections)
    if level == "full":
        assert has_patch_section
        assert response.reveals_code is True
        assert _PATCHED_CODE.strip() in response.text
    else:
        assert not has_patch_section
        assert response.reveals_code is False
        assert "UNIQUE_PATCH_SENTINEL" not in response.text


@pytest.mark.parametrize("level", ASSISTANCE_ORDER)
def test_debug_patch_never_revealed_when_not_fixed(level: AssistanceLevel) -> None:
    result = _debug_result(fixed=False)
    response = generate_response(result=result, plan=_plan(level))

    assert not any(section.kind == "patch" for section in response.sections)
    assert response.reveals_code is False
    assert "UNIQUE_PATCH_SENTINEL" not in response.text


def test_plan_none_behaves_as_hint_and_never_reveals_code() -> None:
    dsa_response = generate_response(result=_dsa_result_with_full_hint_and_code(), plan=None)
    assert dsa_response.assistance_level == "hint"
    assert dsa_response.reveals_code is False
    assert "UNIQUE_SENTINEL_CODE" not in dsa_response.text

    debug_response = generate_response(result=_debug_result(fixed=True), plan=None)
    assert debug_response.assistance_level == "hint"
    assert debug_response.reveals_code is False
    assert "UNIQUE_PATCH_SENTINEL" not in debug_response.text


# ---------------------------------------------------------------------------
# Phase 07 regressions this packet must fix
# ---------------------------------------------------------------------------


def test_explain_result_with_none_rationale_still_renders_line_explanations() -> None:
    result = ExplainResult(
        structure=CodeStructureNode(kind="function", name="f", lineno=1, end_lineno=3),
        line_explanations=[
            LineExplanation(lineno=1, code="def f():", explanation="defines f"),
            LineExplanation(lineno=2, code="    return 1", explanation="returns one"),
        ],
        complexity_rationale=None,
    )

    response = generate_response(result=result, plan=_plan("hint"))

    assert response.text != SAFE_FALLBACK_RESPONSE
    assert "defines f" in response.text
    assert "returns one" in response.text


def test_review_result_with_skipped_verdict_renders_all_findings_and_no_correctness_claim() -> (
    None
):
    findings = [
        ReviewFinding(category="correctness", severity="major", message="off by one bug"),
        ReviewFinding(category="readability", severity="minor", message="rename x"),
        ReviewFinding(category="edge_cases", severity="info", message="handle empty input"),
    ]
    result = ReviewResult(
        correctness_verdict=Verdict(status="skipped", summary="no tests configured"),
        findings=findings,
    )

    response = generate_response(result=result, plan=_plan("hint"))

    for finding in findings:
        assert finding.message in response.text
    assert "confirmed correct" not in response.text.lower()
    assert result.claims_correct is False


# ---------------------------------------------------------------------------
# Fallback ladder
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("level", ASSISTANCE_ORDER)
def test_empty_dsa_result_falls_back_to_fallback_text(level: AssistanceLevel) -> None:
    response = generate_response(
        result=DSAResult(), plan=_plan(level), fallback_text="here is a fallback"
    )
    assert response.text == "here is a fallback"
    assert response.sections == []
    assert response.reveals_code is False


def test_empty_debug_result_falls_back_to_fallback_text() -> None:
    response = generate_response(
        result=DebugResult(), plan=_plan("hint"), fallback_text="fallback text"
    )
    assert response.text == "fallback text"
    assert response.sections == []


def test_empty_explain_result_falls_back_to_safe_fallback_when_no_fallback_text() -> None:
    response = generate_response(result=ExplainResult(), plan=_plan("hint"), fallback_text=None)
    assert response.text == SAFE_FALLBACK_RESPONSE
    assert response.sections == []


def test_empty_review_result_falls_back_to_safe_fallback_when_fallback_text_blank() -> None:
    response = generate_response(result=ReviewResult(), plan=_plan("hint"), fallback_text="   ")
    assert response.text == SAFE_FALLBACK_RESPONSE


def test_no_result_falls_back_to_fallback_text_then_safe_fallback() -> None:
    with_fallback = generate_response(result=None, plan=_plan("hint"), fallback_text="hi there")
    assert with_fallback.text == "hi there"

    without_fallback = generate_response(result=None, plan=_plan("hint"), fallback_text=None)
    assert without_fallback.text == SAFE_FALLBACK_RESPONSE


@pytest.mark.parametrize(
    "result",
    [
        DSAResult(),
        DebugResult(),
        ExplainResult(),
        ReviewResult(),
        None,
    ],
)
def test_text_is_never_empty_for_any_result_type(result: object) -> None:
    response = generate_response(result=result, plan=None, fallback_text=None)  # type: ignore[arg-type]
    assert response.text.strip() != ""


# ---------------------------------------------------------------------------
# GeneratedResponse construction invariants
# ---------------------------------------------------------------------------


def test_generated_response_rejects_reveals_code_true_at_non_full_level() -> None:
    with pytest.raises(ValidationError):
        GeneratedResponse(
            text="body",
            sections=[],
            assistance_level="hint",
            reveals_code=True,
        )


def test_generated_response_rejects_code_section_with_reveals_code_false() -> None:
    with pytest.raises(ValidationError):
        GeneratedResponse(
            text="body",
            sections=[ResponseSection(kind="code", title="Solution code", body="x = 1")],
            assistance_level="full",
            reveals_code=False,
        )


@pytest.mark.parametrize("level", ["hint", "concept", "pseudocode"])
def test_code_revealing_hint_is_withheld_below_partial(level: AssistanceLevel) -> None:
    """A hint whose own body is the solution must not render at a level that
    forbids code.

    `HintResult.reveals_code` is true only at L5/L6, where the hint text *is*
    the partial/full solution. That hint would otherwise render as `next_hint`
    at every assistance level, leaking code straight past the section-level
    gate -- so a result built under a permissive plan, rendered against a
    restrictive one, must drop it rather than print it.
    """
    solution = "def solve(nums):\n    return sorted(nums)  # WHOLE_SOLUTION"
    result = DSAResult(
        hint=HintResult(
            level=HintLevel.L6_FULL,
            text=solution,
            is_terminal=True,
            reveals_code=True,
            ceiling=HintLevel.L6_FULL,
        )
    )

    generated = generate_response(result=result, plan=_plan(level), verification=None)

    assert "WHOLE_SOLUTION" not in generated.text
    assert generated.reveals_code is False
    assert all(section.kind != "next_hint" for section in generated.sections)


def test_code_revealing_hint_is_rendered_at_full() -> None:
    """...and the same hint is shown when the plan does permit code, so the
    withholding above is a gate, not a blanket refusal."""
    solution = "def solve(nums):\n    return sorted(nums)  # WHOLE_SOLUTION"
    result = DSAResult(
        hint=HintResult(
            level=HintLevel.L6_FULL,
            text=solution,
            is_terminal=True,
            reveals_code=True,
            ceiling=HintLevel.L6_FULL,
        )
    )

    generated = generate_response(result=result, plan=_plan("full"), verification=None)

    assert "WHOLE_SOLUTION" in generated.text
    assert generated.reveals_code is True


def test_generated_response_allows_reveals_code_without_a_code_section() -> None:
    """`reveals_code` is deliberately NOT "iff a code section exists".

    At L5/L6 the hint's own body *is* the partial/full solution, and it renders
    as the `next_hint` section -- so code can reach the learner with no section
    of kind `code`/`patch` present. Requiring a code section here would force
    `generate_response` to under-report the flag, which is what misled
    consumers gating on it. The directions that protect the learner are still
    enforced, and are covered by the two tests above and below: a code section
    requires the flag, and the flag requires a code-bearing assistance level.
    """
    response = GeneratedResponse(
        text="here is the whole solution",
        sections=[],
        assistance_level="full",
        reveals_code=True,
    )

    assert response.reveals_code is True


def test_generated_response_rejects_reveals_code_below_partial() -> None:
    """The flag may only be set where the ladder is allowed to carry code:
    `MAX_HINT_LEVEL_FOR_ASSISTANCE` puts `reveals_code`-capable rungs (L5/L6)
    at `partial` and `full` only.
    """
    for level in ("hint", "concept", "pseudocode"):
        with pytest.raises(ValidationError):
            GeneratedResponse(
                text="body",
                sections=[],
                assistance_level=level,  # pyright: ignore[reportArgumentType]
                reveals_code=True,
            )


# ---------------------------------------------------------------------------
# final_response node wiring
# ---------------------------------------------------------------------------


async def test_final_response_node_renders_hint_level_text_without_revealing_code() -> None:
    from app.graph.nodes import final_response
    from app.graph.state import AgentState, RawInput
    from tests.graph.test_phase7_wiring import (
        _plan as _wiring_plan,  # pyright: ignore[reportPrivateUsage]
    )
    from tests.graph.test_phase7_wiring import (
        _runtime,  # pyright: ignore[reportPrivateUsage]
    )

    result = _dsa_result_with_full_hint_and_code()
    state = AgentState(
        input=RawInput(text="help"),
        plan=_wiring_plan(assistance_level="hint"),
        agent_result=result,
    )

    update = await final_response(state, _runtime())

    assert update["response"] == update["generated_response"].text  # type: ignore[union-attr]
    assert update["generated_response"].reveals_code is False  # type: ignore[union-attr]
    assert "UNIQUE_SENTINEL_CODE" not in update["response"]  # type: ignore[index]
