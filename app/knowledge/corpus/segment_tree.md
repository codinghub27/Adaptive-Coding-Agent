---
title: Segment Tree
pattern: segment_tree
topic: range_queries
pattern_family: advanced_range
aliases: fenwick alternative, range update range query, interval tree, binary indexed tree cousin
identification_signals: range queries with updates, range sum with point updates, range minimum query, dynamic range aggregation
representative_problems: Range Sum Query – Immutable | Easy | https://leetcode.com/problems/range-sum-query-immutable/ ; Count of Smaller Numbers After Self | Hard | https://leetcode.com/problems/count-of-smaller-numbers-after-self/ ; Range Sum Query - Mutable | Medium | https://leetcode.com/problems/range-sum-query-mutable/
---

# Segment Tree

## Overview
A segment tree is listed in the study plan's pattern quick-reference (cue: "Range queries with updates") but has no dedicated problem set or topic in the 6-month, 360-problem schedule — it doesn't get its own days. It is a binary-tree structure over an array that supports both range queries (sum, min, max) and point/range updates in O(log n), which is what you reach for once a workload needs updates that a plain prefix-sum array can't handle efficiently.

## When to Recognize It
Cue: "Range queries with updates." The schedule's closest concrete example is the *static* case: Range Sum Query – Immutable (filed under 2-D DP in this plan), solvable with a plain prefix-sum array precisely *because* the array never changes after queries start. The moment the same problem allows updates between queries, prefix sums stop working in O(1) per update and a segment tree (or Fenwick tree) becomes necessary.

## Core Intuition
Each internal node stores the aggregate (sum/min/max) of a contiguous range covered by its subtree; a leaf holds one array element. A point update touches only the O(log n) ancestors of that leaf, recomputing each as the combine of its two children. A range query decomposes the requested range into O(log n) disjoint tree nodes whose precomputed aggregates already cover it, so the raw array is never rescanned.

## Identification Signals
- "range queries with updates"
- "range sum query" combined with "update"
- "range minimum/maximum query"
- "count of elements less than x in a changing range"
- "dynamic range aggregation"

## General Template
```python
class SegmentTree:
    """Sum segment tree over a fixed-size array with point updates."""

    def __init__(self, size: int) -> None:
        self.n = size
        self.tree = [0] * (2 * size)

    def update(self, index: int, value: int) -> None:
        i = index + self.n
        self.tree[i] = value
        while i > 1:
            i //= 2
            self.tree[i] = self.tree[2 * i] + self.tree[2 * i + 1]

    def query(self, left: int, right: int) -> int:
        """Sum of [left, right)."""
        result = 0
        left, right = left + self.n, right + self.n
        while left < right:
            if left % 2:
                result += self.tree[left]
                left += 1
            if right % 2:
                right -= 1
                result += self.tree[right]
            left //= 2
            right //= 2
        return result
```

## Complexity
Time O(log n) per point update and O(log n) per range query, versus O(n) per update-then-rescan with a naive array or O(1) update but O(n) query with a prefix-sum array. Space O(n) for the tree array (an iterative bottom-up segment tree needs exactly 2n slots, as above).

## Common Mistakes
Reaching for a segment tree when the array is genuinely static — a prefix-sum array (`prefix_sum`) does the same query in O(1) with far less code. Off-by-one between inclusive and half-open range conventions when decomposing a query range across tree nodes. Rebuilding the whole tree per update instead of only touching the O(log n) ancestor path. Choosing a segment tree when only prefix-sum-style point updates are needed, not arbitrary range min/max — a Fenwick/Binary Indexed Tree is simpler there.

## When NOT to Use
If the array never changes after queries begin, use `prefix_sum` — simpler, O(1) per query. If you only need point updates plus prefix-sum queries, not range min/max, a Fenwick/Binary Indexed Tree is lighter-weight than a full segment tree. Reach for a segment tree specifically when both updates *and* range aggregation queries (sum/min/max, not just prefix sums) are required together.

## Variations
Sum / min / max segment trees (differ only in the combine function); lazy propagation for O(log n) *range* updates, not just point updates; persistent segment trees (query historical versions); 2-D segment trees (range queries over a grid); merge-sort tree for order-statistics-style range queries.

## Representative Problems
In the 6-month plan:

- [Range Sum Query – Immutable](https://leetcode.com/problems/range-sum-query-immutable/) — Easy (static-array contrast; solvable with `prefix_sum`, no segment tree needed)
- [Count of Smaller Numbers After Self](https://leetcode.com/problems/count-of-smaller-numbers-after-self/) — Hard (filed under Binary Search in the plan; the classic BIT/segment-tree-over-rank solution)

**Not in the 6-month plan** (canonical practice, listed because the plan's pattern reference names Segment Tree but schedules no problem set for it):

- [Range Sum Query - Mutable](https://leetcode.com/problems/range-sum-query-mutable/) — Medium
- [My Calendar III](https://leetcode.com/problems/my-calendar-iii/) — Hard
