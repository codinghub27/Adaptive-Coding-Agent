"""Assemble the final learner-facing reply from a Phase 07 `AgentResult`.

This module is **pure and deterministic**: it never calls an LLM. All prose
already exists inside the agent result (hint text, bug explanations, line
explanations, review findings); this layer only selects and arranges it
according to the turn's `TeachingPlan`.

Design invariant this module enforces structurally, not by prompting: a
hint-level turn can never render full code. `_may_reveal_code` is the single
function that decides whether a code-bearing section is allowed to appear;
every code-bearing section (`code`, `patch`) routes through it. If `plan` is
`None` it is treated as the most conservative assistance level (`"hint"`),
never as `"full"`.

Fallback ladder (also covered by `tests/response/test_generate.py`):
assembled sections -> else `fallback_text` (when non-blank) -> else
`SAFE_FALLBACK_RESPONSE`. `GeneratedResponse.text` can therefore never be
empty. When falling back, `sections` is empty and `reveals_code` is `False`.
"""

from app.response.format import (
    DSA_SECTIONS_BY_ASSISTANCE,
    SAFE_FALLBACK_RESPONSE,
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
    AgentResult,
    DebugResult,
    DSAResult,
    ExplainResult,
    HintLevel,
    HintResult,
    ReviewResult,
)
from app.schemas.execution import Verdict
from app.schemas.plan import AssistanceLevel, TeachingPlan
from app.schemas.response import (
    CODE_SECTION_KINDS,
    GeneratedResponse,
    ResponseSection,
    ResponseSectionKind,
)

__all__ = ["generate_response"]

_EXPLAIN_LINE_BY_LINE_LIMIT_WHEN_CONCISE = 12

_REVIEW_SEVERITY_RANK: dict[str, int] = {"info": 0, "minor": 1, "major": 2}


def _may_reveal_code(plan: TeachingPlan | None) -> bool:
    """The single gate deciding whether a code-bearing section may appear.

    `plan=None` is the most conservative case and must never reveal code.
    """
    return plan is not None and plan.assistance_level == "full"


def _section(
    kind: ResponseSectionKind, body: str, *, language: str | None = None
) -> ResponseSection | None:
    """Build a section, or `None` if `body` is blank -- never emit empty sections."""
    if not body.strip():
        return None
    return ResponseSection(kind=kind, title=SECTION_TITLES[kind], body=body, language=language)


def _dsa_next_steps(result: DSAResult) -> list[str]:
    if result.hint is None:
        return []
    if result.hint.level < result.hint.ceiling:
        return ["Ask for the next hint if you're still stuck."]
    return ["This is as far as this hint level goes -- try implementing the idea and testing it."]


def _hint_withheld(hint: HintResult, plan: TeachingPlan | None) -> bool:
    """Must this hint be withheld because its own body would reveal code?

    A hint at L5/L6 carries the partial/full solution in its text, and the hint
    renders at every assistance level -- so a result built for one plan but
    rendered against a lower-assistance plan would leak code through the hint
    body, bypassing the section-level gate entirely. Withholding it keeps the
    phase's invariant structural rather than merely conventional: the response
    layer refuses to emit code the plan does not allow, even when an upstream
    agent hands it code it should not have.
    """
    return hint.reveals_code and not _may_reveal_code(plan)


def _render_dsa(
    result: DSAResult, level: AssistanceLevel, plan: TeachingPlan | None
) -> tuple[list[ResponseSection], HintLevel | None, HintLevel | None, bool, list[str], list[str]]:
    allowed = DSA_SECTIONS_BY_ASSISTANCE[level]
    next_steps = _dsa_next_steps(result)

    bodies: dict[ResponseSectionKind, tuple[str, str | None]] = {}
    if result.hint is not None and not _hint_withheld(result.hint, plan):
        bodies["next_hint"] = (result.hint.text, None)
    if next_steps:
        bodies["next_steps"] = (render_bullet_list(next_steps), None)
    if result.understanding:
        bodies["understanding"] = (result.understanding, None)
    if result.key_insight:
        bodies["key_insight"] = (result.key_insight, None)
    if result.common_mistakes:
        bodies["common_mistakes"] = (render_bullet_list(result.common_mistakes), None)
    if result.constraints:
        bodies["constraints"] = (render_bullet_list(result.constraints), None)
    if result.brute_force:
        bodies["brute_force"] = (result.brute_force, None)
    if result.why_slow:
        bodies["why_slow"] = (result.why_slow, None)
    if result.pseudocode:
        bodies["pseudocode"] = (render_code_block(result.pseudocode), None)
    complexity_body = render_complexity(result.complexity_time, result.complexity_space)
    if complexity_body:
        bodies["complexity"] = (complexity_body, None)
    if result.code is not None and _may_reveal_code(plan):
        code_body = render_code_block(result.code)
        if code_body:
            bodies["code"] = (code_body, None)

    sections: list[ResponseSection] = []
    for kind in allowed:
        if kind not in bodies:
            continue
        body, language = bodies[kind]
        section = _section(kind, body, language=language)
        if section is not None:
            sections.append(section)

    hint_level = result.hint.level if result.hint is not None else None
    hint_ceiling = result.hint.ceiling if result.hint is not None else None
    more_help_available = (
        result.hint is not None and result.hint.level < result.hint.ceiling
    )
    citations = list(result.citations)
    return sections, hint_level, hint_ceiling, more_help_available, citations, next_steps


def _debug_next_steps(result: DebugResult) -> list[str]:
    has_content = bool(
        result.static_findings
        or result.inferred_approach
        or result.failing_case
        or result.bug_explanation
        or result.patched_code is not None
    )
    if not has_content:
        return []
    if result.fixed:
        return ["Re-run your own test cases to confirm the fix holds."]
    return ["Apply the suggested fix, then re-submit your code so it can be verified."]


def _render_debug(
    result: DebugResult, plan: TeachingPlan | None, verification: Verdict | None
) -> tuple[list[ResponseSection], list[str]]:
    sections: list[ResponseSection] = []

    static_section = _section("static_findings", render_static_findings(result.static_findings))
    if static_section is not None:
        sections.append(static_section)

    if result.inferred_approach:
        section = _section("inferred_approach", result.inferred_approach)
        if section is not None:
            sections.append(section)

    if result.failing_case:
        section = _section("failing_case", result.failing_case)
        if section is not None:
            sections.append(section)

    if result.bug_explanation:
        section = _section("bug_explanation", result.bug_explanation)
        if section is not None:
            sections.append(section)

    if result.patched_code is not None and result.fixed and _may_reveal_code(plan):
        patch_section = _section("patch", render_code_block(result.patched_code))
        if patch_section is not None:
            sections.append(patch_section)

    verdict_for_render = result.final_verdict if result.final_verdict is not None else verification
    verdict_section = _section("verification", render_verdict(verdict_for_render))
    if verdict_section is not None:
        sections.append(verdict_section)

    return sections, _debug_next_steps(result)


def _render_explain(
    result: ExplainResult, plan: TeachingPlan | None
) -> tuple[list[ResponseSection], list[str]]:
    sections: list[ResponseSection] = []

    structure_section = _section("structure", render_structure(result.structure))
    if structure_section is not None:
        sections.append(structure_section)

    concise = plan is not None and plan.concise
    limit = _EXPLAIN_LINE_BY_LINE_LIMIT_WHEN_CONCISE if concise else None
    line_section = _section(
        "line_by_line", render_line_explanations(result.line_explanations, limit=limit)
    )
    if line_section is not None:
        sections.append(line_section)

    complexity_body = render_complexity(result.complexity_time, result.complexity_space)
    if result.complexity_rationale and result.complexity_rationale.strip():
        complexity_body = (
            f"{complexity_body}\n\n{result.complexity_rationale}"
            if complexity_body
            else result.complexity_rationale
        )
    complexity_section = _section("complexity", complexity_body)
    if complexity_section is not None:
        sections.append(complexity_section)

    return sections, []


def _review_next_steps(result: ReviewResult) -> list[str]:
    if not result.findings:
        return []
    top = max(result.findings, key=lambda finding: _REVIEW_SEVERITY_RANK[finding.severity])
    return [f"Address this {top.severity} finding first: {top.message}"]


def _render_review(
    result: ReviewResult, verification: Verdict | None
) -> tuple[list[ResponseSection], list[str]]:
    sections: list[ResponseSection] = []

    findings_section = _section("findings", render_findings(result.findings))
    if findings_section is not None:
        sections.append(findings_section)

    if result.claims_correct:
        correctness_section = _section("correctness", render_verdict(result.correctness_verdict))
        if correctness_section is not None:
            sections.append(correctness_section)
    else:
        verdict_for_render = (
            result.correctness_verdict if result.correctness_verdict is not None else verification
        )
        if verdict_for_render is not None:
            correctness_section = _section("correctness", render_verdict(verdict_for_render))
            if correctness_section is not None:
                sections.append(correctness_section)

    return sections, _review_next_steps(result)


def _assemble_text(sections: list[ResponseSection]) -> str:
    return "\n\n".join(f"## {section.title}\n\n{section.body}" for section in sections)


def generate_response(
    *,
    result: AgentResult | None,
    plan: TeachingPlan | None,
    verification: Verdict | None = None,
    fallback_text: str | None = None,
) -> GeneratedResponse:
    """Build the final `GeneratedResponse` for a turn from its agent result."""
    assistance_level: AssistanceLevel = plan.assistance_level if plan is not None else "hint"

    sections: list[ResponseSection] = []
    hint_level: HintLevel | None = None
    hint_ceiling: HintLevel | None = None
    more_help_available = False
    citations: list[str] = []
    next_steps: list[str] = []

    if isinstance(result, DSAResult):
        (
            sections,
            hint_level,
            hint_ceiling,
            more_help_available,
            citations,
            next_steps,
        ) = _render_dsa(result, assistance_level, plan)
    elif isinstance(result, DebugResult):
        sections, next_steps = _render_debug(result, plan, verification)
    elif isinstance(result, ExplainResult):
        sections, next_steps = _render_explain(result, plan)
    elif isinstance(result, ReviewResult):
        sections, next_steps = _render_review(result, verification)

    # A code-bearing section is not the only way code reaches the learner: at
    # L5/L6 the hint's own body may be the partial/full solution, and that hint
    # renders as the `next_hint` section. Both sources count.
    hint_reveals_code = (
        isinstance(result, DSAResult)
        and result.hint is not None
        and result.hint.reveals_code
        and not _hint_withheld(result.hint, plan)
    )
    reveals_code = (
        any(section.kind in CODE_SECTION_KINDS for section in sections) or hint_reveals_code
    )

    text = _assemble_text(sections)
    if not text.strip():
        stripped_fallback = fallback_text.strip() if fallback_text is not None else ""
        text = stripped_fallback if stripped_fallback else SAFE_FALLBACK_RESPONSE
        sections = []
        reveals_code = False

    return GeneratedResponse(
        text=text,
        sections=sections,
        assistance_level=assistance_level,
        hint_level=hint_level,
        hint_ceiling=hint_ceiling,
        more_help_available=more_help_available,
        reveals_code=reveals_code,
        next_steps=next_steps,
        citations=citations,
    )
