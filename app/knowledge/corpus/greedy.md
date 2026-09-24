---
title: Greedy
pattern: greedy
topic: optimization
aliases: greedy algorithm, greedy choice, local optimum, exchange argument
---

# Greedy

## When to use
A greedy algorithm builds a solution by repeatedly making the choice that looks best *right now*,
without reconsidering it later, and never backtracks. It only produces a globally optimal solution
when the problem has the **greedy-choice property** (a locally optimal choice is part of some
globally optimal solution) and **optimal substructure**. Classic fits: interval scheduling (earliest
finish time first), Huffman coding, minimum spanning tree (Kruskal/Prim), fractional knapsack, and
jump-game reachability. If a locally optimal choice can be proven wrong by a later choice (0/1
knapsack is the canonical counterexample), the problem needs dynamic programming instead.

## Recognition signals
- Sorting by some criterion (finish time, ratio, deadline) then making one pass, taking/skipping
  each item based on a simple local rule.
- The problem says "maximum/minimum number of ..." with a structure that suggests "always take the
  best available option now".
- You can informally justify with an exchange argument: "if the optimal solution didn't make this
  choice, swapping it in wouldn't make things worse."
- Contrast: if choosing greedily can lock you out of a better later choice, and you can't prove
  otherwise, the problem likely needs DP or search instead.

## Template
Sort by the criterion that lets the local choice be made safely (here, earliest finish time), then
make a single linear pass, committing to each choice without ever revisiting it.

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
Typically O(n log n), dominated by the initial sort; the single greedy pass afterward is O(n).
Space is O(1) extra (O(n) if the sort isn't in place or if a heap is used to maintain the "best
current option," as in Huffman coding or task scheduling with a priority queue). This is usually
far better than the DP or search alternative (often O(n^2) or exponential) when greedy is actually
correct for the problem.

## Common mistakes
Applying greedy to a problem that doesn't actually have the greedy-choice property (e.g. treating
0/1 knapsack as fractional knapsack) and getting a plausible-looking but wrong answer with no
runtime error to signal it. Sorting by the wrong key (e.g. sorting intervals by start time instead
of end time for the interval-scheduling problem gives a suboptimal count). Not proving (even
informally, via an exchange argument) that the greedy choice is safe before trusting it -- "it
worked on the examples" is not a proof. Forgetting a tie-breaking rule when two candidates are
equally good locally but diverge globally.

## Variations
Interval scheduling / activity selection (sort by finish time); Huffman coding and task scheduling
(greedy choice via a min-heap); fractional knapsack (sort by value/weight ratio -- note this does
*not* extend to 0/1 knapsack); jump game / gas station (greedy reachability tracking with a running
"furthest reachable" variable); minimum spanning tree (Kruskal's greedy edge selection with
union-find, or Prim's greedy vertex growth).
