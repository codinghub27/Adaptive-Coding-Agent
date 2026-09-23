"""Tests for `app.input.normalize` — pure, deterministic text normalization."""

import time

import pytest

from app.input.normalize import MAX_TEXT_CHARS, guess_language, merge_inputs, normalize_text
from app.schemas.input import CodeBlock, StructuredInput

#: Wall-clock ceiling for the adversarial-input performance tests. Generous
#: relative to the < 0.5s target so the suite isn't flaky on a loaded CI box,
#: while still catching an accidental reintroduction of quadratic behaviour
#: (which blows this budget by orders of magnitude, not a little).
_PERF_BUDGET_SECONDS = 0.5


def test_unfenced_code_and_bare_exception_no_question() -> None:
    text = (
        "def find_target(nums, target):\n"
        "    for i, n in enumerate(nums):\n"
        "        if n == target:\n"
        "            return i\n"
        "\n"
        "IndexError: list index out of range\n"
    )
    result = normalize_text(text)

    assert len(result.code) == 1
    assert result.code[0].language == "python"
    assert "def find_target(nums, target):" in result.code[0].content
    assert result.error == "IndexError: list index out of range"
    assert result.question is None
    assert result.problem is None


def test_fenced_code_traceback_and_question() -> None:
    text = (
        "```python\n"
        "def divide(a, b):\n"
        "    return a / b\n"
        "```\n"
        "\n"
        "Traceback (most recent call last):\n"
        '  File "main.py", line 5, in <module>\n'
        "    divide(1, 0)\n"
        '  File "main.py", line 2, in divide\n'
        "    return a / b\n"
        "ZeroDivisionError: division by zero\n"
        "\n"
        "why does this crash?\n"
    )
    result = normalize_text(text)

    assert len(result.code) == 1
    assert result.code[0].language == "python"
    assert result.code[0].content == "def divide(a, b):\n    return a / b"
    assert result.error is not None
    assert result.error.startswith("Traceback (most recent call last):")
    assert "ZeroDivisionError: division by zero" in result.error
    assert "Traceback" not in (result.code[0].content or "")
    assert result.question == "why does this crash?"


def test_trailing_call_statement_stays_in_code_block() -> None:
    """A bare call statement at the end of pasted code (no trailing `;`,
    no assignment, no keyword prefix) must join the preceding code block,
    not get misread as a prose question."""
    text = (
        "def get_item(items, idx):\n"
        "    return items[idx]\n"
        "\n"
        "items = [1, 2, 3]\n"
        "print(get_item(items, 5))\n"
        "\n"
        "Traceback (most recent call last):\n"
        '  File "main.py", line 5, in <module>\n'
        "    print(get_item(items, 5))\n"
        "IndexError: list index out of range\n"
    )
    result = normalize_text(text)

    assert result.question is None
    assert len(result.code) == 1
    assert "print(get_item(items, 5))" in result.code[0].content
    assert result.error is not None
    assert result.error.startswith("Traceback")
    assert result.error.endswith("IndexError: list index out of range")


def test_call_like_prose_is_not_code() -> None:
    result = normalize_text("I tried print(x) but it failed?")

    assert result.code == []
    assert result.question == "I tried print(x) but it failed?"


def test_js_call_statement_joins_code_block() -> None:
    text = "function add(a, b) {\n  return a + b;\n}\n\nconsole.log(add(1, 2));\n"

    result = normalize_text(text)

    assert len(result.code) == 1
    assert "console.log(add(1, 2));" in result.code[0].content


def test_leetcode_style_problem_with_constraints() -> None:
    text = (
        "Given an array of integers nums and an integer target, return indices "
        "of the two numbers such that they add up to target.\n"
        "\n"
        "You may assume that each input would have exactly one solution, and you "
        "may not use the same element twice.\n"
        "\n"
        "Example 1:\n"
        "Input: nums = [2,7,11,15], target = 9\n"
        "Output: [0,1]\n"
        "Explanation: Because nums[0] + nums[1] == 9, we return [0, 1].\n"
        "\n"
        "Example 2:\n"
        "Input: nums = [3,2,4], target = 6\n"
        "Output: [1,2]\n"
        "\n"
        "Constraints:\n"
        "- 2 <= nums.length <= 10^4\n"
        "- -10^9 <= nums[i] <= 10^9\n"
        "- -10^9 <= target <= 10^9\n"
    )
    result = normalize_text(text)

    assert result.problem is not None
    assert "Given an array of integers" in result.problem
    assert result.constraints == [
        "2 <= nums.length <= 10^4",
        "-10^9 <= nums[i] <= 10^9",
        "-10^9 <= target <= 10^9",
    ]
    assert result.code == []


def test_constraint_bullet_strip_preserves_negative_sign() -> None:
    """A leading `-` that is part of a negative bound must never be eaten as a
    bullet marker — only `-`/`*`/`•` followed by whitespace is a bullet."""
    text = "Constraints:\n- 1 <= n <= 100\n-10^9 <= nums[i] <= 10^9\n"

    result = normalize_text(text)

    assert result.constraints == ["1 <= n <= 100", "-10^9 <= nums[i] <= 10^9"]


def test_plain_concept_question() -> None:
    result = normalize_text("What is a monotonic stack?")

    assert result.question == "What is a monotonic stack?"
    assert result.code == []
    assert result.problem is None
    assert result.error is None


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        ("def foo(x):\n    self.y = x\n    return self.y\n", "python"),
        ("function foo() {\n  const x = 1;\n  return x;\n}\n", "javascript"),
        (
            "public class Main {\n"
            "    public static void main(String[] args) {\n"
            '        System.out.println("hi");\n'
            "    }\n"
            "}\n",
            "java",
        ),
        (
            "#include <iostream>\nint main() {\n    std::cout << 1;\n    return 0;\n}\n",
            "cpp",
        ),
    ],
)
def test_guess_language(snippet: str, expected: str) -> None:
    assert guess_language(snippet) == expected


def test_max_text_chars_exceeded_raises() -> None:
    with pytest.raises(ValueError, match="MAX_TEXT_CHARS"):
        normalize_text("x" * (MAX_TEXT_CHARS + 1))


def test_merge_inputs() -> None:
    image_input = StructuredInput(
        source="image",
        question="what's wrong?",
        code=[CodeBlock(content="print(1)", language="python")],
        error=None,
        problem="some problem",
        constraints=["1 <= n <= 10"],
        language="python",
    )
    text_input = StructuredInput(
        source="text",
        question="why does it fail?",
        code=[CodeBlock(content="print(2)", language="python")],
        error="ValueError: bad",
        problem=None,
        constraints=["1 <= n <= 10", "0 <= m <= 5"],
        language=None,
    )

    merged = merge_inputs(image_input, text_input)

    assert merged.source == "image"
    assert merged.question == "why does it fail?"
    assert merged.error == "ValueError: bad"
    assert merged.problem == "some problem"
    assert [block.content for block in merged.code] == ["print(2)", "print(1)"]
    assert merged.constraints == ["1 <= n <= 10", "0 <= m <= 5"]
    assert merged.language == "python"


def test_english_sentence_with_return_is_not_code() -> None:
    result = normalize_text("Given an array nums, return indices such that they sum to target.")

    assert result.code == []


# --------------------------------------------------------------------------
# Hint request embedded in a pasted problem statement (core-principle fix)
# --------------------------------------------------------------------------


def test_hint_request_trailing_line_is_split_out_as_question() -> None:
    text = (
        "Given an array nums...\n"
        "Example 1:\n"
        "Input: nums = [2,7], target = 9\n"
        "Output: [0,1]\n"
        "I only want a hint please."
    )

    result = normalize_text(text)

    assert result.question == "I only want a hint please."
    assert result.problem is not None
    assert "I only want a hint please." not in result.problem
    assert "Given an array nums..." in result.problem


# --------------------------------------------------------------------------
# Constraints heading followed by a blank line, and bulleted bounds
# --------------------------------------------------------------------------


def test_constraints_heading_with_blank_line_before_bulleted_items() -> None:
    text = "Given...\nConstraints:\n\n- 1 <= nums.length <= 10^5\n- -10^9 <= nums[i] <= 10^9"

    result = normalize_text(text)

    assert result.constraints == [
        "1 <= nums.length <= 10^5",
        "-10^9 <= nums[i] <= 10^9",
    ]
    assert "1 <= nums.length <= 10^5" not in (result.problem or "")
    assert "-10^9 <= nums[i] <= 10^9" not in (result.problem or "")


# --------------------------------------------------------------------------
# Performance: adversarial input must normalize in linear time
# --------------------------------------------------------------------------


def test_perf_many_unclosed_fences_is_fast() -> None:
    text = ("\n```x\n" * 20_000)[:MAX_TEXT_CHARS]

    start = time.perf_counter()
    normalize_text(text)
    elapsed = time.perf_counter() - start

    assert elapsed < _PERF_BUDGET_SECONDS


def test_perf_many_unclosed_tilde_fences_is_fast() -> None:
    text = ("~~~\nsome body line\n" * 10_000)[:MAX_TEXT_CHARS]

    start = time.perf_counter()
    normalize_text(text)
    elapsed = time.perf_counter() - start

    assert elapsed < _PERF_BUDGET_SECONDS


def test_perf_repeated_traceback_headers_is_fast() -> None:
    text = ("Traceback (most recent call last):\n" * 1_400)[:MAX_TEXT_CHARS]

    start = time.perf_counter()
    normalize_text(text)
    elapsed = time.perf_counter() - start

    assert elapsed < _PERF_BUDGET_SECONDS


def test_perf_single_long_line_is_fast() -> None:
    text = "x" * MAX_TEXT_CHARS

    start = time.perf_counter()
    normalize_text(text)
    elapsed = time.perf_counter() - start

    assert elapsed < _PERF_BUDGET_SECONDS
