---
title: Greedy
pattern: greedy
topic: optimization
pattern_family: greedy
difficulty: E:1 M:12 H:1
aliases: greedy algorithm, greedy choice, local optimum, exchange argument
identification_signals: sort by a criterion then one pass, local optimal choice leads to global optimum, exchange argument justification, interval scheduling, fractional knapsack
representative_problems: Jump Game | Medium | https://leetcode.com/problems/jump-game/ ; Jump Game II | Medium | https://leetcode.com/problems/jump-game-ii/ ; Gas Station | Medium | https://leetcode.com/problems/gas-station/ ; Partition Labels | Medium | https://leetcode.com/problems/partition-labels/ ; Task Scheduler | Medium | https://leetcode.com/problems/task-scheduler/ ; Two City Scheduling | Medium | https://leetcode.com/problems/two-city-scheduling/ ; Hand of Straights | Medium | https://leetcode.com/problems/hand-of-straights/
---

# Greedy

## Overview
A greedy algorithm builds a solution by repeatedly making the choice that looks best *right now*, without reconsidering it later, and never backtracks. It only produces a globally optimal solution when the problem has the **greedy-choice property** (a locally optimal choice is part of some globally optimal solution) and **optimal substructure**. Classic fits: interval scheduling, minimum spanning tree, fractional knapsack, and jump-game reachability.

## When to Recognize It
Quick-reference cue: *"Local optimal choice provably leads to global optimum."* Recognize it when the solution is built by sorting by some criterion (finish time, ratio, deadline) and then making one pass, taking/skipping each item by a simple local rule. Contrast: if choosing greedily can lock you out of a better later choice and you can't prove otherwise, the problem likely needs `dynamic_programming` instead.

## Core Intuition
The exchange argument is the proof technique underneath every correct greedy algorithm: assume some optimal solution didn't make the greedy choice, then show you can swap it in without making the solution any worse. If that swap is always possible, the greedy choice is safe to commit to permanently — which is exactly why the algorithm never needs to reconsider a decision once made. Without that argument, "worked on the examples" is not evidence the algorithm is correct.

## Identification Signals
- sorting by a criterion (finish time, ratio, deadline) then a single linear pass
- "maximum/minimum number of ..." with a "always take the best option now" structure
- an informal exchange argument justifies the local choice
- choosing greedily can't be shown to lock out a better later choice

## General Template
```python
def max_non_overlapping_intervals(intervals: list[tuple[int, int]]) -> int:
    """Max number of non-overlapping intervals, via earliest-finish-time greedy."""
    if not intervals:
        return 0
    ordered = sorted(intervals, key=lambda iv: iv[1])
    count = 1
    last_end = ordered[0][1]
    for start, end in ordered[1:]:
        if start >= last_end:
            count += 1
            last_end = end
    return count
```

## Complexity
Typically O(n log n), dominated by the initial sort; the greedy pass afterward is O(n). Space is O(1) extra (O(n) if a heap maintains the "best current option," as in Huffman coding or priority-based scheduling). This is usually far better than the DP or search alternative when greedy is actually correct.

## Common Mistakes
Applying greedy to a problem that lacks the greedy-choice property (treating 0/1 knapsack as fractional knapsack) and getting a plausible-looking but wrong answer with no runtime error. Sorting by the wrong key (start time instead of end time for interval scheduling). Not proving the greedy choice is safe before trusting it. Forgetting a tie-breaking rule when two candidates are locally equal but diverge globally.

## When NOT to Use
If a locally optimal choice can be proven wrong by a later choice (0/1 knapsack is the canonical counterexample), the problem needs `dynamic_programming` instead — greedy will produce a plausible but incorrect answer with no error. If you need to explore multiple candidate choices and backtrack when one fails, use `backtracking` rather than committing to one greedy path.

## Variations
Interval scheduling / activity selection (sort by finish time); Huffman coding and task scheduling (greedy choice via a min-heap); fractional knapsack (sort by value/weight ratio — does *not* extend to 0/1 knapsack); jump game / gas station (greedy reachability tracking with a running "furthest reachable" variable); minimum spanning tree (Kruskal's with union-find, or Prim's).

## Representative Problems
- [Jump Game](https://leetcode.com/problems/jump-game/) — Medium
- [Jump Game II](https://leetcode.com/problems/jump-game-ii/) — Medium
- [Gas Station](https://leetcode.com/problems/gas-station/) — Medium
- [Partition Labels](https://leetcode.com/problems/partition-labels/) — Medium
- [Task Scheduler](https://leetcode.com/problems/task-scheduler/) — Medium
- [Two City Scheduling](https://leetcode.com/problems/two-city-scheduling/) — Medium
- [Hand of Straights](https://leetcode.com/problems/hand-of-straights/) — Medium
