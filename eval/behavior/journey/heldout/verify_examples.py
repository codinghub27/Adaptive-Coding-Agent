"""Runs every learner/agent code snippet used in the HELD-OUT journey.

Same shape as `../verify_examples.py`: (example_id, label, source, function
name, cases). The printed results are the ground truth for every sandbox line
in `heldout_journey_10.jsonl`. Run it in a container, never on the host:

    docker run --rm --network none --read-only -v <this dir>:/dataset:ro \
        -v <scratch>:/out -w /out python:3.11-slim-bookworm \
        python /dataset/verify_examples.py

(`replay_journey.py --dataset heldout` does exactly that before a replay.)
"""

from __future__ import annotations

import json
from collections import deque
from typing import Any

TREE_HELPERS = '''
class TreeNode:
    def __init__(self, val=0, left=None, right=None):
        self.val = val
        self.left = left
        self.right = right

def build(values):
    """LeetCode level-order list -> tree (None = missing child)."""
    if not values or values[0] is None:
        return None
    nodes = [None if v is None else TreeNode(v) for v in values]
    kids = nodes[::-1]
    root = kids.pop()
    queue = [root]
    while queue and kids:
        cur = queue.pop(0)
        if cur is None:
            continue
        if kids:
            cur.left = kids.pop()
            queue.append(cur.left)
        if kids:
            cur.right = kids.pop()
            queue.append(cur.right)
    return root
'''

SNIPPETS: list[tuple[str, str, str, str, list[tuple[tuple[Any, ...], Any]]]] = []

# ---------- HX02: Minimum Size Subarray Sum ----------
_MIN_SUBARRAY_CASES: list[tuple[tuple[Any, ...], Any]] = [
    ((7, [2, 3, 1, 2, 4, 3]), 2),
    ((4, [1, 4, 4]), 1),
    ((11, [1, 1, 1, 1, 1, 1, 1, 1]), 0),
    ((6, [1, 1, 1, 1, 7]), 1),
    ((15, [5, 1, 3, 5, 10, 7, 4, 9, 2, 8]), 2),
]

SNIPPETS.append(
    (
        "hx02",
        "learner_v1_buggy",
        """
def min_sub_array_len(target, nums):
    left = 0
    total = 0
    best = len(nums) + 1
    for right, x in enumerate(nums):
        total += x
        if total >= target:
            best = min(best, right - left + 1)
            total -= nums[left]
            left += 1
    return best if best <= len(nums) else 0
""",
        "min_sub_array_len",
        _MIN_SUBARRAY_CASES,
    )
)

SNIPPETS.append(
    (
        "hx02",
        "learner_v2_fixed",
        """
def min_sub_array_len(target, nums):
    left = 0
    total = 0
    best = len(nums) + 1
    for right, x in enumerate(nums):
        total += x
        while total >= target:
            best = min(best, right - left + 1)
            total -= nums[left]
            left += 1
    return best if best <= len(nums) else 0
""",
        "min_sub_array_len",
        [*_MIN_SUBARRAY_CASES, ((3, [1, 1, 1, 1, 1, 3]), 1)],
    )
)

# ---------- HX03: Fruit Into Baskets ----------
SNIPPETS.append(
    (
        "hx03",
        "learner_v1",
        """
def total_fruit(fruits):
    left = 0
    count = {}
    best = 0
    for right, f in enumerate(fruits):
        count[f] = count.get(f, 0) + 1
        while len(count) > 2:
            count[fruits[left]] -= 1
            if count[fruits[left]] == 0:
                del count[fruits[left]]
            left += 1
        best = max(best, right - left + 1)
    return best
""",
        "total_fruit",
        [
            (([1, 2, 1],), 3),
            (([0, 1, 2, 2],), 3),
            (([1, 2, 3, 2, 2],), 4),
            (([],), 0),
            (([3, 3, 3, 1, 2, 1, 1, 2, 3, 3, 4],), 5),
        ],
    )
)

# ---------- HX04: Capacity To Ship Packages Within D Days ----------
_SHIP_CASES: list[tuple[tuple[Any, ...], Any]] = [
    (([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 5), 15),
    (([3, 2, 2, 4, 1, 4], 3), 6),
    (([1, 2, 3, 1, 1], 4), 3),
    (([10], 1), 10),
]

SNIPPETS.append(
    (
        "hx04",
        "learner_v1_buggy",
        """
def ship_within_days(weights, days):
    lo, hi = 1, sum(weights)
    while lo < hi:
        mid = (lo + hi) // 2
        needed = 1
        load = 0
        for w in weights:
            if load + w > mid:
                needed += 1
                load = 0
            load += w
        if needed <= days:
            hi = mid
        else:
            lo = mid + 1
    return lo
""",
        "ship_within_days",
        _SHIP_CASES,
    )
)

SNIPPETS.append(
    (
        "hx04",
        "learner_v2_fixed",
        """
def ship_within_days(weights, days):
    lo, hi = max(weights), sum(weights)
    while lo < hi:
        mid = (lo + hi) // 2
        needed = 1
        load = 0
        for w in weights:
            if load + w > mid:
                needed += 1
                load = 0
            load += w
        if needed <= days:
            hi = mid
        else:
            lo = mid + 1
    return lo
""",
        "ship_within_days",
        [*_SHIP_CASES, (([5, 5, 5, 5], 4), 5)],
    )
)

# ---------- HX05: Binary Tree Tilt ----------
_TILT_CASES: list[tuple[tuple[Any, ...], Any]] = [
    (([1, 2, 3],), 1),
    (([4, 2, 9, 3, 5, None, 7],), 15),
    (([21, 7, 14, 1, 1, 2, 2, 3, 3],), 9),
    (([],), 0),
]

SNIPPETS.append(
    (
        "hx05",
        "learner_v1_buggy",
        TREE_HELPERS
        + """
def find_tilt(root):
    if root is None:
        return 0
    left = find_tilt(root.left)
    right = find_tilt(root.right)
    return abs(left - right) + left + right

def solve(values):
    return find_tilt(build(values))
""",
        "solve",
        _TILT_CASES,
    )
)

SNIPPETS.append(
    (
        "hx05",
        "learner_v2_fixed",
        TREE_HELPERS
        + """
def find_tilt(root):
    total = 0
    def subtree_sum(node):
        nonlocal total
        if node is None:
            return 0
        ls = subtree_sum(node.left)
        rs = subtree_sum(node.right)
        total += abs(ls - rs)
        return node.val + ls + rs
    subtree_sum(root)
    return total

def solve(values):
    return find_tilt(build(values))
""",
        "solve",
        _TILT_CASES,
    )
)

# ---------- HX06: Longest Univalue Path ----------
_UNIVALUE_CASES: list[tuple[tuple[Any, ...], Any]] = [
    (([5, 4, 5, 1, 1, None, 5],), 2),
    (([1, 4, 5, 4, 4, None, 5],), 2),
    (([3, 2, 3, 2],), 1),
    (([],), 0),
]

SNIPPETS.append(
    (
        "hx06",
        "learner_v1_buggy",
        TREE_HELPERS
        + """
def longest_univalue_path(root):
    best = 0
    def arm(node):
        nonlocal best
        if node is None:
            return 0
        la = arm(node.left)
        ra = arm(node.right)
        if node.left and node.left.val == node.val:
            la += 1
        if node.right and node.right.val == node.val:
            ra += 1
        best = max(best, la + ra)
        return max(la, ra)
    arm(root)
    return best

def solve(values):
    return longest_univalue_path(build(values))
""",
        "solve",
        _UNIVALUE_CASES,
    )
)

SNIPPETS.append(
    (
        "hx06",
        "learner_v2_fixed",
        TREE_HELPERS
        + """
def longest_univalue_path(root):
    best = 0
    def arm(node):
        nonlocal best
        if node is None:
            return 0
        la = arm(node.left)
        ra = arm(node.right)
        la = la + 1 if node.left and node.left.val == node.val else 0
        ra = ra + 1 if node.right and node.right.val == node.val else 0
        best = max(best, la + ra)
        return max(la, ra)
    arm(root)
    return best

def solve(values):
    return longest_univalue_path(build(values))
""",
        "solve",
        [*_UNIVALUE_CASES, (([2, 2, 2, 3, 3, 3, 3],), 2)],
    )
)

# ---------- HX07: Parallel Courses (minimum semesters) ----------
SNIPPETS.append(
    (
        "hx07",
        "learner_v1",
        """
def minimum_semesters(n, relations):
    graph = [[] for _ in range(n + 1)]
    indegree = [0] * (n + 1)
    for prev, nxt in relations:
        graph[prev].append(nxt)
        indegree[nxt] += 1
    ready = [c for c in range(1, n + 1) if indegree[c] == 0]
    semesters = 0
    studied = 0
    while ready:
        semesters += 1
        upcoming = []
        for course in ready:
            studied += 1
            for nxt in graph[course]:
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    upcoming = upcoming + [nxt]
        ready = upcoming
    return semesters if studied == n else -1
""",
        "minimum_semesters",
        [
            ((3, [[1, 3], [2, 3]]), 2),
            ((3, [[1, 2], [2, 3], [3, 1]]), -1),
            ((1, []), 1),
            ((4, [[1, 2], [2, 3], [3, 4]]), 4),
            ((5, [[1, 2], [1, 3], [2, 4], [3, 4]]), 3),
        ],
    )
)

# ---------- HX08: House Robber (agent reference, Guidance mode) ----------
SNIPPETS.append(
    (
        "hx08",
        "agent_reference",
        """
def rob(nums):
    take, skip = 0, 0
    for x in nums:
        take, skip = skip + x, max(take, skip)
    return max(take, skip)
""",
        "rob",
        [
            (([1, 2, 3, 1],), 4),
            (([2, 7, 9, 3, 1],), 12),
            (([],), 0),
            (([5],), 5),
            (([2, 1, 1, 2],), 4),
        ],
    )
)

# ---------- HX10: Largest Rectangle in Histogram ----------
SNIPPETS.append(
    (
        "hx10",
        "learner_v1",
        """
def largest_rectangle_area(heights):
    stack = []
    best = 0
    for i, h in enumerate(heights + [0]):
        start = i
        while stack and stack[-1][1] >= h:
            start, height = stack.pop()
            best = max(best, height * (i - start))
        stack.append((start, h))
    return best
""",
        "largest_rectangle_area",
        [
            (([2, 1, 5, 6, 2, 3],), 10),
            (([2, 4],), 4),
            (([],), 0),
            (([3, 3, 3, 3],), 12),
            (([6, 2, 5, 4, 5, 1, 6],), 12),
        ],
    )
)


def run() -> dict[str, Any]:
    report: dict[str, Any] = {}
    for ex_id, label, src, fn, cases in SNIPPETS:
        ns: dict[str, Any] = {"deque": deque}
        exec(src, ns)  # authored snippets only, and only ever inside the container
        f = ns[fn]
        results = []
        first_fail = None
        for args, expected in cases:
            got = f(*args)
            ok = got == expected
            results.append({"input": list(args), "expected": expected, "got": got, "pass": ok})
            if not ok and first_fail is None:
                first_fail = {"input": list(args), "expected": expected, "got": got}
        passed = sum(r["pass"] for r in results)
        report[f"{ex_id}:{label}"] = {
            "passed": passed,
            "total": len(results),
            "first_failing": first_fail,
            "cases": results,
        }
        print(f"{ex_id}:{label:<18} {passed}/{len(results)}  first_fail={first_fail}")
    return report


if __name__ == "__main__":
    rep = run()
    with open("sandbox_ground_truth.json", "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
