---
title: 1-D Dynamic Programming
pattern: dp_1d
topic: dynamic_programming
pattern_family: dynamic_programming
difficulty: E:2 M:14 H:0
aliases: one dimensional dp, linear dp, dp on a sequence, climbing stairs pattern
identification_signals: dp[i] depends on dp[i-1], single sequence, maximum sum without adjacent, ways to reach step n, longest increasing subsequence
representative_problems: House Robber | Medium | https://leetcode.com/problems/house-robber/ ; Climbing Stairs | Easy | https://leetcode.com/problems/climbing-stairs/ ; Coin Change | Medium | https://leetcode.com/problems/coin-change/ ; Longest Increasing Subsequence | Medium | https://leetcode.com/problems/longest-increasing-subsequence/
---

# 1-D Dynamic Programming

## Overview
1-D dynamic programming solves problems over a single sequence (an array, string, or step count) where the answer at position i is built from the answers at one or a few earlier positions. It is the simplest DP family and the on-ramp to every other one: one state dimension, one recurrence, one or two base cases. See the umbrella `dynamic_programming` doc for the general DP mindset, and `dp_2d` for when the state needs two independent indices instead of one.

## When to Recognize It
Cue: "Single sequence, dp[i] depends on dp[i-1]/dp[i-2] etc." Recognize it when the input is a single array or string; the answer only needs a fixed-size window of prior results (usually one or two positions back); and brute-force recursion over "take or skip position i" or "how many ways to reach i" explores the same overlapping subproblems repeatedly.

## Core Intuition
Define dp[i] as the best or only answer restricted to the first i elements (or ending at i). The recurrence expresses dp[i] as a small combination of dp[i-1], dp[i-2], etc. — e.g. "rob house i" = max(skip it, take it + dp[i-2]). Because dp[i] never depends on anything beyond a fixed lookback window, you can usually collapse the array into O(1) rolling variables once the recurrence is proven correct on paper first.

## Identification Signals
- "dp[i] depends on dp[i-1]"
- "maximum sum without choosing two adjacent elements"
- "number of ways to climb to step n"
- "longest increasing subsequence"
- "minimum cost to reach the end"

## General Template
```python
def house_robber(nums: list[int]) -> int:
    """Max sum of non-adjacent elements. dp[i] = max(dp[i-1], dp[i-2] + nums[i])."""
    prev2, prev1 = 0, 0
    for num in nums:
        prev2, prev1 = prev1, max(prev1, prev2 + num)
    return prev1
```

## Complexity
Time O(n): one pass, constant work per index. Space O(1) with rolling variables when the recurrence only looks back a fixed number of steps; O(n) if the full dp array is kept, which is needed when the choices themselves (not just the final value) must be reconstructed.

## Common Mistakes
Off-by-one on the base cases (dp[0], dp[1]) that then poison every later value. Collapsing to O(1) rolling variables before the recurrence is verified, which hides indexing bugs. Forgetting that "no two adjacent" with a circular array (House Robber II) needs two passes, each excluding one end. Confusing "ending at i" with "using the first i elements" as the definition of dp[i].

## When NOT to Use
If the recurrence needs two independent indices (two strings, or a grid), use `dp_2d` instead. If you only need a running sum or count with no decision to make, `prefix_sum` is simpler and needs no state transitions. If the state also needs a budget or capacity dimension (a knapsack), that is 2-D DP over (index, capacity), not 1-D.

## Variations
Kadane's-style running max (Maximum Subarray); house-robber "skip or take" with wraparound (House Robber II); string DP thinly disguised as 1-D (Decode Ways, Word Break); reachability DP (Jump Game, Jump Game II); circular-array variants (Maximum Sum Circular Subarray).

## Representative Problems
- [Climbing Stairs](https://leetcode.com/problems/climbing-stairs/) — Easy
- [Min Cost Climbing Stairs](https://leetcode.com/problems/min-cost-climbing-stairs/) — Easy
- [House Robber](https://leetcode.com/problems/house-robber/) — Medium
- [House Robber II](https://leetcode.com/problems/house-robber-ii/) — Medium
- [Coin Change](https://leetcode.com/problems/coin-change/) — Medium
- [Longest Increasing Subsequence](https://leetcode.com/problems/longest-increasing-subsequence/) — Medium
- [Word Break](https://leetcode.com/problems/word-break/) — Medium
- [Maximum Subarray (Kadane)](https://leetcode.com/problems/maximum-subarray/) — Medium
