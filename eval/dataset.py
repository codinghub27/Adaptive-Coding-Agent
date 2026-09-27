"""The evaluation dataset: one labelled turn per case.

Labels are what a competent tutor would do with the turn, written by hand.
`expected_topic` uses corpus pattern slugs only -- the closed vocabulary the
learner model keys on -- and is `None` when no corpus pattern honestly covers
the turn (a contentless follow-up, or plain loop arithmetic). `None` is a real
label here, not a missing one: inferring a topic from noise is the bug this
dataset exists to catch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

__all__ = ["DATASET", "Case"]

CaseKind = Literal["dsa", "debug", "explain", "review", "practice", "contentless"]


@dataclass(frozen=True, slots=True)
class Case:
    """One labelled turn."""

    id: str
    text: str
    kind: CaseKind
    expected_route: str
    expected_topic: str | None
    #: True when the turn asked for a hint: the response must NOT reveal code.
    hint_requested: bool = False
    #: True when the turn submits code that should verify as correct in the
    #: sandbox, so a "pass" verdict is the expected outcome.
    expect_verified_pass: bool | None = None
    tags: tuple[str, ...] = field(default_factory=tuple[str, ...])


_TWO_SUM = (
    "Given an array of integers nums and an integer target, return indices of the two "
    "numbers such that they add up to target.\nExample 1:\nInput: [2,7,11,15], 9\nOutput: [0,1]\n"
)

DATASET: tuple[Case, ...] = (
    # --- DSA -------------------------------------------------------------
    Case(
        id="dsa_two_sum_hint",
        text=_TWO_SUM + "Give me a hint, not the answer.",
        kind="dsa",
        expected_route="dsa",
        expected_topic="hashing",
        hint_requested=True,
    ),
    Case(
        id="dsa_sliding_window_hint",
        text=(
            "Given a string s, find the length of the longest substring without repeating "
            "characters.\nExample 1:\nInput: 'abcabcbb'\nOutput: 3\nGive me a hint."
        ),
        kind="dsa",
        expected_route="dsa",
        expected_topic="sliding_window",
        hint_requested=True,
    ),
    Case(
        id="dsa_correct_submission",
        text=_TWO_SUM + "Solve this and check my attempt:\n```python\n"
        "def two_sum(nums, target):\n"
        "    seen = {}\n"
        "    for i, n in enumerate(nums):\n"
        "        if target - n in seen:\n"
        "            return [seen[target - n], i]\n"
        "        seen[n] = i\n"
        "    return []\n```",
        kind="dsa",
        expected_route="dsa",
        expected_topic="hashing",
        expect_verified_pass=True,
    ),
    # --- debug -----------------------------------------------------------
    Case(
        id="debug_index_error",
        text=(
            "My binary search crashes with IndexError: list index out of range.\n"
            "```python\n"
            "def search(nums, target):\n"
            "    lo, hi = 0, len(nums)\n"
            "    while lo <= hi:\n"
            "        mid = (lo + hi) // 2\n"
            "        if nums[mid] == target:\n"
            "            return mid\n"
            "        if nums[mid] < target:\n"
            "            lo = mid + 1\n"
            "        else:\n"
            "            hi = mid - 1\n"
            "    return -1\n```"
        ),
        kind="debug",
        expected_route="debug",
        expected_topic="binary_search",
    ),
    Case(
        id="debug_wrong_window",
        text=(
            "Given an array and k, return the max sum of any contiguous subarray of length k.\n"
            "Example 1:\nInput: [1,2,3,4,5], 2\nOutput: 9\n"
            "My code gives the wrong answer:\n```python\n"
            "def max_window_sum(nums, k):\n"
            "    best = sum(nums[:k])\n"
            "    window = best\n"
            "    for i in range(k, len(nums)):\n"
            "        window += nums[i] - nums[i - k + 1]\n"
            "        if window > best:\n"
            "            best = window\n"
            "    return best\n```"
        ),
        kind="debug",
        expected_route="debug",
        expected_topic="sliding_window",
        expect_verified_pass=False,
    ),
    # --- explain ---------------------------------------------------------
    Case(
        id="explain_bfs_concept",
        text="How do I find the shortest path in an unweighted graph?",
        kind="explain",
        expected_route="explain",
        expected_topic="bfs",
    ),
    Case(
        id="explain_union_find",
        text="What is a union find data structure and when would I use it?",
        kind="explain",
        expected_route="explain",
        expected_topic="union_find",
    ),
    Case(
        id="explain_trie",
        text="What is a trie and why is it good for prefix lookups?",
        kind="explain",
        expected_route="explain",
        expected_topic="trie",
    ),
    # --- review ----------------------------------------------------------
    Case(
        id="review_hash_duplicate",
        text=(
            "Can you review this for quality?\n```python\n"
            "def has_duplicate(nums):\n"
            "    seen = set()\n"
            "    for n in nums:\n"
            "        if n in seen:\n"
            "            return True\n"
            "        seen.add(n)\n"
            "    return False\n```"
        ),
        kind="review",
        expected_route="explain",
        expected_topic="hashing",
    ),
    Case(
        id="optimize_pair_sum",
        text=(
            "How do I optimise this? It is too slow.\n```python\n"
            "def pair_sum(nums, target):\n"
            "    for i in range(len(nums)):\n"
            "        for j in range(i + 1, len(nums)):\n"
            "            if nums[i] + nums[j] == target:\n"
            "                return [i, j]\n"
            "    return []\n```"
        ),
        kind="review",
        expected_route="explain",
        expected_topic="hashing",
    ),
    # --- practice --------------------------------------------------------
    Case(
        id="practice_two_pointers",
        text="give me a two pointer problem to practise",
        kind="practice",
        expected_route="practice",
        expected_topic="two_pointers",
    ),
    Case(
        id="practice_bfs",
        text="I want to practise BFS, give me a problem",
        kind="practice",
        expected_route="practice",
        expected_topic="bfs",
    ),
    # --- contentless: a topic here would be invented ---------------------
    Case(
        id="contentless_thanks",
        text="ok thanks",
        kind="contentless",
        expected_route="clarify",
        expected_topic=None,
    ),
    Case(
        id="contentless_next_hint",
        text="give me the next hint",
        kind="contentless",
        expected_route="dsa",
        expected_topic=None,
        hint_requested=True,
    ),
    Case(
        id="contentless_factorial",
        text=(
            "can you take a look at this?\n```python\n"
            "def factorial(n):\n"
            "    result = 1\n"
            "    for i in range(1, n):\n"
            "        result *= i\n"
            "    return result\n```"
        ),
        kind="contentless",
        expected_route="debug",
        expected_topic=None,
    ),
)
