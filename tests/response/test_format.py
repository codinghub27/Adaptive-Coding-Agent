"""Tests for the pure section table and renderers in `app.response.format`."""

from app.response.format import (
    DSA_SECTIONS_BY_ASSISTANCE,
    SECTION_TITLES,
    render_bullet_list,
    render_code_block,
    render_complexity,
    render_findings,
    render_line_explanations,
    render_static_findings,
    render_structure,
    render_verdict,
)
from app.schemas.agent_results import (
    CodeStructureNode,
    LineExplanation,
    ReviewFinding,
    StaticFinding,
)
from app.schemas.execution import Verdict
from app.schemas.plan import ASSISTANCE_ORDER
from app.schemas.response import ResponseSectionKind

# ---------------------------------------------------------------------------
# The section table
# ---------------------------------------------------------------------------


def test_dsa_sections_by_assistance_is_cumulative_across_assistance_order() -> None:
    for prev, curr in zip(ASSISTANCE_ORDER, ASSISTANCE_ORDER[1:], strict=False):
        prev_kinds = set(DSA_SECTIONS_BY_ASSISTANCE[prev])
        curr_kinds = set(DSA_SECTIONS_BY_ASSISTANCE[curr])
        assert prev_kinds <= curr_kinds, f"{curr!r} must be a superset of {prev!r}"


def test_every_response_section_kind_has_a_title() -> None:
    kinds: tuple[ResponseSectionKind, ...] = (
        "next_hint",
        "understanding",
        "constraints",
        "brute_force",
        "why_slow",
        "key_insight",
        "pseudocode",
        "code",
        "complexity",
        "common_mistakes",
        "static_findings",
        "inferred_approach",
        "failing_case",
        "bug_explanation",
        "patch",
        "verification",
        "structure",
        "line_by_line",
        "findings",
        "correctness",
        "next_steps",
        "citations",
    )
    for kind in kinds:
        assert kind in SECTION_TITLES
        assert SECTION_TITLES[kind].strip()


# ---------------------------------------------------------------------------
# Renderers: empty in -> empty out, populated in -> non-empty markdown out
# ---------------------------------------------------------------------------


def test_render_bullet_list_empty_and_populated() -> None:
    assert render_bullet_list([]) == ""
    assert render_bullet_list(["", "  "]) == ""
    body = render_bullet_list(["do this", "then that"])
    assert body
    assert "do this" in body
    assert "then that" in body


def test_render_code_block_empty_and_populated() -> None:
    assert render_code_block(None) == ""
    assert render_code_block("   ") == ""
    body = render_code_block("x = 1")
    assert body
    assert "x = 1" in body
    assert "```" in body


def test_render_complexity_empty_and_populated() -> None:
    assert render_complexity(None, None) == ""
    assert render_complexity("", "") == ""
    body = render_complexity("O(n)", "O(1)")
    assert "O(n)" in body
    assert "O(1)" in body


def test_render_static_findings_empty_and_populated() -> None:
    assert render_static_findings([]) == ""
    body = render_static_findings(
        [StaticFinding(tool="ast", message="unused variable", lineno=3, severity="minor")]
    )
    assert "unused variable" in body


def test_render_findings_empty_and_populated() -> None:
    assert render_findings([]) == ""
    body = render_findings(
        [ReviewFinding(category="readability", severity="minor", message="rename this variable")]
    )
    assert "rename this variable" in body


def test_render_structure_empty_and_populated() -> None:
    assert render_structure(None) == ""
    node = CodeStructureNode(kind="function", name="f", lineno=1, end_lineno=3)
    body = render_structure(node)
    assert "f" in body


def test_render_line_explanations_empty_and_populated() -> None:
    assert render_line_explanations([]) == ""
    body = render_line_explanations(
        [LineExplanation(lineno=1, code="x = 1", explanation="assigns one to x")]
    )
    assert "assigns one to x" in body


def test_render_line_explanations_respects_limit() -> None:
    explanations = [
        LineExplanation(lineno=i, code=f"line {i}", explanation=f"explains {i}")
        for i in range(1, 20)
    ]
    body = render_line_explanations(explanations, limit=5)
    assert "explains 1\n" in body or body.count("explains ") >= 5
    for i in range(6, 20):
        assert f"explains {i}" not in body


def test_render_verdict_empty_and_populated() -> None:
    assert render_verdict(None) == ""
    skipped = Verdict(status="skipped", summary="no tests were run")
    assert "could not be checked" in render_verdict(skipped)
    passing = Verdict(status="pass", summary="all good", cases_passed=2, cases_total=2)
    assert "confirmed" in render_verdict(passing)
    failing = Verdict(
        status="fail", category="wrong_answer", summary="case 1 failed", cases_total=2
    )
    assert "failure" in render_verdict(failing)
