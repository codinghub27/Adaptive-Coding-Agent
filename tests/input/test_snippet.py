"""`repair_snippet`: pasted code made runnable, never changed in meaning."""

import ast

import pytest

from app.execution.testgen import top_level_functions
from app.input.snippet import is_sample_data, repair_snippet

_RAGGED_BODY = (
    "if not height:\n"
    "            return 0\n"
    "\n"
    "        left, right = 0, len(height) - 1\n"
    "        while left < right:\n"
    "            left += 1\n"
    "\n"
    "        return left\n"
)

_SOLUTION = (
    "class Solution:\n"
    "    def twoSum(self, nums: List[int], target: int = 9) -> List[int]:\n"
    "        seen = {}\n"
    "        for i, n in enumerate(nums):\n"
    "            if target - n in seen:\n"
    "                return [seen[target - n], i]\n"
    "            seen[n] = i\n"
    "        return []\n"
)


def test_runnable_code_is_returned_unchanged() -> None:
    code = "def add(a, b):\n    return a + b\n"
    assert repair_snippet(code) == code


def test_a_ragged_method_body_is_realigned_and_wrapped() -> None:
    repaired = repair_snippet(_RAGGED_BODY)
    assert repaired.startswith("def solve(height):\n    if not height:\n        return 0\n")
    assert "        left += 1\n" in repaired
    assert [f.name for f in top_level_functions(repaired)] == ["solve"]


def test_sample_input_lines_become_defaults_and_the_body_still_runs() -> None:
    repaired = repair_snippet("height = [0, 1, 0]\n" + _RAGGED_BODY)
    assert repaired.startswith("def solve(height=[0, 1, 0]):\n    if not height:")
    # The paste was a script; it still runs on its sample input when executed.
    assert repaired.rstrip().endswith("solve()")
    # The header took the sample line's place: the body keeps its line numbers.
    assert repaired.split("\n")[1] == "    if not height:"


def test_a_uniformly_indented_paste_is_dedented() -> None:
    assert repair_snippet("    x = 1\n    print(x)\n") == "x = 1\nprint(x)\n"


def test_a_solution_class_gets_a_module_level_entry_point() -> None:
    repaired = repair_snippet(_SOLUTION)
    assert repaired.startswith("from typing import *\nclass Solution:")
    assert "def twoSum(nums, target=9):\n    return Solution().twoSum(nums, target)" in repaired
    assert [f.name for f in top_level_functions(repaired)] == ["twoSum"]


def test_a_class_that_needs_constructor_arguments_is_left_alone() -> None:
    code = "class Counter:\n    def __init__(self, start):\n        self.n = start\n"
    assert repair_snippet(code) == code


@pytest.mark.parametrize(
    "code",
    [
        "public int f() { return 1; }",
        "def broken(:\n    pass\n",
        "",
        "   \n",
    ],
)
def test_what_cannot_be_repaired_is_returned_as_typed(code: str) -> None:
    assert repair_snippet(code) == code


def test_crlf_pastes_are_handled() -> None:
    repaired = repair_snippet(_RAGGED_BODY.replace("\n", "\r\n"))
    assert "\r" not in repaired
    ast.parse(repaired)


def test_sample_data_is_recognised() -> None:
    assert is_sample_data("nums = [2, 7, 11, 15]\ntarget = 9")
    assert not is_sample_data("nums = [2, 7]\nprint(nums)")
    assert not is_sample_data("def f():\n    return 1")
    assert not is_sample_data("not python at all {")
