---
title: Backtracking
pattern: backtracking
topic: recursion
pattern_family: backtracking
difficulty: E:1 M:21 H:6
aliases: dfs with pruning, combinatorial search, constraint search, state space search
identification_signals: generate all combinations permutations or subsets, choose recurse undo, constraint satisfaction, incrementally checkable partial solution, exponential worst case acceptable
representative_problems: Subsets | Medium | https://leetcode.com/problems/subsets/ ; Permutations | Medium | https://leetcode.com/problems/permutations/ ; Combination Sum | Medium | https://leetcode.com/problems/combination-sum/ ; N-Queens | Hard | https://leetcode.com/problems/n-queens/ ; Word Search | Medium | https://leetcode.com/problems/word-search/ ; Palindrome Partitioning | Medium | https://leetcode.com/problems/palindrome-partitioning/ ; Sudoku Solver | Hard | https://leetcode.com/problems/sudoku-solver/
---

# Backtracking

## Overview
Backtracking builds candidate solutions incrementally and abandons ("backtracks" on) a candidate the moment it can't possibly be completed into a valid one. Use it for combinatorial enumeration: permutations, combinations, subsets; constraint satisfaction (N-Queens, Sudoku); partitioning problems; and path-finding where you need *all* valid paths, not just one. It's DFS over an implicit tree of partial choices, plus a pruning step that cuts off branches early.

## When to Recognize It
Recognize it when the problem asks to generate (or count) *all* valid arrangements/combinations/subsets/solutions; when there's a natural "make a choice, recurse, undo the choice" shape (choose an element, place a queen, fill a cell); when constraints can be checked incrementally on a partial solution to prune branches early; or when the problem size is small enough that exponential worst-case time is acceptable.

## Core Intuition
Undoing a choice after recursing is what lets the same mutable `current` container represent every candidate along the way, instead of allocating a fresh copy per branch. Pruning works because a constraint violated on a *partial* solution can only get worse by extending it further — so cutting the branch there costs nothing but saves everything below it, without changing what the final complete solutions are.

## Identification Signals
- generate or count *all* valid arrangements/combinations/subsets/solutions
- "choose, recurse, un-choose" (place a queen, pick an element, fill a cell)
- constraints checkable incrementally on a partial solution, enabling pruning
- input size small enough that exponential worst case is acceptable

## General Template
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
Worst case is exponential — O(2^n) for subsets, O(n!) for permutations, O(k^n) for k-ary choice problems — since it explores the full implicit choice tree absent pruning. Effective pruning reduces practical runtime substantially without changing the theoretical worst case. Space is O(n) for recursion depth plus the partial candidate, or O(n * results) if all results are collected.

## Common Mistakes
Forgetting to undo the choice (`current.pop()`, unmark a visited cell) after recursing, corrupting state for sibling branches — the single most common bug. Appending a *reference* to the mutable `current` list into `result` instead of a copy. Missing or weak pruning, making an otherwise-correct solution too slow. Not handling duplicates when unique combinations are required (usually needs sorting plus a "skip equal siblings" check).

## When NOT to Use
If the problem only needs *one* valid path or a yes/no reachability answer rather than all solutions, plain `dfs` without the undo step is simpler and doesn't need to restore state. If a locally-optimal choice can be proven to always be part of some globally-optimal solution (no need to explore alternatives), `greedy` solves it in one pass instead of exploring a tree.

## Variations
Subsets/power set (include-or-exclude each element); permutations (track used elements, or swap-based in-place generation); combination sum (allow reuse vs not, with a running total against the target); N-Queens/Sudoku (constraint propagation plus row/column/diagonal or box tracking); palindrome partitioning (branch on every split point yielding a palindrome prefix).

## Representative Problems
- [Subsets](https://leetcode.com/problems/subsets/) — Medium
- [Permutations](https://leetcode.com/problems/permutations/) — Medium
- [Combination Sum](https://leetcode.com/problems/combination-sum/) — Medium
- [N-Queens](https://leetcode.com/problems/n-queens/) — Hard
- [Word Search](https://leetcode.com/problems/word-search/) — Medium
- [Palindrome Partitioning](https://leetcode.com/problems/palindrome-partitioning/) — Medium
- [Sudoku Solver](https://leetcode.com/problems/sudoku-solver/) — Hard
