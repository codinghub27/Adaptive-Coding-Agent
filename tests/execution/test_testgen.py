"""Tests for `app.execution.testgen` (Phase 07 debug/review P4 defect fix).

No LLM, no sandbox: `extract_test_suite` is pure, deterministic parsing over
`StructuredInput`.
"""

import inspect

import pytest

from app.execution import testgen
from app.execution.testgen import extract_test_suite
from app.schemas.execution import MAX_TEST_CASES, TestSuite
from app.schemas.input import CodeBlock, StructuredInput

_TWO_SUM_CODE = "def two_sum(nums, target):\n    return [0, 1]\n"

_TWO_SUM_STATEMENT = """
Given an array of integers `nums` and an integer `target`, return indices of
the two numbers such that they add up to `target`.

Example 1:
**Input:** nums = [2,7,11,15], target = 9
**Output:** [0,1]
"""


def _problem(statement: str | None, code: str | None) -> StructuredInput:
    return StructuredInput(
        source="text",
        problem=statement,
        code=[CodeBlock(content=code)] if code else [],
    )


def test_leetcode_style_statement_yields_suite_with_kwargs() -> None:
    suite = extract_test_suite(_problem(_TWO_SUM_STATEMENT, _TWO_SUM_CODE))

    assert suite is not None
    assert suite.entrypoint == "two_sum"
    assert len(suite.cases) == 1
    case = suite.cases[0]
    assert case.kwargs == {"nums": [2, 7, 11, 15], "target": 9}
    assert case.expected == [0, 1]
    assert case.args == []


def test_multiple_examples_yield_uniquely_named_cases() -> None:
    statement = """
Example 1:
Input: nums = [1,2], target = 3
Output: [0,1]

Example 2:
Input: nums = [3,4,5], target = 9
Output: [1,2]
"""
    suite = extract_test_suite(_problem(statement, _TWO_SUM_CODE))

    assert suite is not None
    assert [case.name for case in suite.cases] == ["example_1", "example_2"]
    assert suite.cases[0].kwargs == {"nums": [1, 2], "target": 3}
    assert suite.cases[0].expected == [0, 1]
    assert suite.cases[1].kwargs == {"nums": [3, 4, 5], "target": 9}
    assert suite.cases[1].expected == [1, 2]


def test_examples_are_capped_at_max_test_cases() -> None:
    blocks = [
        f"Example {i}:\nInput: nums = [{i}], target = {i}\nOutput: {i}\n"
        for i in range(1, MAX_TEST_CASES + 20)
    ]
    statement = "\n".join(blocks)
    suite = extract_test_suite(_problem(statement, _TWO_SUM_CODE))

    assert suite is not None
    assert len(suite.cases) == MAX_TEST_CASES


def test_bare_input_output_form_yields_args() -> None:
    statement = "Example 1:\nInput: 5\nOutput: 25\n"
    code = "def square(x):\n    return x * x\n"
    suite = extract_test_suite(_problem(statement, code))

    assert suite is not None
    assert suite.entrypoint == "square"
    assert suite.cases[0].args == [5]
    assert suite.cases[0].kwargs == {}
    assert suite.cases[0].expected == 25


@pytest.mark.parametrize(
    "statement",
    [
        pytest.param("Input: nums = [2,7,11,15], target = 9\nOutput: [0,1]", id="plain"),
        pytest.param(
            "**Input:** nums = [2,7,11,15], target = 9\n**Output:** [0,1]", id="bold-colon-inside"
        ),
        pytest.param(
            "**Input**: nums = [2,7,11,15], target = 9\n**Output**: [0,1]",
            id="bold-colon-outside",
        ),
        pytest.param(
            "Input: `nums = [2,7,11,15], target = 9`\nOutput: `[0,1]`", id="backticked-value"
        ),
        pytest.param(
            "**Input:** `nums = [2,7,11,15], target = 9`\n**Output:** `[0,1]`", id="bold-backtick"
        ),
        pytest.param(
            "Input: `nums = [2,7,11,15]`, `target = 9`\nOutput: [0,1]", id="per-part-backticks"
        ),
    ],
)
def test_markdown_decorated_statements_all_extract(statement: str) -> None:
    """A statement pasted from a problem site carries markdown, so these are the
    common shapes, not edge cases. Each must produce the same suite as the plain
    form: when extraction silently yields nothing, the debugger falls back to
    "could not verify" instead of verifying a real fix.
    """
    suite = extract_test_suite(_problem(statement, _TWO_SUM_CODE))

    assert suite is not None, "markdown-decorated statement extracted nothing"
    assert suite.entrypoint == "two_sum"
    assert len(suite.cases) == 1
    assert suite.cases[0].kwargs == {"nums": [2, 7, 11, 15], "target": 9}
    assert suite.cases[0].expected == [0, 1]


def test_returns_none_for_no_code() -> None:
    assert extract_test_suite(_problem(_TWO_SUM_STATEMENT, None)) is None


def test_returns_none_for_unparseable_code() -> None:
    assert extract_test_suite(_problem(_TWO_SUM_STATEMENT, "def two_sum(nums, target:\n")) is None


def test_returns_none_for_code_with_no_top_level_function() -> None:
    assert extract_test_suite(_problem(_TWO_SUM_STATEMENT, "x = 1\ny = 2\n")) is None


def test_returns_none_for_statement_with_no_examples() -> None:
    statement = "Given an array, return the two indices that sum to a target."
    assert extract_test_suite(_problem(statement, _TWO_SUM_CODE)) is None


def test_returns_none_when_values_do_not_literal_eval() -> None:
    statement = "Example 1:\nInput: nums = do_something(), target = 9\nOutput: [0,1]\n"
    assert extract_test_suite(_problem(statement, _TWO_SUM_CODE)) is None


def test_returns_none_for_missing_problem() -> None:
    assert extract_test_suite(None) is None


def test_hostile_statement_never_raises() -> None:
    hostile_statements = [
        "Input: " + "(" * 5000 + "\nOutput: 1\n",
        "Example 1:\nInput: " + "[" * 2000 + "1" + "]" * 2000 + "\nOutput: 1\n",
        "\x00\x01\x02 not python at all {{{{{{{ }}}}",
        "Input:\nOutput:\n" * 500,
    ]
    for statement in hostile_statements:
        result = extract_test_suite(_problem(statement, _TWO_SUM_CODE))
        assert result is None or isinstance(result, TestSuite)


def test_module_never_calls_eval_or_exec() -> None:
    source = inspect.getsource(testgen)
    assert "eval(" not in source.replace("literal_eval(", "")
    assert "exec(" not in source
