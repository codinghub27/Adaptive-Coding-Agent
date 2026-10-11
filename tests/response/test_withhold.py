"""'Don't give me the code yet' is kept in code (target behaviour 6 and 9).

Audit section 14: the patch and the code block were withheld, a corrected
line quoted inside a sentence was not, and one live run of two stated it.
"""

from typing import Final

from app.response.withhold import WITHHELD, redact_fix

_MAX_DEPTH: Final = (
    "def maxDepth(root):\n"
    "    if root is None:\n"
    "        return 0\n"
    "    left = maxDepth(root.left)\n"
    "    right = maxDepth(root.right)\n"
    "    return max(left, right)\n"
)
_MAX_DEPTH_FIXED: Final = _MAX_DEPTH.replace(
    "return max(left, right)", "return 1 + max(left, right)"
)

_TRAP: Final = (
    "def trap(height):\n"
    "    left, right = 0, len(height) - 1\n"
    "    water = 0\n"
    "    while left < right:\n"
    "        if height[left] < height[right]:\n"
    "            left -= 1\n"
    "        else:\n"
    "            right += 1\n"
    "    return water\n"
)
_TRAP_FIXED: Final = _TRAP.replace("left -= 1", "left += 1").replace("right += 1", "right -= 1")


def test_the_diagnosis_is_left_exactly_as_it_was() -> None:
    said = (
        "Your recursive structure is mostly correct. You find the deeper child with "
        "`max(left, right)`, but you are not counting the current node. For a single node "
        "both child depths are `0`, yet `maxDepth(root)` should be `1`. What does the "
        "current node contribute?"
    )
    assert redact_fix(said, _MAX_DEPTH, _MAX_DEPTH_FIXED) == said
    assert redact_fix(said, _MAX_DEPTH, None) == said


def test_the_corrected_line_is_taken_out_of_a_sentence() -> None:
    for stated in (
        "Change the last line to `return 1 + max(left, right)` and it works.",
        "Change the last line to return 1 + max(left, right) and it works.",
        "The value you want is `max(left, right) + 1`.",
        "You should return 1+max( left , right ) instead.",
    ):
        out = redact_fix(stated, _MAX_DEPTH, _MAX_DEPTH_FIXED)
        assert WITHHELD in out, stated
        assert "1 + max" not in out and "+ 1" not in out and "1+max" not in out, out


def test_it_holds_without_a_patch_to_compare_with() -> None:
    """Under 'no code yet' the debugger writes no patch, so the corrected
    expression is recognised as the learner's own expression, extended."""
    for stated in (
        "Return `1 + max(left, right)` instead.",
        "It should be max(left, right) + 1.",
        "Write `return 1 + max(left, right)`.",
    ):
        out = redact_fix(stated, _MAX_DEPTH, None)
        assert WITHHELD in out, stated
        assert "1 + max" not in out and "+ 1" not in out, out


def test_an_augmented_assignment_they_did_not_write_is_withheld() -> None:
    said = (
        "You move the pointers apart: `left -= 1` should be left += 1, and right += 1 is wrong too."
    )
    out = redact_fix(said, _TRAP, None)
    assert "left -= 1" in out and "right += 1" in out  # their own lines: the diagnosis
    assert "left += 1" not in out
    assert out.count(WITHHELD) == 1


def test_a_fenced_block_that_is_not_their_code_is_withheld() -> None:
    said = f"Here is the fix:\n\n```python\n{_MAX_DEPTH_FIXED}```\n\nTry it."
    out = redact_fix(said, _MAX_DEPTH, _MAX_DEPTH_FIXED)
    assert "```" not in out and WITHHELD in out and "Try it." in out
    quoted = "Your last line:\n\n```python\n    return max(left, right)\n```"
    assert redact_fix(quoted, _MAX_DEPTH, _MAX_DEPTH_FIXED) == quoted


def test_values_states_and_calls_are_how_a_diagnosis_talks() -> None:
    said = (
        "On `height = [2, 0, 2]` the loop starts with `left = 0` and `right = 2`. "
        "`trap([2, 0, 2])` should give `2`, but `left` goes to `-1` when i + 1 is reached."
    )
    assert redact_fix(said, _TRAP, _TRAP_FIXED) == said


def test_nothing_to_compare_with_changes_nothing() -> None:
    assert redact_fix("Return `a + b`.", None, None) == "Return `a + b`."
    assert redact_fix("", _MAX_DEPTH, _MAX_DEPTH_FIXED) == ""


async def test_the_reply_of_a_no_code_yet_turn_goes_through_the_guard() -> None:
    """The wiring: `final_response` on a debug turn the learner capped."""
    from app.graph.nodes import final_response
    from app.graph.state import AgentState, RawInput
    from app.schemas.agent_results import DebugResult
    from app.schemas.decision import TurnDecision
    from app.schemas.input import CodeBlock, StructuredInput
    from app.schemas.plan import TeachingPlan
    from tests.graph.test_phase7_wiring import _runtime  # pyright: ignore[reportPrivateUsage]

    def state(withhold: bool) -> AgentState:
        return AgentState(
            input=RawInput(text="Explain it, but don't give me the final code yet."),
            structured_input=StructuredInput(
                source="text",
                question="Explain it, but don't give me the final code yet.",
                code=[CodeBlock(content=_MAX_DEPTH, language="python")],
            ),
            route="debug",
            decision=TurnDecision.model_validate(
                {"subject": "new", "move": "debug", "withhold": withhold}
            ),
            plan=TeachingPlan(
                difficulty="easy",
                assistance_level="concept",
                solution_strategy="socratic_hints",
                topic="trees",
                skill_level=0.5,
            ),
            agent_result=DebugResult(
                bug_explanation=(
                    "You take the deeper child but never count the current node, so a single "
                    "node gives 0. Change the last line to `return 1 + max(left, right)`."
                ),
            ),
        )

    capped = await final_response(state(withhold=True), _runtime())
    said = str(capped.get("response"))
    assert "never count the current node" in said
    assert "1 + max" not in said and WITHHELD in said

    free = await final_response(state(withhold=False), _runtime())
    assert "1 + max" in str(free.get("response"))


def test_evidence_in_a_fenced_block_is_kept() -> None:
    """Code review, 2026-10-11: a traceback, the sandbox's output and the
    learner's own code with a marker comment were all replaced."""
    traceback = "It stops here:\n\n```\nTraceback (most recent call last):\nKeyError: 3\n```"
    assert redact_fix(traceback, _MAX_DEPTH, _MAX_DEPTH_FIXED) == traceback
    output = "It printed:\n\n```\n0\n```\n\nbut a single node should give 1."
    assert redact_fix(output, _MAX_DEPTH, None) == output
    marked = "Look here:\n\n```python\n    return max(left, right)  # <- this line\n```"
    assert redact_fix(marked, _MAX_DEPTH, _MAX_DEPTH_FIXED) == marked
