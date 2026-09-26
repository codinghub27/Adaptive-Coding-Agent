"""Pure, deterministic renderers turning Phase 07 result fields into markdown.

Every `_render_*` helper here is a pure function of its input: no LLM calls,
no I/O. All rendered text may embed untrusted, display-only content lifted
from the learner's own code/problem statement or from upstream LLM output
(see `app.schemas.agent_results`) -- these helpers only select and format it,
never generate new claims.

`SAFE_FALLBACK_RESPONSE` lives here (not in `app.graph.nodes`) so that
`app.response` never has to import from `app.graph` (which would cycle,
since `app.graph.nodes` imports `app.response.generate`); `app.graph.nodes`
re-exports it for backwards compatibility.
"""

from collections.abc import Mapping
from typing import Final

from app.schemas.agent_results import (
    CodeStructureNode,
    LineExplanation,
    ReviewFinding,
    StaticFinding,
)
from app.schemas.execution import Verdict
from app.schemas.plan import ASSISTANCE_ORDER, AssistanceLevel
from app.schemas.response import ResponseSectionKind

__all__ = [
    "CODE_SECTION_KINDS",
    "DSA_SECTIONS_BY_ASSISTANCE",
    "SAFE_FALLBACK_RESPONSE",
    "SECTION_TITLES",
    "render_bullet_list",
    "render_code_block",
    "render_complexity",
    "render_findings",
    "render_line_explanations",
    "render_static_findings",
    "render_structure",
    "render_verdict",
]

SAFE_FALLBACK_RESPONSE: Final = (
    "I wasn't able to put together a full response for that -- could you try again?"
)

CODE_SECTION_KINDS: Final[frozenset[ResponseSectionKind]] = frozenset({"code", "patch"})

SECTION_TITLES: Final[Mapping[ResponseSectionKind, str]] = {
    # DSA
    "next_hint": "Your next hint",
    "understanding": "Understanding the problem",
    "constraints": "Constraints to keep in mind",
    "brute_force": "A brute-force approach",
    "why_slow": "Why that's too slow",
    "key_insight": "The key insight",
    "pseudocode": "Pseudocode",
    "code": "Solution code",
    "complexity": "Complexity",
    "common_mistakes": "Common mistakes to avoid",
    # debug
    "static_findings": "What the static checks found",
    "inferred_approach": "What your code is trying to do",
    "failing_case": "A case where it fails",
    "bug_explanation": "What's going wrong",
    "patch": "Suggested fix",
    "verification": "What the sandbox found",
    # explain
    "structure": "Code structure",
    "line_by_line": "Line by line",
    # review
    "findings": "Review findings",
    "correctness": "Correctness",
    # shared
    "next_steps": "Next steps",
    "citations": "References",
}

DSA_SECTIONS_BY_ASSISTANCE: Final[Mapping[AssistanceLevel, tuple[ResponseSectionKind, ...]]] = {
    "hint": ("next_hint", "next_steps"),
    "concept": ("next_hint", "next_steps", "understanding", "key_insight", "common_mistakes"),
    "pseudocode": (
        "next_hint",
        "next_steps",
        "understanding",
        "key_insight",
        "common_mistakes",
        "constraints",
        "brute_force",
        "why_slow",
        "pseudocode",
    ),
    "partial": (
        "next_hint",
        "next_steps",
        "understanding",
        "key_insight",
        "common_mistakes",
        "constraints",
        "brute_force",
        "why_slow",
        "pseudocode",
        "complexity",
    ),
    "full": (
        "next_hint",
        "next_steps",
        "understanding",
        "key_insight",
        "common_mistakes",
        "constraints",
        "brute_force",
        "why_slow",
        "pseudocode",
        "complexity",
        "code",
    ),
}
"""Cumulative section tables: each `AssistanceLevel`'s tuple is a superset of
every level before it in `ASSISTANCE_ORDER` (see `test_format.py`)."""


def render_bullet_list(items: list[str]) -> str:
    """Render a markdown bullet list, or empty string for no items."""
    cleaned = [item.strip() for item in items if item.strip()]
    if not cleaned:
        return ""
    return "\n".join(f"- {item}" for item in cleaned)


def render_code_block(code: str | None, *, language: str | None = None) -> str:
    """Render a fenced markdown code block, or empty string for blank code."""
    if code is None or not code.strip():
        return ""
    fence_lang = language or ""
    return f"```{fence_lang}\n{code}\n```"


def render_complexity(time: str | None, space: str | None) -> str:
    """Render "Time: X · Space: Y" from whichever of the two is present."""
    parts: list[str] = []
    if time and time.strip():
        parts.append(f"Time: {time.strip()}")
    if space and space.strip():
        parts.append(f"Space: {space.strip()}")
    if not parts:
        return ""
    return " · ".join(parts)


def render_static_findings(findings: list[StaticFinding]) -> str:
    """Render a bullet list of static-analysis findings."""
    items = [
        f"[{finding.severity}] {finding.message}"
        + (f" (line {finding.lineno})" if finding.lineno is not None else "")
        for finding in findings
    ]
    return render_bullet_list(items)


def render_findings(findings: list[ReviewFinding]) -> str:
    """Render review findings grouped by category with severity markers."""
    if not findings:
        return ""
    by_category: dict[str, list[ReviewFinding]] = {}
    for finding in findings:
        by_category.setdefault(finding.category, []).append(finding)

    blocks: list[str] = []
    for category, category_findings in by_category.items():
        lines = [f"**{category.replace('_', ' ').title()}**"]
        for finding in category_findings:
            location = f" (line {finding.lineno})" if finding.lineno is not None else ""
            entry = f"- [{finding.severity}] {finding.message}{location}"
            if finding.suggestion:
                entry += f" -- suggestion: {finding.suggestion}"
            lines.append(entry)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _render_structure_node(node: CodeStructureNode, depth: int = 0) -> list[str]:
    indent = "  " * depth
    lines = [f"{indent}- {node.kind} `{node.name}` (lines {node.lineno}-{node.end_lineno})"]
    for child in node.children:
        lines.extend(_render_structure_node(child, depth + 1))
    return lines


def render_structure(structure: CodeStructureNode | None) -> str:
    """Render an indented tree describing the learner's code structure."""
    if structure is None:
        return ""
    return "\n".join(_render_structure_node(structure))


def render_line_explanations(
    explanations: list[LineExplanation], *, limit: int | None = None
) -> str:
    """Render "lineno: code -- explanation" entries, optionally capped."""
    if not explanations:
        return ""
    capped = explanations if limit is None else explanations[:limit]
    lines = [f"`{item.lineno}`: `{item.code}` -- {item.explanation}" for item in capped]
    body = "\n".join(lines)
    if limit is not None and len(explanations) > limit:
        body += f"\n\n_(showing the first {limit} of {len(explanations)} lines)_"
    return body


def render_verdict(verdict: Verdict | None) -> str:
    """Render what the sandbox actually observed -- never overclaiming a fix."""
    if verdict is None:
        return ""
    if verdict.status in ("skipped", "inconclusive"):
        return f"Correctness could not be checked ({verdict.status}). {verdict.summary}".strip()
    if verdict.status == "pass":
        cases = f"{verdict.cases_passed}/{verdict.cases_total} cases"
        return f"The sandbox confirmed this passes ({cases}). {verdict.summary}".strip()
    return f"The sandbox still found a failure. {verdict.summary}".strip()


def _check_cumulative_sections() -> None:
    """Each level's section table must be a superset of the previous level's,
    so the table cannot silently regress (also covered by `test_format.py`).
    """
    for prev, curr in zip(ASSISTANCE_ORDER, ASSISTANCE_ORDER[1:], strict=False):
        assert set(DSA_SECTIONS_BY_ASSISTANCE[prev]) <= set(DSA_SECTIONS_BY_ASSISTANCE[curr]), (
            f"DSA_SECTIONS_BY_ASSISTANCE[{curr!r}] must be a superset of [{prev!r}]"
        )


_check_cumulative_sections()
