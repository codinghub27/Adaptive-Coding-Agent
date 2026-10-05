"""Per-turn tutoring decisions (ADAPTIVE-tutoring G1, G3, G4, G5).

Pure and deterministic: no LLM, no I/O. The graph nodes call these to

- react to a graded answer (`react`): advance / narrow / reframe / scaffold;
- decide which guiding question (if any) a turn ends with (`question_for_turn`);
- render the tutoring sections of the reply (`tutoring_sections`);
- fold the turn into the conversation's `SessionProgress` (`next_progress`).

Every string placed in front of the learner here is agent-authored (the bank,
the misconception catalog, or fixed copy below). Learner text never flows in.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from app.response.format import SECTION_TITLES, render_execution_line
from app.schemas.event import Difficulty
from app.schemas.execution import Verdict
from app.schemas.plan import ASSISTANCE_ORDER, AssistanceLevel, TeachingMode
from app.schemas.response import ResponseSection, ResponseSectionKind
from app.schemas.tutoring import (
    AnswerGrade,
    GradeRecord,
    PendingCheck,
    PracticeRecord,
    Reaction,
    SessionProgress,
)
from app.tutoring.bank import (
    NARROW_SEP,
    RECOGNITION_PREFIX,
    base_question_id,
    chain_start,
    code_submission_check,
    get_question,
    pattern_family,
    pending_for,
    recognition_id,
)
from app.tutoring.misconceptions import Misconception, get_misconception, is_catalog_id

__all__ = [
    "MODE_SCAFFOLD_CAP",
    "Reacted",
    "asked_key",
    "next_progress",
    "question_for_turn",
    "raise_assistance",
    "react",
    "execution_lines",
    "step_difficulty",
    "surfaced_misconceptions",
    "tutoring_sections",
]

#: How far a "don't know" may raise assistance, per teaching mode. Never past
#: `partial`: the full solution stays governed by the P4 escalation policy.
MODE_SCAFFOLD_CAP: Final[dict[TeachingMode, AssistanceLevel]] = {
    "guidance": "partial",
    "balanced": "pseudocode",
    "challenge": "concept",
}

#: After this many graded attempts at one question, stop re-asking it: the
#: second miss is taught and the lesson moves on (a third identical "Not
#: quite" + the same question is a loop, not tutoring).
MAX_ATTEMPTS: Final = 2
MAX_ASKED: Final = 60
MAX_GRADES: Final = 30

_GRADE_LEAD: Final[dict[str, str]] = {
    "correct": "**Correct.**",
    "partial": "**Partly there.**",
    "incorrect": "**Not quite.**",
    "dont_know": "**That's okay -- let's take it one step at a time.**",
}

_GENERIC_SCAFFOLD: Final = (
    "Let's build it together. I'll raise the level of help one step and walk you through "
    "the next piece of the solution."
)


def raise_assistance(
    level: AssistanceLevel | None, mode: TeachingMode, cap: AssistanceLevel | None = None
) -> AssistanceLevel:
    """One step more help than `level`, within the mode's (and client's) cap."""
    current = level or "hint"
    ceiling = MODE_SCAFFOLD_CAP[mode]
    if cap is not None and ASSISTANCE_ORDER.index(cap) < ASSISTANCE_ORDER.index(ceiling):
        ceiling = cap
    index = min(ASSISTANCE_ORDER.index(current) + 1, ASSISTANCE_ORDER.index(ceiling))
    return ASSISTANCE_ORDER[max(index, ASSISTANCE_ORDER.index(current))]


def _humanize(concept_id: str) -> str:
    return concept_id.replace("_", " ")


def _attempts(progress: SessionProgress, question_id: str) -> int:
    base = base_question_id(question_id)
    return sum(1 for g in progress.grades if base_question_id(g.question_id) == base)


@dataclass(slots=True)
class Reacted:
    """What a graded answer leads to."""

    reaction: Reaction
    feedback: str
    misconception: Misconception | None
    next_pending: PendingCheck | None
    handoff: bool
    assistance_before: AssistanceLevel
    assistance_after: AssistanceLevel
    lesson: str | None = None


def _after_correct(
    spec_id: str, pending: PendingCheck, level: AssistanceLevel
) -> PendingCheck | None:
    spec = get_question(spec_id)
    if spec is None:
        return None
    if spec.next:
        return pending_for(spec.next, problem_key=pending.problem_key, assistance=level)
    if spec.then == "code_submission" and spec.then_question:
        return code_submission_check(
            spec.then_question,
            topic=spec.topic,
            problem_key=pending.problem_key,
            assistance=level,
            lesson=spec.lesson,
        )
    return None


def react(
    pending: PendingCheck,
    grade: AnswerGrade,
    *,
    mode: TeachingMode,
    cap: AssistanceLevel | None,
    progress: SessionProgress,
    has_active_problem: bool,
) -> Reacted:
    """The brief's plan reaction, decided from the grade alone.

    correct   -> advance (next question in the chain, or the code request)
    partial   -> acknowledge + one narrower follow-up on a missing concept
    incorrect -> name the gap (misconception if any), reframe, ask again
    dont_know -> raise assistance one step (within the mode cap); on a problem,
                 hand the next rung to the DSA agent at the raised level
    """
    before: AssistanceLevel = pending.assistance_at_ask or "hint"
    spec = get_question(pending.question_id) if pending.kind == "question" else None
    base = base_question_id(pending.question_id)
    misconception = (
        get_misconception(grade.misconception_id)
        if grade.misconception_id is not None and is_catalog_id(grade.misconception_id)
        else None
    )
    attempts = _attempts(progress, pending.question_id)

    if grade.grade == "dont_know" or (
        pending.kind == "code_submission" and grade.grade != "correct"
    ):
        after = raise_assistance(before, mode, cap)
        handoff = has_active_problem and (
            pending.kind == "code_submission" or base.startswith(RECOGNITION_PREFIX)
        )
        if (
            handoff
            and pending.kind == "code_submission"
            and mode == "guidance"
            and (cap is None or cap == "full")
        ):
            # Guidance mode, the concept is already graded correct, and the
            # learner says they cannot write it: build it together. "full"
            # reaches the DSA agent's existing P4 path, which reveals ONLY a
            # sandbox-verified reference solution (or nothing).
            after = "full"
        feedback = spec.scaffold if spec is not None and spec.scaffold else _GENERIC_SCAFFOLD
        next_pending = None
        if not handoff and spec is not None and attempts + 1 < MAX_ATTEMPTS:
            next_pending = pending_for(
                pending.question_id, problem_key=pending.problem_key, assistance=after
            )
        return Reacted(
            "scaffold",
            feedback,
            None,
            next_pending,
            handoff,
            before,
            after,
            lesson=pending.lesson if pending.kind == "code_submission" else None,
        )

    if spec is None:  # a code request answered "correct" cannot happen; be safe
        return Reacted("advance", "", None, None, False, before, before)

    if grade.grade == "correct":
        next_pending = _after_correct(base, pending, before)
        # The takeaway closes a chain; when the chain ends in a code request it
        # waits for that code instead (shown with the reviewed / verified code).
        closes_chain = spec.next is None and spec.then is None
        feedback = spec.on_correct
        if grade.method == "llm_terminology":
            # Right mechanics under another technique's name: credit the
            # reasoning, fix the word, and move on -- not "Not quite".
            feedback = (
                "Your reasoning is right -- only the name is different. What you described "
                f"is usually called {_humanize(spec.topic)}."
            )
        return Reacted(
            "advance",
            feedback,
            None,
            next_pending,
            False,
            before,
            before,
            lesson=spec.lesson if closes_chain else None,
        )

    told_the_answer = grade.grade == "incorrect" and base.startswith(RECOGNITION_PREFIX)
    if attempts + 1 >= MAX_ATTEMPTS or told_the_answer:
        # A repeated miss -- or a wrong technique, whose correction already
        # names the right one -- is taught and the lesson moves on. Asking the
        # same question again would only invite the answer just given.
        feedback = " ".join(part for part in (spec.on_incorrect, spec.scaffold) if part)
        next_pending = _after_correct(base, pending, before)
        return Reacted("scaffold", feedback, misconception, next_pending, False, before, before)

    if grade.grade == "partial":
        concepts = {c.id: c for c in spec.expected_concepts}
        target = next(
            (cid for cid in grade.missing_concepts if cid in concepts and concepts[cid].follow_up),
            None,
        )
        matched = ", ".join(_humanize(c) for c in grade.matched_concepts)
        feedback = (
            f"You're on the right track{f' -- you have the {matched} part' if matched else ''}."
        )
        if target is not None and NARROW_SEP not in pending.question_id:
            next_pending = pending_for(
                base, problem_key=pending.problem_key, assistance=before, narrow_to=target
            )
        else:
            next_pending = pending_for(
                pending.question_id, problem_key=pending.problem_key, assistance=before
            )
        return Reacted("narrow", feedback, None, next_pending, False, before, before)

    # incorrect: name the gap, reframe with a small example, ask again (same rung)
    reframe_id = spec.reframe or pending.question_id
    feedback = spec.on_incorrect if misconception is None else ""
    next_pending = pending_for(reframe_id, problem_key=pending.problem_key, assistance=before)
    return Reacted("reframe", feedback, misconception, next_pending, False, before, before)


def asked_key(question_id: str, problem_key: str | None) -> str:
    return f"{base_question_id(question_id)}@{problem_key or '-'}"


def _unasked(question_id: str | None, progress: SessionProgress, problem_key: str | None) -> bool:
    return question_id is not None and asked_key(question_id, problem_key) not in progress.asked


def question_for_turn(
    *,
    route: str | None,
    progress: SessionProgress,
    problem_key: str | None,
    problem_text: str | None,
    topic: str | None,
    assistance: AssistanceLevel,
    reveals_code: bool,
    misconceptions: Sequence[str],
    practice: PracticeRecord | None,
    first_turn_on_problem: bool,
    topic_trusted: bool = True,
) -> PendingCheck | None:
    """The guiding question a non-grading turn ends with, or `None`.

    Exactly one, chosen in this order: a misconception found in the learner's
    code (ask its conceptual question); a practice problem's opening question;
    the curated chain for a problem/concept the turn is about; the pattern's
    recognition question on the first turn of a new problem -- only when the
    topic is trusted (a retrieval guess for a problem missing from the corpus
    would be taught and graded as the wrong pattern). Never when this turn
    reveals code, and never the same question twice for one problem.
    """
    if reveals_code:
        return None
    for misconception_id in misconceptions:
        item = get_misconception(misconception_id)
        if item is not None:
            return pending_for(item.question_id, problem_key=problem_key, assistance=assistance)
    if route == "practice" and practice is not None:
        qid = chain_start(practice.title) or recognition_id(practice.topic)
        return pending_for(qid, problem_key=problem_key, assistance=assistance)
    if route in ("dsa", "explain"):
        qid = chain_start(problem_text)
        if _unasked(qid, progress, problem_key) and qid is not None:
            return pending_for(qid, problem_key=problem_key, assistance=assistance)
        if route == "dsa" and first_turn_on_problem and topic is not None and topic_trusted:
            rid = recognition_id(topic)
            if _unasked(rid, progress, problem_key) and get_question(rid) is not None:
                return pending_for(rid, problem_key=problem_key, assistance=assistance)
    return None


def surfaced_misconceptions(
    watch: Sequence[str], topic: str | None, found_now: Sequence[str]
) -> list[str]:
    """Catalog misconceptions this learner showed BEFORE, relevant to `topic`."""
    if topic is None:
        return []
    family = pattern_family(topic)
    out: list[str] = []
    for item_id in watch:
        if item_id in found_now or not is_catalog_id(item_id):
            continue
        item = get_misconception(item_id)
        if item is None:
            continue
        if item.pattern == topic or pattern_family(item.pattern) == family:
            out.append(item_id)
    return out


def _section(kind: ResponseSectionKind, body: str) -> ResponseSection | None:
    if not body.strip():
        return None
    return ResponseSection(kind=kind, title=SECTION_TITLES[kind], body=body)


def _misconception_body(item: Misconception) -> str:
    return f"**{item.name}.** {item.correct_model}\n\n**Small example:** {item.example}"


@dataclass(slots=True)
class TutoringSections:
    """The reply's tutoring sections, in display order around the agent's own."""

    before: list[ResponseSection] = field(default_factory=list[ResponseSection])
    after: list[ResponseSection] = field(default_factory=list[ResponseSection])


def tutoring_sections(
    *,
    grade: AnswerGrade | None,
    feedback: str,
    misconceptions: Sequence[str],
    surfaced: Sequence[str],
    execution_lines: Sequence[str],
    question: PendingCheck | None,
    lead: str | None = None,
    lesson: str | None = None,
) -> TutoringSections:
    """Render the tutoring sections (all agent-authored text)."""
    out = TutoringSections()
    if lead:
        section = _section("lead", lead)
        if section is not None:
            out.before.append(section)
    if grade is not None:
        lead = _GRADE_LEAD[grade.grade]
        section = _section("answer_feedback", f"{lead} {feedback}".strip())
        if section is not None:
            out.before.append(section)
    for misconception_id in misconceptions:
        item = get_misconception(misconception_id)
        if item is not None:
            section = _section("misconception", _misconception_body(item))
            if section is not None:
                out.before.append(section)
    if surfaced:
        lines: list[str] = []
        for misconception_id in surfaced:
            item = get_misconception(misconception_id)
            if item is not None:
                lines.append(
                    f"- **{item.name}** -- you ran into this earlier. {item.correct_model}"
                )
        section = _section("watch_out", "\n".join(lines))
        if section is not None:
            out.after.append(section)
    if execution_lines:
        section = _section("execution", "\n\n".join(execution_lines))
        if section is not None:
            out.after.append(section)
    if lesson:
        section = _section("lesson", lesson)
        if section is not None:
            out.after.append(section)
    if question is not None:
        section = _section("check_question", question.question)
        if section is not None:
            out.after.append(section)
    return out


def execution_lines(
    *, learner: Verdict | None, final: Verdict | None, learner_submitted: bool, reveals_code: bool
) -> list[str]:
    """The "Execution" lines for a turn where code was submitted or revealed.

    A learner submission reports its OWN verdict (`initial_verdict`); when the
    turn's final verification is of different (patched) code, that is a
    second, separately labelled line. A revealed solution reports the turn's
    verification. Nothing ran -> "not executed".
    """
    if learner_submitted:
        lines = [render_execution_line(learner, " (your code)")]
        if final is not None and final != learner:
            lines.append(render_execution_line(final, " (suggested fix)"))
        return lines
    if reveals_code:
        return [render_execution_line(final)]
    return []


def next_progress(
    progress: SessionProgress,
    *,
    topic: str | None,
    grade: AnswerGrade | None,
    graded: PendingCheck | None,
    asked: PendingCheck | None,
    practice: PracticeRecord | None,
    misconceptions: Sequence[str],
    floor_key: str | None,
    floor: AssistanceLevel | None,
) -> SessionProgress:
    """Fold this turn into the conversation's progress record."""
    grades = list(progress.grades)
    if grade is not None and graded is not None:
        grades.append(
            GradeRecord(question_id=graded.question_id, topic=graded.topic, grade=grade.grade)
        )
    asked_list = list(progress.asked)
    if asked is not None and asked.kind == "question":
        key = asked_key(asked.question_id, asked.problem_key)
        if key not in asked_list:
            asked_list.append(key)
    seen = list(progress.misconceptions)
    for item in misconceptions:
        if item not in seen:
            seen.append(item)
    grades_at_practice = progress.grades_at_practice
    if practice is not None:
        grades_at_practice = len(grades[-MAX_GRADES:])
    floors = dict(progress.assistance_floor)
    if floor_key is not None and floor is not None:
        floors[floor_key] = floor
    # A copy, not a fresh record: fields this function does not own (the
    # earlier subject) must survive the turn.
    return progress.model_copy(
        update={
            "last_topic": topic or progress.last_topic,
            "last_practice": practice or progress.last_practice,
            "grades": grades[-MAX_GRADES:],
            "asked": asked_list[-MAX_ASKED:],
            "misconceptions": seen,
            "assistance_floor": floors,
            "grades_at_practice": grades_at_practice,
        }
    )


_DIFFICULTIES: Final[tuple[Difficulty, ...]] = ("easy", "medium", "hard")


def step_difficulty(level: Difficulty, delta: int) -> Difficulty:
    index = max(0, min(len(_DIFFICULTIES) - 1, _DIFFICULTIES.index(level) + delta))
    return _DIFFICULTIES[index]
