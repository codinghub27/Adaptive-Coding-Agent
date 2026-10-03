"""Reactions, progression, misconception detectors and conceptual evidence (G1-G5)."""

import json
from pathlib import Path

import pytest

from app.memory.profile import CONCEPT_CEILING, PRIOR, apply_event, concept_update
from app.schemas.event import LearningEventCreate
from app.schemas.tutoring import AnswerGrade, GradeRecord, PracticeRecord, SessionProgress
from app.tutoring.bank import named_pattern, pending_for
from app.tutoring.misconceptions import detect_in_code
from app.tutoring.progression import requested_difficulty, session_difficulty
from app.tutoring.turn import execution_lines, raise_assistance, react

FIXTURES = (
    Path(__file__).resolve().parents[2] / "eval" / "behavior" / "misconception_fixtures.jsonl"
)


def _grade(grade: str, misconception: str | None = None) -> AnswerGrade:
    return AnswerGrade.model_validate(
        {"grade": grade, "method": "accepted", "misconception_id": misconception}
    )


def _react(question_id: str, grade: AnswerGrade, *, active: bool = False):  # noqa: ANN202
    pending = pending_for(question_id, assistance="hint")
    assert pending is not None
    return react(
        pending,
        grade,
        mode="balanced",
        cap=None,
        progress=SessionProgress.empty(),
        has_active_problem=active,
    )


def test_correct_advances_to_the_next_question_in_the_chain() -> None:
    reacted = _react("hashing.two_sum.complement", _grade("correct"))
    assert reacted.reaction == "advance"
    assert reacted.next_pending is not None
    assert reacted.next_pending.question_id == "hashing.two_sum.lookup"


def test_correct_at_the_end_of_a_chain_asks_for_the_code() -> None:
    reacted = _react("dfs.bridges.condition", _grade("correct"))
    assert reacted.next_pending is not None
    assert reacted.next_pending.kind == "code_submission"


def test_incorrect_reframes_with_the_misconception_at_the_same_level() -> None:
    reacted = _react(
        "bfs.vs_dfs.shortest", _grade("incorrect", "bfs.dfs_for_unweighted_shortest_path")
    )
    assert reacted.reaction == "reframe"
    assert reacted.misconception is not None
    assert reacted.next_pending is not None
    assert reacted.next_pending.question_id == "bfs.vs_dfs.reachability"
    assert reacted.assistance_after == reacted.assistance_before


def test_partial_asks_one_narrower_follow_up() -> None:
    grade = AnswerGrade.model_validate(
        {
            "grade": "partial",
            "method": "concepts",
            "matched_concepts": ["dfs"],
            "missing_concepts": ["discovery_time"],
        }
    )
    reacted = _react("dfs.bridges.approach", grade)
    assert reacted.reaction == "narrow"
    assert reacted.next_pending is not None
    assert reacted.next_pending.question_id == "dfs.bridges.approach~discovery_time"


def test_dont_know_raises_assistance_one_step_and_hands_off_on_a_problem() -> None:
    reacted = _react("recognition.bfs", _grade("dont_know"), active=True)
    assert reacted.reaction == "scaffold"
    assert (reacted.assistance_before, reacted.assistance_after) == ("hint", "concept")
    assert reacted.handoff


@pytest.mark.parametrize(
    ("mode", "start", "expected"),
    [
        ("guidance", "pseudocode", "partial"),
        ("guidance", "partial", "partial"),
        ("balanced", "pseudocode", "pseudocode"),
        ("challenge", "hint", "concept"),
        ("challenge", "concept", "concept"),
    ],
)
def test_raise_assistance_respects_the_mode_cap(mode: str, start: str, expected: str) -> None:
    assert raise_assistance(start, mode, None) == expected  # type: ignore[arg-type]


def test_raise_assistance_never_reaches_full() -> None:
    assert raise_assistance("partial", "guidance", None) == "partial"


def test_execution_lines_come_only_from_verdicts() -> None:
    assert execution_lines(
        learner=None, final=None, learner_submitted=True, reveals_code=False
    ) == ["Execution (your code): not executed"]
    assert (
        execution_lines(learner=None, final=None, learner_submitted=False, reveals_code=False) == []
    )


def test_requested_difficulty_phrases() -> None:
    assert requested_difficulty("lets start with easy problem") == "easy"
    assert requested_difficulty("Give me a hard graph problem.") == "hard"
    assert requested_difficulty("Can you give me a harder problem now?") == "harder"
    assert requested_difficulty("Give me a problem.") is None


def _progress(last: str | None, grades: list[str]) -> SessionProgress:
    practice = (
        PracticeRecord(title="t", topic="bfs", difficulty=last)  # type: ignore[arg-type]
        if last
        else None
    )
    return SessionProgress(
        last_practice=practice,
        grades=[GradeRecord(question_id="q", grade=g) for g in grades],  # type: ignore[arg-type]
        grades_at_practice=0,
    )


def test_harder_after_success_goes_up_one_level() -> None:
    assert session_difficulty("medium", _progress("medium", ["correct"]), "harder")[0] == "hard"


def test_harder_after_struggle_stays() -> None:
    level, reason = session_difficulty("medium", _progress("medium", ["dont_know"]), "harder")
    assert level == "medium"
    assert reason == "harder_denied_struggle"


def test_explicit_easy_is_honoured() -> None:
    assert session_difficulty("hard", _progress("hard", ["correct"]), "easy")[0] == "easy"


def test_named_pattern_reads_the_request() -> None:
    assert named_pattern("Give me a hard graph problem.") == "graphs"
    assert named_pattern("give me a two pointer problem") == "two_pointers"
    assert named_pattern("lets start with easy problem") is None


def _fixtures() -> list[dict[str, object]]:
    rows = [json.loads(line) for line in FIXTURES.read_text(encoding="utf-8").splitlines() if line]
    return [r for r in rows if r["kind"] == "code"]


@pytest.mark.parametrize("row", _fixtures(), ids=lambda r: str(r["id"]))
def test_code_detectors_on_the_catalog_fixtures(row: dict[str, object]) -> None:
    found = detect_in_code(str(row["code"]), str(row["topic"]))
    expected = row["expected"]
    if expected:
        assert expected in found
    else:
        assert found == []


def test_concept_evidence_moves_skill_by_concept_alpha_and_never_past_the_ceiling() -> None:
    assert concept_update(PRIOR, "correct") == pytest.approx(0.535)
    high = 0.66
    assert concept_update(high, "correct") == CONCEPT_CEILING
    assert concept_update(0.9, "correct") <= 0.9  # sandbox-earned level is never raised


def test_concept_event_never_sets_solved_and_updates_with_concept_alpha() -> None:
    event = LearningEventCreate(
        topic="bfs", solved=None, evidence_source="concept_check", concept_grade="correct"
    )
    # A fresh topic key stays at PRIOR; the family estimate moves by CONCEPT_ALPHA.
    skills, _errors = apply_event({"bfs": PRIOR}, {}, event)
    assert skills["bfs"] == PRIOR
    assert skills["family:graphs"] == pytest.approx(0.535)
    # A topic that already carries sandbox evidence moves too.
    skills, _errors = apply_event({"bfs": 0.6}, {}, event)
    assert skills["bfs"] == pytest.approx(0.625)
    exposure = LearningEventCreate(topic="bfs", solved=None)
    unchanged, _ = apply_event({"bfs": PRIOR}, {}, exposure)
    assert unchanged["bfs"] == PRIOR


def test_guidance_dont_know_on_a_code_request_unlocks_the_verified_solution() -> None:
    # Spec Example 1: "I don't know how" after the concept is clear -> build it
    # together. "full" reaches the DSA agent's verified-reference path only.
    pending = pending_for("hashing.two_sum.lookup", assistance="concept")
    assert pending is not None
    request = react(
        pending,
        _grade("correct"),
        mode="guidance",
        cap=None,
        progress=SessionProgress.empty(),
        has_active_problem=True,
    ).next_pending
    assert request is not None and request.kind == "code_submission"
    reacted = react(
        request,
        _grade("dont_know"),
        mode="guidance",
        cap=None,
        progress=SessionProgress.empty(),
        has_active_problem=True,
    )
    assert reacted.handoff
    assert reacted.assistance_after == "full"


def test_balanced_dont_know_on_a_code_request_stays_one_step() -> None:
    pending = pending_for("hashing.two_sum.lookup", assistance="concept")
    assert pending is not None
    request = react(
        pending,
        _grade("correct"),
        mode="balanced",
        cap=None,
        progress=SessionProgress.empty(),
        has_active_problem=True,
    ).next_pending
    assert request is not None
    reacted = react(
        request,
        _grade("dont_know"),
        mode="balanced",
        cap=None,
        progress=SessionProgress.empty(),
        has_active_problem=True,
    )
    assert reacted.assistance_after == "pseudocode"


def test_closing_a_chain_ends_on_the_reusable_lesson() -> None:
    reacted = _react("hashing.checked_vs_accessed", _grade("correct"))
    assert reacted.lesson is not None and "KeyError" in reacted.lesson
    assert _react("hashing.two_sum.complement", _grade("correct")).lesson is None
