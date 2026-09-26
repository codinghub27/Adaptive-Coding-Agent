---
title: Dynamic Programming
pattern: dynamic_programming
topic: optimization
pattern_family: dynamic_programming
aliases: dp, memoization, tabulation, top-down bottom-up, optimal substructure
identification_signals: overlapping subproblems, optimal substructure, number of ways to do something, minimum or maximum cost subject to constraints, state transition and base case
representative_problems: House Robber | Medium | https://leetcode.com/problems/house-robber/ ; Climbing Stairs | Easy | https://leetcode.com/problems/climbing-stairs/ ; Coin Change | Medium | https://leetcode.com/problems/coin-change/ ; Longest Increasing Subsequence | Medium | https://leetcode.com/problems/longest-increasing-subsequence/ ; Longest Common Subsequence | Medium | https://leetcode.com/problems/longest-common-subsequence/ ; Edit Distance | Hard | https://leetcode.com/problems/edit-distance/ ; Unique Paths | Medium | https://leetcode.com/problems/unique-paths/
---

# Dynamic Programming

## Overview
This is the umbrella pattern: the general DP mindset that unifies `dp_1d` (state over a single sequence, one index) and `dp_2d` (state over two sequences or a grid, two indices). Dynamic programming applies when a problem has **overlapping subproblems** (naive recursion re-solves the same smaller subproblem repeatedly) and **optimal substructure** (an optimal solution is built from optimal solutions to subproblems). See `dp_1d` and `dp_2d` for the concrete recurrences and templates.

## When to Recognize It
Recognize it when a brute-force recursive solution re-solves identical subproblems many times (visible by drawing the recursion tree); the problem asks for a count, min, or max "in how many ways" / "minimum cost to" / "longest ... such that" over prefixes, subarrays, or a budget/capacity; or the state can be described with a small, fixed number of parameters (index, remaining capacity, last choice).

## Core Intuition
Every DP solution needs three things: the **state** (what parameters uniquely identify a subproblem), the **transition** (how a state's answer is built from smaller states' answers), and the **base case**. Caching each state's answer the first time it's computed is what turns exponential brute-force recursion into polynomial time — the exponential blowup in naive recursion comes entirely from re-deriving the same state's answer along different call paths.

## Identification Signals
- a brute-force recursion re-solves identical subproblems many times
- "number of ways", "minimum/maximum cost", "longest/shortest subsequence"
- "can you reach/partition into ..." phrased over prefixes or a budget
- the state is describable with a small, fixed number of parameters

## General Template
```python
from functools import lru_cache


def coin_change(coins: list[int], amount: int) -> int:
    """Fewest coins to make amount; -1 if impossible. Top-down with memoization."""

    @lru_cache(maxsize=None)
    def min_coins(remaining: int) -> int:
        if remaining == 0:
            return 0
        if remaining < 0:
            return 10**9
        return min((1 + min_coins(remaining - c) for c in coins), default=10**9)

    result = min_coins(amount)
    return result if result < 10**9 else -1
```

## Complexity
Time is O(number of distinct states * cost per transition) — each state computed once and cached, versus exponential naive recursion. Space is O(number of states) for the cache/table, plus O(depth) recursion stack for top-down; bottom-up avoids the recursion stack but still needs O(states) space unless the transition only depends on a fixed window of previous states.

## Common Mistakes
Missing or wrong base cases, which silently propagate incorrect values through every dependent state. Defining the state with too few parameters, causing different subproblems to collide in the cache. Iterating the bottom-up table in the wrong order, referencing a "future" state not yet computed. Not distinguishing 0/1 (each item used once) from unbounded (item reusable) transitions.

## When NOT to Use
If the state is a single index into one sequence with a fixed lookback, go straight to `dp_1d` rather than the general framework. If it needs two independent indices (two strings, or a grid), use `dp_2d`. If a locally optimal choice can be proven always safe (no need to compare alternatives), `greedy` solves it in one pass without any state table at all.

## Variations
Top-down (recursion + memoization) vs bottom-up (iterative table filling, avoids recursion-limit issues, allows space optimization). 1-D DP (`dp_1d`: climbing stairs, house robber) vs 2-D DP (`dp_2d`: longest common subsequence, edit distance, unique paths). Knapsack-style DP (0/1 vs unbounded, distinguished by loop order); interval DP (matrix chain multiplication, burst balloons); DP on trees/graphs (state per node, combined from children).

## Representative Problems
- [Climbing Stairs](https://leetcode.com/problems/climbing-stairs/) — Easy
- [House Robber](https://leetcode.com/problems/house-robber/) — Medium
- [Coin Change](https://leetcode.com/problems/coin-change/) — Medium
- [Longest Increasing Subsequence](https://leetcode.com/problems/longest-increasing-subsequence/) — Medium
- [Longest Common Subsequence](https://leetcode.com/problems/longest-common-subsequence/) — Medium
- [Edit Distance](https://leetcode.com/problems/edit-distance/) — Hard
- [Unique Paths](https://leetcode.com/problems/unique-paths/) — Medium
