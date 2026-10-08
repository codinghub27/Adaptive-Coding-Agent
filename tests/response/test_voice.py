"""A reply is spoken, not assembled under headers (docs/BEHAVIOR_GAP.md, RC-3)."""

from app.response.format import SECTION_TITLES
from app.response.generate import generate_response
from app.response.voice import speak, speak_section
from app.schemas.agent_results import DebugResult
from app.schemas.execution import Verdict
from app.schemas.plan import TeachingPlan
from app.schemas.response import ResponseSection, ResponseSectionKind


def _section(kind: ResponseSectionKind, body: str, title: str | None = None) -> ResponseSection:
    return ResponseSection(kind=kind, title=title or SECTION_TITLES[kind], body=body)


def test_the_tutors_own_sentences_and_the_verdict_lines_are_said_as_they_are() -> None:
    line = "Execution: ✓ passed 4/4 test cases in the sandbox"
    for kind in ("next_hint", "explanation", "bug_explanation", "check_question", "execution"):
        assert speak_section(_section(kind, line)) == line


def test_no_section_is_spoken_under_a_markdown_header() -> None:
    for kind in SECTION_TITLES:
        spoken = speak_section(_section(kind, "one line"))
        assert not spoken.startswith("#"), kind
        assert "one line" in spoken


def test_scanned_parts_get_a_short_inline_label() -> None:
    assert speak_section(_section("complexity", "Time: O(n)")) == "**Cost:** Time: O(n)"
    assert speak_section(_section("key_insight", "a\nb")) == "**The idea**\n\na\nb"
    assert speak_section(_section("patch", "```python\nx = 1\n```")).startswith("Here is the fix:")
    mine = _section("code", "```python\nx = 1\n```", title="Your code")
    assert speak_section(mine).startswith("Here is your code:")


def test_speaking_keeps_every_section_in_order_and_every_body() -> None:
    sections = [
        _section("inferred_approach", "You are using two pointers."),
        _section("bug_explanation", "`seen[num]` should be `seen[complement]`."),
        _section("check_question", "Which key did the `if` just prove exists?"),
    ]
    spoken, text = speak(sections)
    assert [s.kind for s in spoken] == [s.kind for s in sections]
    assert [s.body for s in spoken] == [s.body for s in sections]
    assert all(s.spoken for s in spoken)
    assert text.endswith("Which key did the `if` just prove exists?")
    assert "##" not in text


def test_a_passing_debug_turn_is_not_a_log() -> None:
    """Measured live: "passed 6/6 ... The sandbox confirmed this passes (6/6
    cases). all 6 case(s) passed" under two headers."""
    passed = Verdict(status="pass", summary="all 6 case(s) passed", cases_passed=6, cases_total=6)
    result = DebugResult(
        inferred_approach="You are using two pointers.",
        bug_explanation="Another way is a prefix-max and suffix-max array: O(n) space.",
        initial_verdict=passed,
        final_verdict=passed,
        fixed=True,
    )
    plan = TeachingPlan(
        difficulty="medium",
        assistance_level="concept",
        solution_strategy="guided_debugging",
        skill_level=0.5,
    )
    generated = generate_response(result=result, plan=plan, verification=passed)
    assert "##" not in generated.text
    assert "confirmed this passes" not in generated.text  # the Execution line says it once
    assert "Not verified by running your code" not in generated.text
    assert generated.text.startswith("You are using two pointers.")
    assert "prefix-max" in generated.text


def test_code_is_fenced_as_python() -> None:
    failed = Verdict(
        status="fail", category="wrong_answer", summary="1 of 2", cases_passed=1, cases_total=2
    )
    passed = Verdict(status="pass", summary="all 2 case(s) passed", cases_passed=2, cases_total=2)
    result = DebugResult(
        bug_explanation="Off by one.",
        patched_code="def f():\n    return 1\n",
        initial_verdict=failed,
        final_verdict=passed,
        fixed=True,
    )
    plan = TeachingPlan(
        difficulty="medium",
        assistance_level="full",
        solution_strategy="guided_debugging",
        skill_level=0.5,
    )
    generated = generate_response(result=result, plan=plan, verification=passed)
    assert "```python\ndef f():" in generated.text
    assert generated.reveals_code is True
