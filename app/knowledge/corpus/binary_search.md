---
title: Binary Search
pattern: binary_search
topic: searching
pattern_family: searching
difficulty: E:3 M:8 H:3
aliases: binary search on answer, lo hi search, bisect, log n search
identification_signals: sorted array, monotonic predicate over a range, find the smallest x such that, first or last occurrence, insertion point
representative_problems: Binary Search | Easy | https://leetcode.com/problems/binary-search/ ; Search a 2D Matrix | Medium | https://leetcode.com/problems/search-a-2d-matrix/ ; Search in Rotated Sorted Array | Medium | https://leetcode.com/problems/search-in-rotated-sorted-array/ ; Find Minimum in Rotated Sorted Array | Medium | https://leetcode.com/problems/find-minimum-in-rotated-sorted-array/ ; Median of Two Sorted Arrays | Hard | https://leetcode.com/problems/median-of-two-sorted-arrays/ ; Find First and Last Position of Element in Sorted Array | Medium | https://leetcode.com/problems/find-first-and-last-position-of-element-in-sorted-array/ ; Find Peak Element | Medium | https://leetcode.com/problems/find-peak-element/
---

# Binary Search

## Overview
Binary search applies whenever you can define a monotonic predicate over a range — a condition that is `False` for a prefix of the range and `True` for the rest (or vice versa) — and you want the boundary between them in O(log n) instead of O(n). The most familiar form searches a sorted array for a value; see `binary_search_on_answer` for the "guess a numeric answer and binary search the feasible range" generalization.

## When to Recognize It
Quick-reference cue: *"Sorted arrays, search space problems, minimize/maximize."* Recognize it when the input array is sorted, when a brute-force linear scan works but the answer space is large enough that O(log n) matters, or when you need the insertion point or first/last occurrence of a value in sorted data (`bisect_left`/`bisect_right`).

## Core Intuition
A monotonic predicate splits the search space into exactly two contiguous regions — everything before the boundary fails the check, everything after it passes. Testing the midpoint tells you which region it's in and, because the split is monotonic, eliminates the other half entirely: there is never a need to re-examine it. Halving the space every iteration is what turns O(n) into O(log n).

## Identification Signals
- the array is sorted, or a monotonic predicate exists over the range
- "find the smallest x such that ...", "minimum/first value satisfying ..."
- a brute-force linear scan works but the input is large enough that O(log n) matters
- need the insertion point, or the first/last occurrence of a value in sorted data

## General Template
```python
def lower_bound(nums: list[int], target: int) -> int:
    """Leftmost index where nums[i] >= target (loop invariant: answer in [lo, hi])."""
    lo, hi = 0, len(nums)
    while lo < hi:
        mid = lo + (hi - lo) // 2
        if nums[mid] < target:
            lo = mid + 1
        else:
            hi = mid
    return lo
```

## Complexity
O(log n) time per search regardless of variant, since the search space halves each iteration. Space is O(1) iteratively (O(log n) recursively, due to the call stack). Applying the same idea to "binary search on the answer" costs O(log(range) * cost of the feasible-check).

## Common Mistakes
Mixing `lo <= hi` with `hi = len(nums) - 1` (closed interval) and `lo < hi` with `hi = len(nums)` (half-open interval) within the same function, causing off-by-one bugs or infinite loops. Forgetting to move `lo`/`hi` past `mid` (`hi = mid` when it should be `hi = mid - 1`), causing infinite loops when the bounds stop converging. Applying binary search to a predicate that isn't actually monotonic, which gives silently wrong answers.

## When NOT to Use
If there's no sorted array or explicit value to search for, but instead a numeric quantity to minimize/maximize subject to a feasibility check, that's the same halving idea applied differently — use `binary_search_on_answer` and write a `feasible(x)` predicate instead of comparing array elements. If the collection is unsorted and can't be sorted without losing information, a hash map (`hashing`) is the right lookup tool instead.

## Variations
Search sorted array for exact value; `bisect_left`/`bisect_right` for first/last occurrence or insertion point; search in a rotated sorted array (extra check for which half is sorted); binary search on a 2D sorted matrix (treat as a flattened 1D array, or eliminate a row/column per step); binary search over two sorted arrays for a combined median.

## Representative Problems
- [Binary Search](https://leetcode.com/problems/binary-search/) — Easy
- [Search a 2D Matrix](https://leetcode.com/problems/search-a-2d-matrix/) — Medium
- [Search in Rotated Sorted Array](https://leetcode.com/problems/search-in-rotated-sorted-array/) — Medium
- [Find Minimum in Rotated Sorted Array](https://leetcode.com/problems/find-minimum-in-rotated-sorted-array/) — Medium
- [Median of Two Sorted Arrays](https://leetcode.com/problems/median-of-two-sorted-arrays/) — Hard
- [Find First and Last Position of Element in Sorted Array](https://leetcode.com/problems/find-first-and-last-position-of-element-in-sorted-array/) — Medium
- [Find Peak Element](https://leetcode.com/problems/find-peak-element/) — Medium
