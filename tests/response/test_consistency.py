"""A stated cost and a walk-through are checked against what is on the page.

Audit section 15: "Accuracy is asked for in prompts, not checked."
"""

from typing import Final

import pytest

from app.response.consistency import consistent_time, trace_problems, without_trace_problems

_BRUTE: Final = (
    "def two_sum(nums, target):\n"
    "    for i in range(len(nums)):\n"
    "        for j in range(i + 1, len(nums)):\n"
    "            if nums[i] + nums[j] == target:\n"
    "                return [i, j]\n"
    "    return []\n"
)
_HASHMAP: Final = (
    "def two_sum(nums, target):\n"
    "    seen = {}\n"
    "    for i, n in enumerate(nums):\n"
    "        if target - n in seen:\n"
    "            return [seen[target - n], i]\n"
    "        seen[n] = i\n"
    "    return []\n"
)
_WINDOW: Final = (
    "def longest(s):\n"
    "    left = 0\n"
    "    seen = set()\n"
    "    best = 0\n"
    "    for right in range(len(s)):\n"
    "        while s[right] in seen:\n"
    "            seen.remove(s[left])\n"
    "            left += 1\n"
    "        seen.add(s[right])\n"
    "        best = max(best, right - left + 1)\n"
    "    return best\n"
)
_SORTED: Final = (
    "def has_dup(nums):\n"
    "    nums = sorted(nums)\n"
    "    return any(a == b for a, b in zip(nums, nums[1:]))\n"
)
_GRAPH: Final = (
    "def edges(graph):\n"
    "    total = 0\n"
    "    for u in graph:\n"
    "        for v in graph[u]:\n"
    "            total += 1\n"
    "    return total\n"
)
_WORDS: Final = (
    "def count(words):\n"
    "    total = 0\n"
    "    for word in words:\n"
    "        for ch in word:\n"
    "            total += 1\n"
    "    return total\n"
)
_TRIPLE: Final = (
    "def three(nums):\n"
    "    n = len(nums)\n"
    "    for i in range(n):\n"
    "        for j in range(i + 1, n):\n"
    "            for k in range(j + 1, n):\n"
    "                pass\n"
)
_HELPER: Final = (
    "def outer(nums):\n"
    "    for a in nums:\n"
    "        def inner():\n"
    "            for b in nums:\n"
    "                pass\n"
)


def test_a_cost_the_code_agrees_with_is_left_alone() -> None:
    assert consistent_time(_HASHMAP, "O(n)") == "O(n)"
    assert consistent_time(_BRUTE, "O(n^2)") == "O(n^2)"
    assert consistent_time(_BRUTE, "O(n²)") == "O(n²)"
    assert consistent_time(_SORTED, "O(n log n)") == "O(n log n)"


def test_linear_claimed_for_two_loops_over_the_same_input_is_corrected() -> None:
    """Audit section 15: 'O(n) claimed beside O(n^2) work'."""
    assert consistent_time(_BRUTE, "O(n)") == "O(n^2)"
    assert consistent_time(_BRUTE, "O(n log n)") == "O(n^2)"
    assert consistent_time(_TRIPLE, "O(n^2)") == "O(n^3)"


@pytest.mark.parametrize("code", [_WINDOW, _GRAPH, _WORDS, _HELPER])
def test_loops_that_are_not_the_input_squared_are_not_called_quadratic(code: str) -> None:
    """A `while` that only moves a pointer forward, an adjacency list, the
    letters of each word, a helper that is only defined: all linear."""
    assert consistent_time(code, "O(n)") == "O(n)"


def test_a_cost_the_code_rules_out_but_cannot_replace_is_not_stated() -> None:
    assert consistent_time(_SORTED, "O(n)") is None
    assert consistent_time(_HASHMAP, "O(1)") is None
    assert consistent_time("def f(n):\n    return 1 if n < 2 else n * f(n - 1)\n", "O(1)") is None


def test_what_cannot_be_checked_is_left_alone() -> None:
    assert consistent_time(_BRUTE, "O(n * m)") == "O(n * m)"
    assert consistent_time(_GRAPH, "O(V + E)") == "O(V + E)"
    assert consistent_time("def broken(:\n", "O(1)") == "O(1)"
    assert consistent_time(None, "O(n)") == "O(n)"
    assert consistent_time(_BRUTE, None) is None
    assert consistent_time("def f(x):\n    return x + 1\n", "O(1)") == "O(1)"


def test_a_sound_walk_through_has_no_problems() -> None:
    said = (
        'Take "aba". Start at index 1, the middle: s[1] is b. Expand to index 0 and index 2: '
        "both are a, so the palindrome is the whole string. What happens for [2, 7, 11, 15] "
        "when you reach index 1, which is 7?"
    )
    assert trace_problems(said) == []
    assert without_trace_problems(said) == said


def test_an_index_past_the_end_is_caught() -> None:
    """Audit section 15: an "aba" walk that did not match the string."""
    assert trace_problems('In "aba", look at index 3 next.')
    assert trace_problems("For [2, 7, 11, 15], compare index 4 with the target.")


def test_a_value_the_index_does_not_hold_is_caught() -> None:
    assert trace_problems("In \"aba\", index 0 is 'b'.")
    assert trace_problems("With [2, 7, 11, 15], index 1 is 11.")
    assert not trace_problems("With [2, 7, 11, 15], index 1 is 7.")


def test_what_is_not_a_claim_about_the_input_is_left_alone() -> None:
    """Code review, 2026-10-11: each of these was dropped as a wrong sentence."""
    # A dictionary lookup beside the quoted list, not an index into it.
    assert not trace_problems("With [2, 7, 11, 15], seen[7] is 1 and seen[2] is 0.")
    # "a" is the article, not the letter at index 0.
    assert not trace_problems('Take "racecar": index 0 is a match for index 6.')
    # A quoted name is not the input.
    assert not trace_problems('Keep a set called "seen". Then look at index 5 of the array.')


def test_text_that_quotes_no_input_is_not_judged() -> None:
    assert trace_problems("Move the pointer at index 12 until index 40 is reached.") == []
    assert trace_problems("") == []


def test_the_wrong_sentence_is_dropped_and_the_rest_kept() -> None:
    said = (
        'Take "aba" and start in the middle. Index 5 is the last letter. '
        "Which two letters do you compare first?"
    )
    kept = without_trace_problems(said)
    assert kept is not None
    assert "Index 5" not in kept and kept.endswith("compare first?")
    assert without_trace_problems('In "ab", index 9 is b.') is None


def test_the_cost_line_beside_revealed_code_is_the_checked_one() -> None:
    from app.response.generate import generate_response
    from tests.response.test_generate import (
        _dsa_result_with_full_hint_and_code,  # pyright: ignore[reportPrivateUsage]
        _plan,  # pyright: ignore[reportPrivateUsage]
    )

    result = _dsa_result_with_full_hint_and_code().model_copy(
        update={"code": _BRUTE, "complexity_time": "O(n)"}
    )

    shown = generate_response(result=result, plan=_plan("full")).text
    assert "O(n^2)" in shown and "Time: O(n) " not in shown

    # Without the code on the page there is nothing to check the claim against.
    hinted = generate_response(result=result, plan=_plan("pseudocode")).text
    assert "O(n^2)" not in hinted


def test_a_guided_step_that_misreads_its_example_is_not_said() -> None:
    from app.graph.subgraphs.dsa import _usable_step  # pyright: ignore[reportPrivateUsage]

    sound = 'Take "aba" and start at index 1. Which letters sit on either side of it?'
    assert _usable_step(sound) == sound
    partly = 'Take "aba" and start at index 1. Index 7 is the end. Which letters are beside it?'
    kept = _usable_step(partly)
    assert kept is not None and "Index 7" not in kept and kept.endswith("?")
    # The question itself was the wrong sentence: the generic rung is used instead.
    assert _usable_step('Take "aba" and start at index 1. What is at index 9?') is None
