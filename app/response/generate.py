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

from typing import cast

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
    # Pattern-level corpus sections (P3). A problem-specific complexity from the
    # solver always wins over the pattern's typical one.
    for kind in ("recognition", "intuition", "complexity"):
        teaching = result.teaching_sections.get(kind)
        if teaching and kind not in bodies:
            bodies[cast("ResponseSectionKind", kind)] = (teaching, None)
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
    more_help_available = result.hint is not None and result.hint.level < result.hint.ceiling
    citations = list(result.citations)
    return sections, hint_level, hint_ceiling, more_help_available, citations, next_steps


_PATCH_RAN_CLEAN_NOTE = (
    "The fix above ran in the sandbox without the failure your code hit. There were no "
    "test cases to check its output against, so it is executed, not verified -- run it on "
    "an input whose answer you know."
)


_NOTHING_TO_FIX_NOTE = (
    "You asked for the corrected code: yours passed every test case in the sandbox, so "
    "there was nothing to fix. Your code is shown above."
)
_YOUR_CODE_TITLE = "Your code"


def _patch_ran_clean(result: DebugResult) -> bool:
    """The learner's code FAILED in the sandbox and the patch then ran to
    completion, with no test cases to judge its output (execution, not
    verification -- target behaviour section 15). Enough to show the patch
    with that label; never enough to call it fixed."""
    initial, final = result.initial_verdict, result.final_verdict
    return (
        initial is not None
        and initial.status == "fail"
        and final is not None
        and final.status == "inconclusive"
        and final.category == "no_tests"
    )


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
        # An explanation produced without a sandbox-proven failure is a careful
        # reading of the code, not a verified diagnosis, and must say so --
        # overclaiming here is exactly what the "never trust an LLM's claim of
        # correctness" rule exists to prevent.
        body = result.bug_explanation
        if result.not_executed:
            body = (
                body
                + "\n\n_Not executed -- the code sandbox is not available, so this is a"
                + " reading of your code, not a test of it._"
            )
        elif result.initial_verdict is None or result.initial_verdict.status != "fail":
            body = (
                body
                + "\n\n_Not verified by running your code -- no test cases could be"
                + " derived for this problem, so treat this as a careful reading"
                + " rather than proof._"
            )
        section = _section("bug_explanation", body)
        if section is not None:
            sections.append(section)

    ran_clean = _patch_ran_clean(result)
    if result.patched_code is not None and (result.fixed or ran_clean) and _may_reveal_code(plan):
        patch_section = _section("patch", render_code_block(result.patched_code))
        if patch_section is not None:
            sections.append(patch_section)

    patch_shown = any(section.kind == "patch" for section in sections)
    if result.presented_code is not None and not patch_shown and _may_reveal_code(plan):
        # F2: they asked for the code and there is no fix to show, so THEIR
        # code is handed back -- with what the sandbox knows about it.
        parts = [result.presented_label or "", render_code_block(result.presented_code)]
        if result.presented_notes:
            parts.append(render_bullet_list(result.presented_notes))
        body = "\n\n".join(part for part in parts if part)
        if body.strip():
            sections.append(ResponseSection(kind="code", title=_YOUR_CODE_TITLE, body=body))

    # The learner asked about THEIR code, so this section reports the verdict on
    # what they submitted (`initial_verdict`), not `final_verdict` -- which is
    # the verdict after the debugger patched it and therefore reads "passes"
    # exactly when their code was broken and got fixed. Rendering that as "the
    # sandbox found" told learners their failing code passed. The fix's own
    # verdict is reported separately, and only when a fix was actually proven.
    verdict_for_render = (
        result.initial_verdict if result.initial_verdict is not None else verification
    )
    # One "What the sandbox found" section: their code's verdict, then what
    # (if anything) is known about the fix. Two sections under one title read
    # as two different findings.
    found = [render_verdict(verdict_for_render)]
    if result.fixed and result.final_verdict is not None:
        found.append(
            f"A fix was found and verified in the sandbox: {result.final_verdict.summary}."
        )
    elif ran_clean and result.patched_code is not None and _may_reveal_code(plan):
        found.append(_PATCH_RAN_CLEAN_NOTE)
    elif (
        _may_reveal_code(plan)
        and result.initial_verdict is not None
        and result.initial_verdict.status == "pass"
    ):
        # They asked for the corrected code and theirs PASSED: say so, rather
        # than leave the request unanswered. Not on an unproven run -- there
        # the reading above may have named a bug, and this would contradict it.
        found.append(_NOTHING_TO_FIX_NOTE)
    verdict_section = _section("verification", "\n\n".join(part for part in found if part))
    if verdict_section is not None:
        sections.append(verdict_section)

    return sections, _debug_next_steps(result)


def _render_explain(
    result: ExplainResult, plan: TeachingPlan | None
) -> tuple[list[ResponseSection], list[str]]:
    sections: list[ResponseSection] = []

    if result.answer:
        answer_section = _section("explanation", result.answer)
        if answer_section is not None:
            sections.append(answer_section)

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
        citations = list(result.citations)
    elif isinstance(result, ExplainResult):
        sections, next_steps = _render_explain(result, plan)
        citations = list(result.citations)
    elif isinstance(result, ReviewResult):
        sections, next_steps = _render_review(result, verification)
        citations = list(result.citations)

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
        # The fallback text used none of the sources (P5 review).
        citations = []

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
