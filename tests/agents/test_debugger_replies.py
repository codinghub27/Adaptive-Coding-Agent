"""The debugger answers what was said, reads a bare traceback, and does not
blame the learner for the sandbox having no keyboard. All found by running the
reference conversations live (docs/BEHAVIOR_GAP.md)."""

import json

from app.agents.debugger import read_code
from app.graph.nodes import is_code_line
from app.graph.subgraphs.debug import (
    NEEDS_INPUT_SUMMARY,
    _credit_for_their_fix,  # pyright: ignore[reportPrivateUsage]
    reads_keyboard,
)
from app.response.format import render_execution_line
from app.schemas.execution import Verdict
from app.schemas.input import CodeBlock, StructuredInput
from tests.input.fakes import FakeLLMClient

_REPLY = json.dumps({"bug_explanation": "Yes, that fixes it.", "used": []})


def _system(llm: FakeLLMClient) -> str:
    return llm.chat_calls[0][0].content


async def test_a_reply_to_the_diagnosis_is_answered_not_diagnosed_again() -> None:
    llm = FakeLLMClient(chat_content=_REPLY)
    problem = StructuredInput(
        source="text",
        question="Add 1.",
        code=[CodeBlock(content="def f(x):\n    return x\n", language="python")],
    )
    reading = await read_code(
        problem, static_findings=[], failing_case="f(1)", bug_location=None, llm=llm, reply=True
    )
    assert reading.explanation == "Yes, that fixes it."
    system = _system(llm)
    assert "your WHOLE reply is the" in system
    assert "Do not explain the bug again" in system
    assert "inferred_approach" not in system  # said on the turn before


async def test_a_traceback_without_code_is_read_on_its_own() -> None:
    llm = FakeLLMClient(chat_content=_REPLY)
    problem = StructuredInput(source="text", error="TypeError: string indices must be integers")
    await read_code(
        problem,
        static_findings=[],
        failing_case=None,
        bug_location=None,
        llm=llm,
        failure_established=False,
        traceback_only=True,
    )
    system = _system(llm)
    assert "NOT the code" in system
    assert "ask for ONLY that" in system
    assert "inferred_approach" not in system  # there is no approach to name


async def test_a_question_about_passing_code_is_answered() -> None:
    llm = FakeLLMClient(chat_content=_REPLY)
    problem = StructuredInput(
        source="text",
        question="is there another method?",
        code=[CodeBlock(content="def f(x):\n    return x\n", language="python")],
    )
    await read_code(
        problem,
        static_findings=[],
        failing_case=None,
        bug_location=None,
        llm=llm,
        failure_established=False,
        passed=True,
    )
    system = _system(llm)
    assert "PASSED" in system and "never invent one" in system
    assert "name ONE concrete alternative" in system


def test_a_line_of_code_is_told_from_a_sentence() -> None:
    for code in ("return 1 + max(left, right)", "seen[num] = i", "print(x)", "`left += 1`"):
        assert is_code_line(code), code
    for prose in ("Add 1.", "7?", "a dictionary", "x", "42", "", "I don't know"):
        assert not is_code_line(prose), prose


def test_waiting_for_the_keyboard_is_not_a_bug() -> None:
    assert reads_keyboard('user = input("Enter your name: ")\nprint(user)\n')
    assert not reads_keyboard("def f(input_list):\n    return input_list\n")
    verdict = Verdict(status="inconclusive", category="no_tests", summary=NEEDS_INPUT_SUMMARY)
    line = render_execution_line(verdict, " (your code)")
    assert line.startswith("Execution (your code): not run to the end")
    assert "keyboard input" in line
    assert "✗" not in line


def test_a_proposed_line_that_is_the_verified_fix_is_credited_by_the_sandbox() -> None:
    """A fact, not a model's opinion: their line IS a line of the fix that
    just passed, so the verdict on it is said in fixed words with the count."""
    patched = "def maxDepth(root):\n    if root is None:\n        return 0\n" + (
        "    left = maxDepth(root.left)\n    right = maxDepth(root.right)\n"
        "    return 1 + max(left, right)\n"
    )
    passed = Verdict(status="pass", summary="all 5 case(s) passed", cases_passed=5, cases_total=5)
    failed = Verdict(
        status="fail", category="wrong_answer", summary="x", cases_passed=1, cases_total=5
    )

    credit = _credit_for_their_fix("`return 1 +  max(left, right)`", patched, passed)
    assert credit is not None
    assert credit.startswith("Yes -- that is the fix.")
    assert "passes 5/5" in credit

    assert _credit_for_their_fix("return max(left, right) + 2", patched, passed) is None
    assert _credit_for_their_fix("Add 1.", patched, passed) is None  # words: the tutor answers
    assert _credit_for_their_fix("return 1 + max(left, right)", patched, failed) is None
    assert _credit_for_their_fix("return 1 + max(left, right)", None, passed) is None
