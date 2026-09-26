---
title: 2-D Dynamic Programming
pattern: dp_2d
topic: dynamic_programming
pattern_family: dynamic_programming
difficulty: E:1 M:26 H:27
aliases: two dimensional dp, grid dp, dp on two sequences, dp table
identification_signals: dp[i][j] recurrence, two strings, grid with rows and columns, edit distance, longest common subsequence
representative_problems: Longest Common Subsequence | Medium | https://leetcode.com/problems/longest-common-subsequence/ ; Edit Distance | Hard | https://leetcode.com/problems/edit-distance/ ; Unique Paths | Medium | https://leetcode.com/problems/unique-paths/ ; Coin Change II | Medium | https://leetcode.com/problems/coin-change-ii/
---

# 2-D Dynamic Programming

## Overview
2-D dynamic programming solves problems where the state needs two independent indices — two strings being compared, or a grid with rows and columns — so dp[i][j] holds the best answer using a prefix of each dimension. It generalizes `dp_1d` once one sequence isn't enough context; see that doc for the single-sequence case and the umbrella `dynamic_programming` doc for the shared DP mindset.

## When to Recognize It
Cue: "Two sequences or a grid, dp[i][j] recurrence." Recognize it when the input is two strings or arrays compared position by position, or a 2-D grid being traversed; when the recurrence needs both "how far into sequence A" and "how far into sequence B" (or "row" and "column") to be well-defined; when a 1-D dp array cannot capture the state alone.

## Core Intuition
dp[i][j] answers the subproblem restricted to the first i elements of one dimension and the first j of the other. Transitions usually branch on whether the current pair of elements matches (diagonal move, dp[i-1][j-1]) versus doesn't ("skip one side," dp[i-1][j] or dp[i][j-1]). Filling the table in increasing order of i then j guarantees every dependency is already computed — draw the table on paper before coding.

## Identification Signals
- "dp[i][j] recurrence"
- "longest common subsequence"
- "minimum edit distance between two strings"
- "unique paths through a grid"
- "number of ways to reach cell (i, j)"

## General Template
```python
def longest_common_subsequence(a: str, b: str) -> int:
    """dp[i][j] = LCS length of a[:i] and b[:j]."""
    rows, cols = len(a) + 1, len(b) + 1
    dp = [[0] * cols for _ in range(rows)]
    for i in range(1, rows):
        for j in range(1, cols):
            if a[i - 1] == b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    return dp[-1][-1]
```

## Complexity
Time O(n * m) — one cell per pair of prefix lengths, constant work per cell. Space O(n * m) for the full table, often reducible to O(min(n, m)) by keeping only the previous row, since most 2-D recurrences only reference the row directly above.

## Common Mistakes
Off-by-one between "index into the string" and "index into the padded dp table" (the classic +1 row/column for the empty-prefix base case). Filling the table in the wrong order and reading an uncomputed cell. Not padding row 0 / column 0 with correct base cases (0 for LCS, i or j for edit distance). Defaulting to O(n*m) space when a rolling-row optimization would suffice.

## When NOT to Use
If only one sequence or index drives the state, use `dp_1d` — a 2-D table wastes memory and obscures the simpler recurrence. If the "two dimensions" are really just ranges within one array with no cross-comparison, consider `intervals` or an interval-DP variation instead of a full grid.

## Variations
Longest common subsequence family (LCS, edit distance, interleaving strings); grid path counting (unique paths, minimum path sum, obstacles); knapsack-as-2D (item index x remaining capacity); interval DP (dp[i][j] over a contiguous range, e.g. burst balloons); string matching with wildcards (wildcard/regex matching).

## Representative Problems
- [Unique Paths](https://leetcode.com/problems/unique-paths/) — Medium
- [Longest Common Subsequence](https://leetcode.com/problems/longest-common-subsequence/) — Medium
- [Coin Change II](https://leetcode.com/problems/coin-change-ii/) — Medium
- [Interleaving String](https://leetcode.com/problems/interleaving-string/) — Hard
- [Edit Distance](https://leetcode.com/problems/edit-distance/) — Hard
- [Minimum Path Sum](https://leetcode.com/problems/minimum-path-sum/) — Medium
- [Maximal Square](https://leetcode.com/problems/maximal-square/) — Medium
- [Distinct Subsequences](https://leetcode.com/problems/distinct-subsequences/) — Hard
