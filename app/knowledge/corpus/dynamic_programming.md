---
title: Dynamic Programming
pattern: dynamic_programming
topic: optimization
aliases: dp, memoization, tabulation, top-down bottom-up, optimal substructure
---

# Dynamic Programming

## When to use
Dynamic programming applies when a problem has **overlapping subproblems** (the same smaller
subproblem is solved repeatedly by naive recursion) and **optimal substructure** (an optimal
solution can be built from optimal solutions to subproblems). It turns exponential brute-force
recursion into polynomial time by caching subproblem results. Use it for counting the number of
ways to do something, finding an optimal (min/max) value subject to constraints, or deciding
feasibility, when the problem naturally decomposes by "what's the best answer using the first i
items / first i characters / a budget of j". Every DP solution needs three things: the **state**
(what parameters uniquely identify a subproblem), the **transition** (how a state's answer is built
from smaller states), and the **base case**.

## Recognition signals
- A brute-force recursive solution re-solves identical subproblems many times (visible by drawing
  the recursion tree).
- The problem asks for a count, min, or max "in how many ways" / "minimum cost to" / "longest ...
  such that" over prefixes, subarrays, or a budget/capacity.
- Keywords: "number of ways", "minimum/maximum cost", "longest/shortest subsequence", "can you
  reach/partition into".
- The state can be described with a small, fixed number of parameters (index, remaining capacity,
  last choice).

## Template
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
Time is O(number of distinct states * cost per transition) -- each state is computed once and
cached, versus exponential time for naive recursion. For `coin_change` above that's
O(amount * len(coins)). Space is O(number of distinct states) for the cache/table, plus O(depth)
recursion stack for top-down; bottom-up (tabulation) avoids the recursion stack but still needs
O(states) space unless the transition only depends on a fixed window of previous states, in which
case it can often be optimized to O(window size).

## Common mistakes
Missing or wrong base cases, which silently propagate incorrect values through every dependent
state. Defining the state with too few parameters, causing different subproblems to collide in the
cache and return wrong (stale) answers. Forgetting `@lru_cache`/memo table entirely and re-deriving
the exponential brute force by accident. Iterating the bottom-up table in the wrong order,
referencing a "future" state that hasn't been computed yet. Not distinguishing 0/1 (each item used
once) from unbounded (item reusable) transitions when the inner loop order determines this.

## Variations
Top-down (recursion + memoization, easier to write, mirrors the recursive definition directly) vs
bottom-up (iterative table filling, avoids recursion-limit issues, allows space optimization). 1D DP
(climbing stairs, house robber); 2D DP (longest common subsequence, edit distance, unique paths);
knapsack-style DP (0/1 vs unbounded, distinguished by loop order); interval DP (matrix chain
multiplication, burst balloons); DP on trees/graphs (state per node, combined from children);
space-optimized DP (rolling array when only the last one or two rows are needed).
