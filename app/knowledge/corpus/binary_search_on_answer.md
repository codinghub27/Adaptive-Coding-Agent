---
title: Binary Search on Answer
pattern: binary_search_on_answer
topic: searching
pattern_family: searching
difficulty: E:3 M:8 H:3
aliases: binary search on the answer, search space binary search, parametric search, minimize maximize binary search
identification_signals: minimize the maximum, maximize the minimum, smallest value such that, find the minimum days/speed/capacity
representative_problems: Koko Eating Bananas | Medium | https://leetcode.com/problems/koko-eating-bananas/ ; Capacity to Ship Packages Within D Days | Medium | https://leetcode.com/problems/capacity-to-ship-packages-within-d-days/ ; Split Array Largest Sum | Hard | https://leetcode.com/problems/split-array-largest-sum/
---

# Binary Search on Answer

## Overview
Binary search on answer applies binary search not to a sorted array of values, but to the **space of
possible answers** to an optimization question — a speed, capacity, distance, or day count. It works
whenever "can we achieve X?" is a yes/no question whose answer flips exactly once as X increases (or
decreases), turning an optimization problem into a search for that flip point.

## When to Recognize It
The spreadsheet's cue: *"Minimize/maximize a value over a search space."* Recognize it when the
problem asks to minimize the maximum (or maximize the minimum) of something, and you can write a
`feasible(x) -> bool` check — "can Koko finish in D days eating at speed x?", "can we ship everything
in D days with capacity x?" — that is monotonic: once true, it stays true for all larger x.

## Core Intuition
The key structural fact is **monotonicity of feasibility**: if capacity x works, any capacity greater
than x also works (more capacity only makes shipping easier), so the set of feasible x values forms
a contiguous suffix (or prefix) of the search range. Binary search exploits this by testing the
midpoint's feasibility and discarding the half of the range that's provably all-feasible or
all-infeasible, converging on the exact boundary in O(log(range)) probes instead of scanning every
candidate value.

## Identification Signals
- "minimize the maximum" / "maximize the minimum"
- "find the smallest x such that ..."
- "minimum speed/capacity/days to finish within a limit"
- answer lies in a large numeric range, not indices into a small array

## General Template
```python
from collections.abc import Callable


def binary_search_on_answer(low: int, high: int, feasible: Callable[[int], bool]) -> int:
    """Finds the smallest `x` in [low, high] for which `feasible(x)` is True,
    assuming feasibility is monotonic (False ... False, True ... True)."""
    while low < high:
        mid = low + (high - low) // 2
        if feasible(mid):
            high = mid  # mid works; a smaller value might too
        else:
            low = mid + 1  # mid doesn't work; need something larger
    return low
```

## Complexity
O(log(range) * cost_of_feasible) time, where `range` is `high - low` and `cost_of_feasible` is
whatever it takes to evaluate the yes/no check once (often O(n) over the input array). Space is
O(1) beyond whatever `feasible` itself needs.

## Common Mistakes
Writing a `feasible` check that isn't actually monotonic — if feasibility can flip back and forth,
binary search silently returns a wrong boundary instead of erroring. Off-by-one errors in the
`low`/`high` update (using `mid - 1`/`mid + 1` inconsistently causes infinite loops or skips the
true answer). Picking too narrow a search range (e.g. `high` below the true feasible minimum,
which is a common bug when the answer can equal `max(array)` or `sum(array)`).

## When NOT to Use
If the answer is an index into an already-sorted array (classic "find target" search), use plain
`binary_search` instead — there's no feasibility predicate to invent. If the search space isn't
provably monotonic in feasibility, this pattern doesn't apply at all; look for a greedy or DP
formulation instead.

## Variations
Minimizing instead of maximizing (flip the comparison and search for the largest feasible x);
binary search on a real-valued (floating point) answer, iterating a fixed number of times instead of
until `low == high`; binary search combined with a greedy or two-pointer feasibility check (e.g.
counting shipments needed for a given capacity in O(n)).

## Representative Problems
- [Koko Eating Bananas](https://leetcode.com/problems/koko-eating-bananas/) — Medium
- [Capacity to Ship Packages Within D Days](https://leetcode.com/problems/capacity-to-ship-packages-within-d-days/) — Medium
- [Split Array Largest Sum](https://leetcode.com/problems/split-array-largest-sum/) — Hard
- [Median of Two Sorted Arrays](https://leetcode.com/problems/median-of-two-sorted-arrays/) — Hard
- [Find Peak Element](https://leetcode.com/problems/find-peak-element/) — Medium
