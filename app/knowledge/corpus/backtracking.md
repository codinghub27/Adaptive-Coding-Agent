---
title: Backtracking
pattern: backtracking
topic: recursion
aliases: dfs with pruning, combinatorial search, constraint search, state space search
---

# Backtracking

## When to use
Backtracking systematically builds candidate solutions incrementally and abandons ("backtracks" on)
a candidate as soon as it determines the candidate cannot possibly be completed into a valid
solution. Use it for combinatorial enumeration problems: generating all permutations, combinations,
or subsets; constraint satisfaction (N-Queens, Sudoku); partitioning problems (palindrome
partitioning, combination sum); and path-finding where you need *all* valid paths, not just one.
It's DFS over an implicit tree of partial choices, plus a pruning step that cuts off branches early
once a constraint is violated, which is what keeps it from being pure brute force.

## Recognition signals
- The problem asks to generate *all* valid arrangements/combinations/subsets/solutions, or count
  them.
- There's a natural notion of "make a choice, recurse, undo the choice" (choose an element, place a
  queen, fill a cell).
- Constraints can be checked incrementally on a partial solution, allowing branches to be pruned
  before they're fully built.
- Problem size is small enough that exponential worst-case time is acceptable (the
  constraints/pruning are what make it tractable in practice).

## Template
The canonical shape is "choose, recurse, un-choose": append a partial result, try each extension
in a loop, recurse one level deeper, then undo the extension before trying the next candidate.

```python
def subsets(nums: list[int]) -> list[list[int]]:
    """All subsets of nums via backtracking (choose / recurse / un-choose)."""
    result: list[list[int]] = []
    current: list[int] = []

    def backtrack(start: int) -> None:
        result.append(current.copy())
        for i in range(start, len(nums)):
            current.append(nums[i])
            backtrack(i + 1)
            current.pop()  # undo the choice before trying the next one

    backtrack(0)
    return result
```

## Complexity
Worst case is exponential -- O(2^n) for subsets, O(n!) for permutations, O(k^n) for k-ary choice
problems -- since backtracking explores the full implicit tree of choices in the absence of
pruning. Effective pruning (cutting branches that provably can't lead to a valid solution) reduces
the practical runtime substantially without changing the theoretical worst case. Space is O(n) for
the recursion depth plus the space to store the current partial candidate, or O(n * results) if all
results are collected.

## Common mistakes
Forgetting to undo the choice (`current.pop()`, unmark a visited cell, restore a used flag) after
recursing, which corrupts the state for sibling branches -- this is the single most common
backtracking bug. Appending a *reference* to the mutable `current` list into `result` instead of a
copy, so every stored "solution" ends up reflecting the final (empty) state after all backtracking
completes. Missing or weak pruning, making an otherwise-correct solution too slow for the input
size. Not handling duplicate elements when the problem requires unique combinations (usually needs
sorting first plus a "skip equal siblings" check).

## Variations
Subsets/power set (include-or-exclude each element); permutations (track used elements, or
swap-based in-place generation); combination sum (allow reuse vs not, with a running total against
the target); N-Queens / Sudoku (constraint propagation plus row/column/diagonal or box tracking);
palindrome partitioning (branch on every possible split point that yields a palindrome prefix).
Backtracking is DFS with the addition of pruning and an explicit undo step.
