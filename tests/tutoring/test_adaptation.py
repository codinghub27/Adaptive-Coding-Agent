"""What changes in a reply because of the learner (target behaviour 17-22).

Measured (docs/BEHAVIOR_GAP.md, section 4): every one of 48 real turns was
planned at skill 0.50, nothing in any reply changed because of who the learner
was, and "Adapted to your level" was shown on every stored reply.
"""

from datetime import UTC, datetime
from uuid import uuid4

from app.graph.nodes import (
    _adaptation,  # pyright: ignore[reportPrivateUsage]
    _log_evidence,  # pyright: ignore[reportPrivateUsage]
)
from app.graph.state import AgentState, RawInput
from app.schemas.conversation import MessageView
from app.schemas.decision import Evidence, TurnDecision, scaffold_for
from app.schemas.plan import TeachingPlan
from app.schemas.profile import LearnerProfileView
from app.schemas.tutoring import SessionProgress
from app.tutoring.adaptation import (
    LINK_PHRASE,
    STRUGGLE,
    SUCCESS,
    Adaptation,
    adapt,
    streak,
    tutor_state_block,
)
from app.tutoring.misconceptions import catalog
from app.tutoring.turn import tutoring_sections


def _decision(evidence: Evidence = "none", *, wants_code: bool = False) -> TurnDecision:
    return TurnDecision(
        subject="active",
        evidence=evidence,
        move="code" if wants_code else "step",
        scaffold=scaffold_for(evidence),
        wants_code=wants_code,
    )


def _adapt(evidence: Evidence, log: list[str], **kwargs: object) -> Adaptation:
    return adapt(
        decision=_decision(evidence),
        pitch="intermediate",
        topic="hashing",
        evidence_log=log,
        **kwargs,  # type: ignore[arg-type]
    )


def test_a_request_neither_extends_nor_breaks_a_streak() -> None:
    assert streak(["stuck", "incorrect"], "none", STRUGGLE) == 2
    assert streak(["stuck"], "stuck", STRUGGLE) == 2
    assert streak(["stuck", "correct"], "stuck", STRUGGLE) == 1
    assert streak(["correct"], "terminology_error", SUCCESS) == 2


def test_with_no_evidence_nothing_is_adapted_and_no_badge_shows() -> None:
    adaptation = _adapt("none", [])
    assert adaptation.representation == "plain"
    assert adaptation.notes == []
    assert adaptation.adapted is False


def test_repeated_struggle_changes_the_representation_not_the_wording() -> None:
    """Section 18: explanation -> example -> visual -> simpler question. Pseudocode
    and code are the hint ladder's own rungs, not a restyling of a hint."""
    seen = [_adapt("stuck", ["stuck"] * n).representation for n in range(4)]
    assert seen == ["worked_example", "diagram", "simpler_question", "simpler_question"]
    second = _adapt("incorrect", ["stuck"])
    assert second.adapted is True
    assert "drew it out" in second.notes[0]


def test_two_right_answers_skip_the_small_steps() -> None:
    assert _adapt("correct", []).skip_ahead is False
    after_two = _adapt("correct", ["correct"])
    assert after_two.skip_ahead is True
    assert after_two.representation == "plain"
    assert any("skipped the small steps" in note for note in after_two.notes)
    # A miss in between starts the count again.
    assert _adapt("correct", ["correct", "incorrect"]).skip_ahead is False


def test_a_code_ask_is_not_restyled() -> None:
    adaptation = adapt(
        decision=_decision("none", wants_code=True),
        pitch="intermediate",
        topic="hashing",
        evidence_log=["stuck", "stuck"],
    )
    assert adaptation.representation == "plain"
    assert adaptation.skip_ahead is False


def test_the_pitch_follows_the_skill_estimate_and_says_so() -> None:
    weak = adapt(decision=_decision(), pitch="beginner", topic="two_pointers", evidence_log=[])
    assert weak.adapted is True
    assert "two pointers" in weak.notes[0]
    strong = adapt(decision=_decision(), pitch="advanced", topic="graphs", evidence_log=[])
    assert "Skipping the basics" in strong.notes[0]


def test_the_prompt_block_carries_the_decision_and_the_instruction() -> None:
    adaptation = _adapt("stuck", ["stuck"])
    block = tutor_state_block(_decision("stuck"), adaptation)
    assert block.startswith("<tutor_state>") and block.endswith("</tutor_state>")
    assert "learner_showed: stuck" in block
    assert "scaffolding: up" in block
    assert "representation: diagram" in block
    assert "draw it" in block
    assert tutor_state_block(None, None) == ""


_EARLIER = MessageView(
    id=uuid4(),
    conversation_id=uuid4(),
    seq=1,
    role="assistant",
    content="What number do you need with 2 to make 9?",
    intent=None,
    created_at=datetime.now(UTC),
)


def _state(
    *,
    evidence: Evidence,
    progress: SessionProgress | None = None,
    skill: float = 0.5,
    relation: str = "followup",
) -> AgentState:
    return AgentState(
        input=RawInput(text="x"),
        recent_context=[_EARLIER] if relation != "new" else [],
        decision=_decision(evidence),
        session_progress=progress,
        problem_key="_p1",
        problem_relation=relation,  # type: ignore[arg-type]
        profile=LearnerProfileView.empty(),
        plan=TeachingPlan(
            difficulty="medium",
            assistance_level="concept",
            solution_strategy="socratic_hints",
            topic="hashing",
            skill_level=skill,
        ),
    )


def test_the_log_carries_what_was_shown_from_one_turn_to_the_next() -> None:
    progress = SessionProgress.empty()
    for evidence in ("stuck", "none", "incorrect"):
        state = _state(evidence=evidence, progress=progress)  # type: ignore[arg-type]
        progress = _log_evidence(progress, state)
    assert progress.evidence_log == ["stuck", "incorrect"]
    assert progress.evidence_key == "_p1"

    third = _state(evidence="stuck", progress=progress)
    assert third.plan is not None
    adaptation = _adaptation(third, third.plan)
    assert adaptation.struggles == 3
    assert adaptation.representation == "simpler_question"


def test_a_new_problem_starts_a_new_log() -> None:
    progress = SessionProgress.empty().model_copy(
        update={"evidence_log": ["stuck", "stuck"], "evidence_key": "_p1"}
    )
    fresh = _state(evidence="none", progress=progress, relation="new")
    assert fresh.plan is not None
    assert _adaptation(fresh, fresh.plan).representation == "plain"
    assert _log_evidence(progress, fresh).evidence_log == []


def test_a_weak_and_a_strong_learner_are_pitched_differently() -> None:
    weak = _state(evidence="none", skill=0.2)
    strong = _state(evidence="none", skill=0.9)
    assert weak.plan is not None and strong.plan is not None
    assert _adaptation(weak, weak.plan).pitch == "beginner"
    assert _adaptation(strong, strong.plan).pitch == "advanced"
    middle = _state(evidence="none")
    assert middle.plan is not None
    assert _adaptation(middle, middle.plan).adapted is False


def test_a_repeated_misconception_is_linked_to_the_earlier_time() -> None:
    """Section 19: correct normally, then link it, then a mini-lesson."""
    item = catalog()[0]

    def body(times: int) -> str:
        sections = tutoring_sections(
            grade=None,
            feedback="",
            misconceptions=[item.id],
            surfaced=[],
            execution_lines=[],
            question=None,
            seen_before={item.id: times},
        )
        return sections.before[0].body

    first, second, third = body(0), body(1), body(2)
    assert "before" not in first.split(item.name)[0]
    assert "same mix-up you ran into before" in second
    assert "third time" in third and "Try this" in third
    assert item.name in first and item.name in second and item.name in third


def test_linking_a_repeat_is_itself_an_adaptation() -> None:
    adaptation = _adapt("none", []).linked_to(["trees.returnable_vs_global_path"])
    assert adaptation.adapted is True
    assert adaptation.recurring == ["trees.returnable_vs_global_path"]
    assert adaptation.linked_to(["trees.returnable_vs_global_path"]).notes == adaptation.notes


def test_not_knowing_how_to_start_is_not_a_failed_step() -> None:
    """Measured live: a first message "I don't understand how to start" was
    badged "You were stuck on the last step" when there had been no step."""
    opening = _state(evidence="stuck", relation="new")
    assert opening.plan is not None
    adaptation = _adaptation(opening, opening.plan)
    assert adaptation.representation == "plain"
    assert adaptation.adapted is False
    assert _log_evidence(SessionProgress.empty(), opening).evidence_log == []
    direct = adapt(
        decision=_decision("stuck"),
        pitch="intermediate",
        topic="hashing",
        evidence_log=[],
        first_turn=True,
    )
    assert direct.struggles == 0


def test_the_learners_own_no_answer_yet_is_in_the_prompt() -> None:
    decision = _decision("none").model_copy(update={"withhold": True})
    block = tutor_state_block(decision, _adapt("none", []))
    assert "withhold_answer: yes" in block
    assert "do NOT state the corrected line" in block


def test_a_known_mistake_is_not_a_badge_until_the_reply_links_it() -> None:
    """Knowing about an earlier mistake changes nothing by itself. The prompt
    carries what it was; the badge says so only once the reply has linked it."""
    item = catalog()[0]
    known = _adapt("none", [], recurring=[item.id])
    assert known.adapted is False
    block = tutor_state_block(_decision("none"), known)
    assert f"made_before: {item.name}." in block
    assert LINK_PHRASE in block
    assert known.linked_to([item.id]).adapted is True


def test_a_note_is_kept_only_for_what_the_reply_did() -> None:
    """Measured live: "this one is a worked example" over a reply that was the
    full solution, and "I skipped the small steps" over the question bank's
    fixed text."""
    planned = _adapt("stuck", ["stuck"])
    assert planned.adapted is True
    assert planned.as_delivered(by_tutor_step=True) == planned
    undone = planned.as_delivered(by_tutor_step=False)
    assert (undone.representation, undone.adapted) == ("plain", False)

    skipping = _adapt("correct", ["correct"])
    assert skipping.as_delivered(by_tutor_step=False).skip_ahead is False

    weak = adapt(
        decision=_decision("stuck"), pitch="beginner", topic="hashing", evidence_log=["stuck"]
    )
    kept = weak.as_delivered(by_tutor_step=False)
    assert len(kept.notes) == 1 and "Starting smaller" in kept.notes[0]  # the pitch is real


def test_the_link_is_found_whatever_hyphen_the_model_typed() -> None:
    from app.graph.nodes import _plain_hyphens  # pyright: ignore[reportPrivateUsage]

    said = "This is the same mix\u2011up as before: the one-sided return."
    assert LINK_PHRASE not in said.lower()
    assert LINK_PHRASE in _plain_hyphens(said.lower())
