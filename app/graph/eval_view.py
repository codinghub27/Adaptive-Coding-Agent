"""Project a finished turn's `AgentState` onto `EvalDecision` (EVAL_MODE only).

Read-only: nothing here decides anything. Every field is copied from what a
node already put on the state, so the replay measures the agent's real
decision and not a second reading of it.
"""

from typing import Final

from app import clock
from app.graph.state import AgentState
from app.schemas.agent_results import DebugResult, DSAResult, ReviewResult
from app.schemas.eval import (
    EvalDecision,
    EvalEscalation,
    EvalEvidence,
    EvalFailingCase,
    EvalGrade,
    EvalPending,
    EvalPlan,
    EvalSandbox,
)
from app.schemas.event import ConceptGrade
from app.schemas.execution import Verdict

__all__ = ["build_eval_decision"]

#: `TurnDecision.evidence` -> the grade it amounts to (`vocab_map.json`).
_EVIDENCE_GRADE: Final[dict[str, ConceptGrade]] = {
    "correct": "correct",
    "terminology_error": "partial",
    "partially_correct": "partial",
    "incomplete": "partial",
    "conceptual_misconception": "incorrect",
    "implementation_error": "incorrect",
    "incorrect": "incorrect",
    "stuck": "dont_know",
}

#: Plan rationale tags that say the learner's stored skill shaped the plan.
_SKILL_RATIONALE: Final = frozenset({"weak_skill", "strong_skill"})

_ESCALATION_DENIED: Final = "escalation_denied"


def _grade(state: AgentState) -> EvalGrade | None:
    if state.answer_grade is not None:
        return EvalGrade(
            grade=state.answer_grade.grade, source="question", method=state.answer_grade.method
        )
    if state.decision is None:
        return None
    grade = _EVIDENCE_GRADE.get(state.decision.evidence)
    return EvalGrade(grade=grade, source="reading") if grade is not None else None


def _learner_verdict(state: AgentState) -> tuple[Verdict | None, Verdict | None]:
    """(verdict on the learner's code, verdict on the tutor's code)."""
    result = state.agent_result
    if isinstance(result, DebugResult):
        return result.initial_verdict, result.final_verdict
    if isinstance(result, DSAResult):
        return result.initial_verdict, state.verification
    if isinstance(result, ReviewResult):
        return result.correctness_verdict, None
    if state.tutoring is not None and state.tutoring.execution is not None:
        return state.tutoring.execution.learner, state.tutoring.execution.final
    return None, None


def _sandbox(state: AgentState) -> EvalSandbox | None:
    learner, final = _learner_verdict(state)
    verdict, subject = (learner, "learner") if learner is not None else (final, "reference")
    if verdict is None or verdict.status == "skipped":
        return None
    failing = (
        EvalFailingCase(
            name=verdict.first_failing_case, expected=verdict.expected, actual=verdict.actual
        )
        if verdict.first_failing_case is not None
        else None
    )
    return EvalSandbox(
        status=verdict.status,
        passed=verdict.cases_passed,
        total=verdict.cases_total,
        first_failing=failing,
        provenance=state.suite_source,
        subject=subject,
        final_status=final.status if final is not None and subject == "learner" else None,
    )


def _memory_used(state: AgentState) -> list[str]:
    facts: list[str] = list(state.memory_facts)
    if state.adaptation is not None:
        facts += [f"common_errors.{tag}" for tag in state.adaptation.recurring]
    if state.tutoring is not None:
        facts += [f"common_errors.{tag}" for tag in state.tutoring.surfaced_misconceptions]
    plan = state.plan
    if plan is not None and plan.topic and _SKILL_RATIONALE.intersection(plan.rationale):
        facts.append(f"skills.{plan.topic}")
    return list(dict.fromkeys(facts))


def _escalation(state: AgentState) -> EvalEscalation | None:
    plan = state.plan
    generated = state.generated_response
    if plan is None or generated is None or not generated.reveals_code:
        return None
    if any(tag.startswith(_ESCALATION_DENIED) for tag in plan.rationale):
        return None
    # The planner escalates to `full` only on an explicit ask for the code
    # (`build_plan`: `ceiling_reached and explicit_ask and effort`).
    asked = state.decision is not None and state.decision.wants_code
    reason = "explicit_ask" if "escalated" in plan.rationale or asked else "plan"
    _, final = _learner_verdict(state)
    reference = final or state.verification
    verified = reference is not None and reference.status == "pass"
    return EvalEscalation(
        reason=reason, teaching_mode=state.input.teaching_mode, verified_reference=verified
    )


def build_eval_decision(state: AgentState) -> EvalDecision:
    """The decision record for one finished turn."""
    decision = state.decision
    plan = state.plan
    generated = state.generated_response
    pending = state.next_pending
    progress = state.next_progress or state.session_progress
    return EvalDecision(
        intent=state.intent.intent if state.intent is not None else None,
        intent_confidence=state.intent.confidence if state.intent is not None else None,
        intent_source=state.intent.source if state.intent is not None else None,
        route=state.route or "clarify",
        move=decision.move if decision is not None else None,
        subject=decision.subject if decision is not None else None,
        scaffold=decision.scaffold if decision is not None else None,
        wants_code=decision.wants_code if decision is not None else False,
        withhold=decision.withhold if decision is not None else False,
        reasons=list(decision.reasons) if decision is not None else [],
        teaching_mode=state.input.teaching_mode,
        plan=(
            EvalPlan(
                difficulty=plan.difficulty,
                assistance_level=(
                    generated.assistance_level if generated is not None else plan.assistance_level
                ),
                hint_rung=(
                    int(generated.hint_level)
                    if generated is not None and generated.hint_level is not None
                    else None
                ),
                topic=plan.topic,
                skill_level=plan.skill_level,
                rationale=list(plan.rationale),
            )
            if plan is not None
            else None
        ),
        grade=_grade(state),
        pending_check=(
            EvalPending(kind=pending.kind, topic=pending.topic, question_id=pending.question_id)
            if pending is not None
            else None
        ),
        sandbox=_sandbox(state),
        evidence=[
            EvalEvidence(
                topic=event.topic,
                source=event.evidence_source,
                solved=event.solved,
                concept_grade=event.concept_grade,
                needed_full_solution=event.needed_full_solution,
                hints_used=event.hints_used,
                errors=list(event.errors),
            )
            for event in state.events
        ],
        misconceptions=list(state.tutoring.misconceptions) if state.tutoring is not None else [],
        memory_used=_memory_used(state),
        escalation=_escalation(state),
        reveals_code=generated.reveals_code if generated is not None else False,
        skill_deltas=dict(state.skill_deltas),
        adaptations=list(state.adaptation.notes) if state.adaptation is not None else [],
        last_thread=progress.last_thread if progress is not None else None,
        clock=clock.now(),
    )
