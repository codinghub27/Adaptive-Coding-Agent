"""A request to be shown code and have it explained is answered (2026-10-11).

Three real conversations, each with green unit tests at the time:

- "Explain how to solve this problem and explain with code line by line of how
  it works", on a problem sent as an image: a hint, then a generic second hint.
- "explain the code of 3sum line by line of how it works": "I wasn't able to
  put together a full response", and on another run "I can't tell what that
  refers to".
- "give proper python code": the right code under "Time: O(n^2)".

What is pinned here is the decision, the prompt and the text built in code.
Whether the model then writes a good walk is read live.
"""

from typing import Final

from app.agents.concept import (
    _Output,  # pyright: ignore[reportPrivateUsage]
    _said,  # pyright: ignore[reportPrivateUsage]
)
from app.agents.planner import wants_the_code
from app.execution.synth import (
    _REFERENCE_SYSTEM,  # pyright: ignore[reportPrivateUsage]
    _SynthOutput,  # pyright: ignore[reportPrivateUsage]
    _walkthrough,  # pyright: ignore[reportPrivateUsage]
)
from app.graph.nodes import _decide  # pyright: ignore[reportPrivateUsage]
from app.graph.state import AgentState, RawInput
from app.response.generate import generate_response
from app.schemas.agent_results import CodeStep, DSAResult, HintLevel, HintResult
from app.schemas.input import CodeBlock, StructuredInput
from app.schemas.intent import Intent, IntentResult
from app.schemas.plan import TeachingPlan

_ASK: Final = (
    "Explain how to solve this problem and explain with code line by line of how it works."
)
_STATEMENT: Final = (
    "Next Permutation\n\nGiven an array of integers nums, find the next permutation."
)
_CODE: Final = "def solve(nums):\n    i = len(nums) - 2\n    nums.reverse()\n    return nums\n"


def _read(intent: Intent, *, confidence: float = 0.9, source: str = "llm") -> IntentResult:
    return IntentResult.model_validate(
        {"intent": intent, "confidence": confidence, "source": source}
    )


def _state(
    question: str, intent: IntentResult, *, problem: str | None = None, code: str = ""
) -> AgentState:
    blocks = [CodeBlock(content=code, language="python")] if code else []
    return AgentState(
        input=RawInput(text=question),
        structured_input=StructuredInput(
            source="text", question=question, problem=problem, code=blocks
        ),
        intent=intent,
    )


# --- the decision ------------------------------------------------------------------


def test_explaining_code_nobody_has_on_a_stated_problem_is_an_ask_for_that_code() -> None:
    update = _decide(_state(_ASK, _read(Intent.CODE_EXPLAIN), problem=_STATEMENT))

    settled = update.get("intent")
    assert settled is not None and settled.intent is Intent.DSA_SOLVE
    assert settled.asks_for_code is True
    assert update.get("route") == "dsa"
    decision = update.get("decision")
    assert decision is not None and decision.wants_code is True


def test_explaining_code_nobody_has_with_only_a_name_is_explained_from_knowledge() -> None:
    question = "explain the code of 3sum line by line of how it works."
    update = _decide(_state(question, _read(Intent.CODE_EXPLAIN)))

    settled = update.get("intent")
    assert settled is not None and settled.intent is Intent.CONCEPT_EXPLANATION
    assert update.get("route") == "explain"


def test_code_the_learner_did_bring_is_still_theirs_to_have_explained() -> None:
    update = _decide(_state("explain this line by line", _read(Intent.CODE_EXPLAIN), code=_CODE))

    settled = update.get("intent")
    assert settled is None or settled.intent is not Intent.CONCEPT_EXPLANATION
    assert update.get("route") in ("explain", "debug")


def test_an_unsure_reading_of_explain_this_code_is_still_asked_about() -> None:
    unsure = _read(Intent.CODE_EXPLAIN, confidence=0.3)
    assert _decide(_state("hmm can you look at this thing", unsure)).get("route") == "clarify"


def test_an_unsure_opening_question_is_explained_not_asked_what_it_refers_to() -> None:
    """Nothing has been said yet, so there is nothing it could refer to."""
    question = "explain the code of 3sum line by line of how it works."
    unsure = _read(Intent.CONCEPT_EXPLANATION, confidence=0.3)
    assert _decide(_state(question, unsure)).get("route") == "explain"

    # The keyword fallback calls anything it cannot place "a concept": no model
    # read that turn, and it is still asked about.
    fallback = _read(Intent.CONCEPT_EXPLANATION, confidence=0.2, source="fallback")
    assert _decide(_state(question, fallback)).get("route") == "clarify"


def test_a_message_that_names_the_code_may_be_longer_than_a_bare_demand() -> None:
    assert len(_ASK.split()) > 14
    asked = IntentResult.model_validate(
        {"intent": Intent.DSA_SOLVE, "confidence": 0.9, "source": "llm", "asks_for_code": True}
    )
    assert wants_the_code(asked, _ASK) is True
    # Still not on the model's word alone when the code is never named.
    walk = "can you please walk me through how I should think about and approach this one today"
    assert wants_the_code(asked, walk) is False


# --- the walk-through ---------------------------------------------------------------


def test_the_code_writer_is_asked_for_a_walk_only_when_one_was_asked_for() -> None:
    assert '"walkthrough"' in _REFERENCE_SYSTEM
    assert "copied EXACTLY from reference_solution" in _REFERENCE_SYSTEM
    assert 'leave "walkthrough" out' in _REFERENCE_SYSTEM


def _proposed(*steps: tuple[str, str]) -> _SynthOutput:
    return _SynthOutput.model_validate(
        {
            "reference_solution": _CODE,
            "walkthrough": [{"code": code, "says": says} for code, says in steps],
        }
    )


def test_a_step_is_kept_only_when_its_lines_are_in_the_code_shown() -> None:
    kept = _walkthrough(
        _proposed(
            ("i = len(nums) - 2", "Start at the second-last position."),
            ("    nums.reverse()   ", "Put the tail in ascending order."),
            ("j = i + 1", "A line that is not in the code."),
            ("return nums", ""),
        ),
        _CODE,
    )
    assert kept == (
        ("i = len(nums) - 2", "Start at the second-last position."),
        ("nums.reverse()", "Put the tail in ascending order."),
    )


def test_one_sound_step_is_not_a_walk_through() -> None:
    assert _walkthrough(_proposed(("return nums", "Hand it back."), ("x = 1", "No.")), _CODE) == ()
    assert _walkthrough(_proposed(), _CODE) == ()


def _revealed(steps: list[CodeStep]) -> DSAResult:
    hint = HintResult(
        level=HintLevel.L6_FULL,
        text="Here is the full solution.",
        is_terminal=True,
        reveals_code=True,
        ceiling=HintLevel.L6_FULL,
    )
    return DSAResult(topic="arrays", code=_CODE, hint=hint, walkthrough=steps)


def _plan(level: str) -> TeachingPlan:
    return TeachingPlan.model_validate(
        {
            "difficulty": "medium",
            "assistance_level": level,
            "solution_strategy": "socratic_hints",
            "topic": "arrays",
            "skill_level": 0.5,
        }
    )


def test_the_walk_is_shown_under_the_code_and_never_without_it() -> None:
    steps = [
        CodeStep(code="i = len(nums) - 2", says="Start at the second-last position."),
        CodeStep(code="nums.reverse()\nreturn nums", says="Reverse, then hand it back."),
    ]
    shown = generate_response(result=_revealed(steps), plan=_plan("full")).text
    assert "**Line by line:**" in shown
    assert "1. `i = len(nums) - 2`" in shown and "Start at the second-last position." in shown
    assert "2. `nums.reverse() / return nums`" in shown
    assert shown.index("```python") < shown.index("**Line by line:**")

    assert "Line by line" not in generate_response(result=_revealed([]), plan=_plan("full")).text
    hinted = generate_response(result=_revealed(steps), plan=_plan("hint")).text
    assert "Line by line" not in hinted and "len(nums) - 2" not in hinted


# --- one closing question, and a plan that agrees with itself ------------------------


def test_an_answer_does_not_ask_its_own_question_before_the_closing_one() -> None:
    parsed = _Output.model_validate(
        {
            "answer": (
                "Bitwise AND keeps a bit only where both have it.\n\n**Check:** What is 13 & 4?"
            ),
            "check": "What is bit 3 of 13 & 4?",
        }
    )
    assert _said(parsed) == "Bitwise AND keeps a bit only where both have it."

    ruled = _Output.model_validate(
        {"answer": "AND keeps shared bits.\n\n---\n**Check:** What is 13 & 4?", "check": "Q?"}
    )
    assert _said(ruled) == "AND keeps shared bits."

    # With no closing question to follow, the answer's own one is the question.
    alone = _Output.model_validate({"answer": "AND keeps shared bits.\n\nWhat is 13 & 4?"})
    assert _said(alone).endswith("What is 13 & 4?")


def test_a_plan_is_titled_with_the_number_of_weeks_it_lists() -> None:
    plan = (
        "## 12-Week DSA Mastery Plan\n\n**Week 1-2:** arrays\n\n**Week 3-4:** stacks\n\n"
        "**Week 17-18:** tries"
    )
    said = _said(_Output.model_validate({"answer": plan}))
    assert "18-Week DSA Mastery Plan" in said and "12-Week" not in said

    right = "## 4-Week Plan\n\n**Week 1:** a\n\n**Week 2:** b\n\n**Week 3-4:** c"
    assert _said(_Output.model_validate({"answer": right})) == right


def test_the_explainer_is_told_what_a_line_by_line_request_looks_like() -> None:
    from app.agents.concept import (
        _OPEN_SYSTEM,  # pyright: ignore[reportPrivateUsage]
        _SYSTEM,  # pyright: ignore[reportPrivateUsage]
    )

    for system in (_SYSTEM, _OPEN_SYSTEM):
        assert "that request replaces the default shape of your answer" in system
        assert "quotes one line (or a few adjacent lines) of it exactly" in system
