"""The debugger answers what was said, reads a bare traceback, and does not
blame the learner for the sandbox having no keyboard. All found by running the
reference conversations live (docs/BEHAVIOR_GAP.md)."""

import json

from app.agents.debugger import read_code
from app.graph.nodes import is_code_line
from app.graph.subgraphs.debug import NEEDS_INPUT_SUMMARY, reads_keyboard
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
    assert "do NOT explain it again" in system
    assert "say plainly whether it fixes the bug" in system


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
