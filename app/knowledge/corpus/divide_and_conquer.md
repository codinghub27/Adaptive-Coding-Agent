---
title: Divide and Conquer
pattern: divide_and_conquer
topic: divide_and_conquer
pattern_family: divide_and_conquer
aliases: merge sort family, split and combine, recursive halving, conquer and merge
identification_signals: splittable in half, merge two sorted results, recurrence T(n) = 2T(n/2) + O(n), combine sub-solutions
representative_problems: Merge k Sorted Lists | Hard | https://leetcode.com/problems/merge-k-sorted-lists/ ; Sort List | Medium | https://leetcode.com/problems/sort-list/ ; Median of Two Sorted Arrays | Hard | https://leetcode.com/problems/median-of-two-sorted-arrays/ ; Pow(x, n) | Medium | https://leetcode.com/problems/powx-n/
---

# Divide and Conquer

## Overview
Divide and conquer is a solving *strategy*, not a topic in this study plan's 6-month schedule: it recursively splits a problem into independent smaller subproblems of the same shape, solves each half, then combines the sub-results. Its representative problem, Merge k Sorted Lists, is filed under "Linked List" in the plan; the strategy itself resurfaces across sorting, searching, and math topics wherever a problem is naturally splittable in half.

## When to Recognize It
Cue: "Merge sort, quick sort, problems splittable in half." Recognize it when a problem on an array or list can be solved independently on its left and right halves and the two halves' answers can be *combined* in less time than solving the whole problem directly; classic tells are "sorted list of sorted lists," "kth smallest across two sorted arrays," or exponentiation (`x^n`).

## Core Intuition
The recurrence T(n) = a*T(n/b) + O(f(n)) captures the cost: split into `a` subproblems of size n/b, spend O(f(n)) combining them. The Master Theorem says the total cost is dominated by whichever of "work at the leaves" vs "work combining" grows faster. The key design question is always: can the combine step be done fast enough (an O(n) merge, an O(1) multiply-and-square) that halving repeatedly still beats a direct, non-recursive approach?

## Identification Signals
- "splittable in half"
- "merge two sorted lists/arrays"
- "kth smallest across k sorted structures"
- "compute x raised to the power n efficiently"
- "recurrence T(n) = 2T(n/2) + O(n)"

## General Template
```python
def merge_sort(nums: list[int]) -> list[int]:
    """Classic divide-and-conquer sort: split, recurse, merge."""
    if len(nums) <= 1:
        return nums
    mid = len(nums) // 2
    left, right = merge_sort(nums[:mid]), merge_sort(nums[mid:])
    merged: list[int] = []
    i = j = 0
    while i < len(left) and j < len(right):
        if left[i] <= right[j]:
            merged.append(left[i])
            i += 1
        else:
            merged.append(right[j])
            j += 1
    merged.extend(left[i:])
    merged.extend(right[j:])
    return merged
```

## Complexity
For merge sort: T(n) = 2T(n/2) + O(n) solves to O(n log n) time, O(n) auxiliary space for the merge buffers plus O(log n) recursion depth. Exponentiation by squaring (Pow(x, n)) is O(log n) time by halving the exponent each call, O(log n) recursion space (or O(1) iteratively).

## Common Mistakes
Splitting the input but doing an O(n) or worse combine step, erasing the benefit versus a naive approach (e.g. re-sorting instead of merging). Off-by-one on the midpoint for odd-length inputs. Recomputing overlapping subproblems instead of independent ones — that's a sign the problem actually wants `dynamic_programming`, not divide and conquer. Missing the base case for the smallest input size (length 0 or 1), causing infinite recursion.

## When NOT to Use
If the subproblems overlap (the same smaller instance is needed by multiple branches), memoize instead — that's `dynamic_programming`, not divide and conquer. If the problem doesn't naturally split into independent halves, such as a single running aggregate, a linear scan or `prefix_sum` is simpler.

## Variations
Merge-based (merge sort, merge k sorted lists/arrays, count inversions); partition-based (quickselect, quicksort); numeric halving (fast exponentiation, integer multiplication); array-halving combined with binary search (median of two sorted arrays); combined with prefix structure to count cross-boundary pairs (count of smaller numbers after self).

## Representative Problems
- [Merge k Sorted Lists](https://leetcode.com/problems/merge-k-sorted-lists/) — Hard
- [Sort List](https://leetcode.com/problems/sort-list/) — Medium
- [Median of Two Sorted Arrays](https://leetcode.com/problems/median-of-two-sorted-arrays/) — Hard
- [Pow(x, n)](https://leetcode.com/problems/powx-n/) — Medium
- [Count of Smaller Numbers After Self](https://leetcode.com/problems/count-of-smaller-numbers-after-self/) — Hard
- [Maximum Subarray (Kadane)](https://leetcode.com/problems/maximum-subarray/) — Medium
