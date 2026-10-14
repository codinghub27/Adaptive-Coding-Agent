"""Runs every learner/agent code snippet used in adaptive_journey_10.jsonl.

Each entry: (example_id, label, source, function_name, cases).
The printed results are the ground truth for every "sandbox" line in the dataset.
Re-run inside the project's Docker sandbox to re-verify.
"""

from __future__ import annotations

import json
from collections import deque
from typing import Any

# ---------- shared helpers ----------
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

# ---------- EX02: Longest Substring Without Repeating Characters ----------
SNIPPETS.append(
    (
        "ex02",
        "learner_v1_buggy",
        """
def length_of_longest_substring(s):
    last_seen = {}
    left = 0
    best = 0
    for i, ch in enumerate(s):
        if ch in last_seen:
            left = last_seen[ch] + 1
        last_seen[ch] = i
        best = max(best, i - left + 1)
    return best
""",
        "length_of_longest_substring",
        [(("abcabcbb",), 3), (("bbbbb",), 1), (("pwwkew",), 3), (("",), 0), (("abba",), 2)],
    )
)

SNIPPETS.append(
    (
        "ex02",
        "learner_v2_fixed",
        """
def length_of_longest_substring(s):
    last_seen = {}
    left = 0
    best = 0
    for i, ch in enumerate(s):
        if ch in last_seen and last_seen[ch] >= left:
            left = last_seen[ch] + 1
        last_seen[ch] = i
        best = max(best, i - left + 1)
    return best
""",
        "length_of_longest_substring",
        [
            (("abcabcbb",), 3),
            (("bbbbb",), 1),
            (("pwwkew",), 3),
            (("",), 0),
            (("abba",), 2),
            (("tmmzuxt",), 5),
        ],
    )
)

# ---------- EX03: Max Consecutive Ones III ----------
SNIPPETS.append(
    (
        "ex03",
        "learner_v1",
        """
def longest_ones(nums, k):
    left = 0
    zeros = 0
    best = 0
    for right, x in enumerate(nums):
        if x == 0:
            zeros += 1
        while zeros > k:
            if nums[left] == 0:
                zeros -= 1
            left += 1
        best = max(best, right - left + 1)
    return best
""",
        "longest_ones",
        [
            (([1, 1, 1, 0, 0, 0, 1, 1, 1, 1, 0], 2), 6),
            (([0, 0, 1, 1, 0, 0, 1, 1, 1, 0, 1, 1, 0, 0, 0, 1, 1, 1, 1], 3), 10),
            (([0, 0, 0], 0), 0),
            (([1, 1, 1], 0), 3),
            (([0], 1), 1),
        ],
    )
)

# ---------- EX04: Koko Eating Bananas ----------
SNIPPETS.append(
    (
        "ex04",
        "learner_v1_buggy",
        """
def min_eating_speed(piles, h):
    lo, hi = 1, max(piles)
    while lo < hi:
        mid = (lo + hi) // 2
        hours = sum(p // mid for p in piles)
        if hours <= h:
            hi = mid
        else:
            lo = mid + 1
    return lo
""",
        "min_eating_speed",
        [
            (([3, 6, 7, 11], 8), 4),
            (([30, 11, 23, 4, 20], 5), 30),
            (([30, 11, 23, 4, 20], 6), 23),
            (([1], 1), 1),
        ],
    )
)

SNIPPETS.append(
    (
        "ex04",
        "learner_v2_fixed",
        """
def min_eating_speed(piles, h):
    lo, hi = 1, max(piles)
    while lo < hi:
        mid = (lo + hi) // 2
        hours = sum((p + mid - 1) // mid for p in piles)
        if hours <= h:
            hi = mid
        else:
            lo = mid + 1
    return lo
""",
        "min_eating_speed",
        [
            (([3, 6, 7, 11], 8), 4),
            (([30, 11, 23, 4, 20], 5), 30),
            (([30, 11, 23, 4, 20], 6), 23),
            (([1], 1), 1),
            (([1000000000], 2), 500000000),
        ],
    )
)

# ---------- EX05: Diameter of Binary Tree ----------
SNIPPETS.append(
    (
        "ex05",
        "learner_v1_buggy",
        TREE_HELPERS
        + """
def diameter_of_binary_tree(root):
    if root is None:
        return 0
    left = diameter_of_binary_tree(root.left)
    right = diameter_of_binary_tree(root.right)
    return left + right + 1

def solve(values):
    return diameter_of_binary_tree(build(values))
""",
        "solve",
        [
            (([1, 2, 3, 4, 5],), 3),
            (([1, 2],), 1),
            (([1],), 0),
            (([1, 2, None, 3, 4, 5, None, None, 6, 7],), 5),
        ],
    )
)

SNIPPETS.append(
    (
        "ex05",
        "learner_v2_fixed",
        TREE_HELPERS
        + """
def diameter_of_binary_tree(root):
    best = 0
    def height(node):
        nonlocal best
        if node is None:
            return 0
        lh = height(node.left)
        rh = height(node.right)
        best = max(best, lh + rh)
        return 1 + max(lh, rh)
    height(root)
    return best

def solve(values):
    return diameter_of_binary_tree(build(values))
""",
        "solve",
        [
            (([1, 2, 3, 4, 5],), 3),
            (([1, 2],), 1),
            (([1],), 0),
            (([1, 2, None, 3, 4, 5, None, None, 6, 7],), 5),
        ],
    )
)

# ---------- EX06: Binary Tree Maximum Path Sum ----------
SNIPPETS.append(
    (
        "ex06",
        "learner_v1_buggy",
        TREE_HELPERS
        + """
def max_path_sum(root):
    best = float("-inf")
    def gain(node):
        nonlocal best
        if node is None:
            return 0
        lg = gain(node.left)
        rg = gain(node.right)
        best = max(best, node.val + lg + rg)
        return node.val + max(lg, rg)
    gain(root)
    return best

def solve(values):
    return max_path_sum(build(values))
""",
        "solve",
        [
            (([1, 2, 3],), 6),
            (([-10, 9, 20, None, None, 15, 7],), 42),
            (([-3],), -3),
            (([2, -1],), 2),
        ],
    )
)

SNIPPETS.append(
    (
        "ex06",
        "learner_v2_fixed",
        TREE_HELPERS
        + """
def max_path_sum(root):
    best = float("-inf")
    def gain(node):
        nonlocal best
        if node is None:
            return 0
        lg = max(gain(node.left), 0)
        rg = max(gain(node.right), 0)
        best = max(best, node.val + lg + rg)
        return node.val + max(lg, rg)
    gain(root)
    return best

def solve(values):
    return max_path_sum(build(values))
""",
        "solve",
        [
            (([1, 2, 3],), 6),
            (([-10, 9, 20, None, None, 15, 7],), 42),
            (([-3],), -3),
            (([2, -1],), 2),
            (([-2, -1],), -1),
        ],
    )
)

# ---------- EX07: Course Schedule (Kahn) ----------
SNIPPETS.append(
    (
        "ex07",
        "learner_v1",
        """
def can_finish(num_courses, prerequisites):
    graph = [[] for _ in range(num_courses)]
    indegree = [0] * num_courses
    for course, pre in prerequisites:
        graph[pre].append(course)
        indegree[course] += 1
    queue = [c for c in range(num_courses) if indegree[c] == 0]
    taken = 0
    while queue:
        node = queue.pop(0)
        taken += 1
        for nxt in graph[node]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
    return taken == num_courses
""",
        "can_finish",
        [
            ((2, [[1, 0]]), True),
            ((2, [[1, 0], [0, 1]]), False),
            ((1, []), True),
            ((4, [[1, 0], [2, 1], [3, 2]]), True),
            ((3, [[0, 1], [1, 2], [2, 0]]), False),
        ],
    )
)

# ---------- EX08: Coin Change (agent-revealed, verified reference) ----------
SNIPPETS.append(
    (
        "ex08",
        "agent_reference",
        """
def coin_change(coins, amount):
    INF = amount + 1
    dp = [0] + [INF] * amount
    for a in range(1, amount + 1):
        for c in coins:
            if c <= a and dp[a - c] + 1 < dp[a]:
                dp[a] = dp[a - c] + 1
    return dp[amount] if dp[amount] != INF else -1
""",
        "coin_change",
        [
            (([1, 2, 5], 11), 3),
            (([2], 3), -1),
            (([1], 0), 0),
            (([2, 5, 10, 1], 27), 4),
            (([186, 419, 83, 408], 6249), 20),
        ],
    )
)

# ---------- EX10: Sliding Window Maximum ----------
SNIPPETS.append(
    (
        "ex10",
        "learner_v1",
        """
from collections import deque

def max_sliding_window(nums, k):
    dq = deque()
    out = []
    for i, x in enumerate(nums):
        while dq and nums[dq[-1]] <= x:
            dq.pop()
        dq.append(i)
        if dq[0] <= i - k:
            dq.popleft()
        if i >= k - 1:
            out.append(nums[dq[0]])
    return out
""",
        "max_sliding_window",
        [
            (([1, 3, -1, -3, 5, 3, 6, 7], 3), [3, 3, 5, 5, 6, 7]),
            (([1], 1), [1]),
            (([9, 8, 7], 2), [9, 8]),
            (([1, -1], 1), [1, -1]),
            (([4, 4, 4, 4], 2), [4, 4, 4]),
        ],
    )
)


def run() -> dict[str, Any]:
    report: dict[str, Any] = {}
    for ex_id, label, src, fn, cases in SNIPPETS:
        ns: dict[str, Any] = {"deque": deque}
        exec(src, ns)  # local verification of authored snippets only (not user code)
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
