"""Buggy code, "find the bugs", then "give me the fixed code" (2026-10-11).

The owner's conversation: Container With Most Water with three bugs (a loop
condition that is never true, `-` for `*`, a name that is never defined).

- "find bugs in this code" named one of the three.
- "give bug fixed code of my code" returned the failing case and no code.
- On another run no test cases could be derived; the code "ran cleanly"
  (the loop never executes), nothing was diagnosed, and the ask for the fixed
  code returned the learner's own broken code as "your code as you shared it".

Running the learner's code first is intended: that run is the evidence of
what is wrong with it. What was wrong is what happened after it.
"""

from typing import Final

from app.agents.debugger import (
    _CODE_TOKENS,  # pyright: ignore[reportPrivateUsage]
    _EXPLAIN_SYSTEM,  # pyright: ignore[reportPrivateUsage]
    _PATCH_SYSTEM,  # pyright: ignore[reportPrivateUsage]
    _READ_SYSTEM,  # pyright: ignore[reportPrivateUsage]
    _READING_TOKENS,  # pyright: ignore[reportPrivateUsage]
    patch_code,
    read_code,
    static_analysis,
)
from app.graph.subgraphs.debug import (
    MAX_PATCH_ATTEMPTS,
    _fix_asked_for_a_read_bug,  # pyright: ignore[reportPrivateUsage]
)
from app.response.generate import generate_response
from app.schemas.agent_results import DebugResult
from app.schemas.execution import Verdict
from app.schemas.input import CodeBlock, StructuredInput
from app.schemas.plan import TeachingPlan
from tests.input.fakes import FakeLLMClient

_MAX_AREA: Final = (
    "def maxArea(height):\n"
    "    left = 0\n"
    "    right = len(height) - 1\n"
    "    max_water = 0\n"
    "    while left > right:\n"
    "        width = right - left\n"
    "        current_height = min(height[left], height[right])\n"
    "        current_water = width - current_height\n"
    "        max_water = max(water, current_water)\n"
    "        if height[left] < height[right]:\n"
    "            left += 1\n"
    "        else:\n"
    "            right -= 1\n"
    "    return max_water\n"
)
_FIXED: Final = (
    _MAX_AREA.replace("left > right", "left < right")
    .replace("width - current_height", "width * current_height")
    .replace("max(water,", "max(max_water,")
)


def _problem() -> StructuredInput:
    return StructuredInput(
        source="text",
        question="find bugs in this code.",
        code=[CodeBlock(content=_MAX_AREA, language="python")],
    )


def _plan(level: str) -> TeachingPlan:
    return TeachingPlan.model_validate(
        {
            "difficulty": "medium",
            "assistance_level": level,
            "solution_strategy": "socratic_hints",
            "topic": "two_pointers",
            "skill_level": 0.5,
        }
    )


# --- every bug is looked for ----------------------------------------------------------


def test_a_name_that_is_never_defined_is_found_without_running_anything() -> None:
    """`water` sits behind a loop that never runs: no test reaches it."""
    findings = [f for f in static_analysis(_MAX_AREA) if "never defined" in f.message]
    assert [(f.lineno, f.severity) for f in findings] == [(9, "major")]
    assert "'water'" in findings[0].message


def test_names_that_do_exist_are_never_reported() -> None:
    code = (
        "import math\nfrom collections import deque as dq\n\n"
        "def f(nums, *rest, key=None, **more):\n"
        "    total = sum(x for x in nums)\n"
        "    for i, n in enumerate(nums):\n"
        "        with open('f') as handle:\n"
        "            pass\n"
        "    try:\n        pass\n    except ValueError as err:\n        print(err, handle, i, n)\n"
        "    return math.floor(total), dq(), rest, key, more, later()\n\n"
        "def later():\n    return [y for y in range(3)]\n"
    )
    assert not [f for f in static_analysis(code) if "never defined" in f.message]


def test_the_reader_is_asked_for_every_bug_not_the_first() -> None:
    for system in (_EXPLAIN_SYSTEM, _READ_SYSTEM):
        assert "EVERY definite bug" in system
        assert "numbered list" in system
    assert "the failing case shows only the first thing that goes wrong" in _EXPLAIN_SYSTEM
    assert '"found_bug"' in _READ_SYSTEM


def test_the_patch_is_asked_to_fix_every_bug() -> None:
    assert "fixes EVERY bug in it" in _PATCH_SYSTEM
    assert MAX_PATCH_ATTEMPTS >= 3


async def test_the_calls_have_room_for_the_model_to_think_and_then_write() -> None:
    """At 500 and 1200 the replies came back empty: no diagnosis, no code."""
    assert _READING_TOKENS >= 1500 and _CODE_TOKENS >= 3000
    llm = FakeLLMClient(chat_content='{"bug_explanation": "1. the loop", "found_bug": true}')
    reading = await read_code(
        _problem(),
        static_findings=[],
        failing_case=None,
        bug_location=None,
        llm=llm,
        failure_established=False,
    )
    assert reading.found_bug is True
    assert llm.chat_kwargs[-1]["max_tokens"] == _READING_TOKENS

    patching = FakeLLMClient(chat_content='{"patched_code": "x = 1"}')
    await patch_code(
        _problem(),
        static_findings=[],
        failing_case=None,
        bug_location=None,
        bug_explanation="1. the loop",
        llm=patching,
    )
    assert patching.chat_kwargs[-1]["max_tokens"] == _CODE_TOKENS


async def test_a_reading_that_found_nothing_does_not_say_it_found_a_bug() -> None:
    llm = FakeLLMClient(chat_content='{"bug_explanation": "I found no bug.", "found_bug": false}')
    reading = await read_code(
        _problem(),
        static_findings=[],
        failing_case=None,
        bug_location=None,
        llm=llm,
        failure_established=False,
    )
    assert reading.found_bug is False


# --- the fixed code is given when it is asked for ------------------------------------


def test_a_bug_that_was_read_not_proven_is_still_fixed_when_the_fix_is_asked_for() -> None:
    asked = {"wants_code": True, "reading_found_bug": True, "attempts": 0}
    assert _fix_asked_for_a_read_bug(asked) is True  # pyright: ignore[reportArgumentType]
    # Not asked for, nothing found, or already tried once: not written.
    for change in ({"wants_code": False}, {"reading_found_bug": False}, {"attempts": 1}):
        assert _fix_asked_for_a_read_bug({**asked, **change}) is False  # pyright: ignore[reportArgumentType]


def _result(final: Verdict, initial: Verdict) -> DebugResult:
    return DebugResult(
        bug_explanation="1. `while left > right:` is never true.",
        patched_code=_FIXED,
        initial_verdict=initial,
        final_verdict=final,
        fixed=final.status == "pass",
    )


_FAILED: Final = Verdict(
    status="fail",
    category="wrong_answer",
    summary="1/6 cases passed",
    cases_passed=1,
    cases_total=6,
)
_NO_TESTS: Final = Verdict(status="inconclusive", category="no_tests", summary="no test cases")


def test_a_fix_that_did_not_pass_is_shown_labelled_never_withheld() -> None:
    still = Verdict(
        status="fail",
        category="wrong_answer",
        summary="5/6 cases passed",
        cases_passed=5,
        cases_total=6,
    )
    shown = generate_response(result=_result(still, _FAILED), plan=_plan("full")).text
    assert "width * current_height" in shown
    assert "Not verified in sandbox" in shown and "5/6 cases passed" in shown
    assert "A fix was found and verified" not in shown


def test_a_fix_for_a_bug_that_was_only_read_is_executed_not_verified() -> None:
    shown = generate_response(result=_result(_NO_TESTS, _NO_TESTS), plan=_plan("full")).text
    assert "width * current_height" in shown
    assert "executed, not verified" in shown
    assert "Not verified in sandbox." not in shown  # it did not fail: it could not be checked


def test_a_verified_fix_is_still_said_to_be_verified_and_nothing_else_is() -> None:
    passed = Verdict(status="pass", summary="6/6 cases passed", cases_passed=6, cases_total=6)
    shown = generate_response(result=_result(passed, _FAILED), plan=_plan("full")).text
    assert "A fix was found and verified in the sandbox" in shown
    assert "Not verified" not in shown


def test_no_fix_is_shown_to_a_learner_who_did_not_ask_for_it() -> None:
    for final in (_FAILED, _NO_TESTS):
        hinted = generate_response(result=_result(final, _FAILED), plan=_plan("hint")).text
        assert "width * current_height" not in hinted


def test_when_no_fix_could_be_written_the_reply_says_so() -> None:
    """Seen live with the provider rate-limited: the learner's broken code came
    back as "your code as you shared it" and nothing said it was not a fix."""
    none = DebugResult(
        static_findings=[f for f in static_analysis(_MAX_AREA) if "never defined" in f.message],
        presented_code=_MAX_AREA,
        presented_label="This is your code as you shared it (made runnable).",
        initial_verdict=_NO_TESTS,
    )
    shown = generate_response(result=none, plan=_plan("full")).text
    assert "I could not write one this time" in shown and "Nothing above is a fix" in shown

    failed = DebugResult(bug_explanation="1. the loop.", initial_verdict=_FAILED)
    assert (
        "I could not write one this time"
        in generate_response(result=failed, plan=_plan("full")).text
    )
    # Not asked for, or nothing known to be wrong: not said.
    assert "could not write" not in generate_response(result=failed, plan=_plan("hint")).text
    clean = DebugResult(presented_code="x = 1", initial_verdict=_NO_TESTS)
    assert "could not write" not in generate_response(result=clean, plan=_plan("full")).text
