---
title: Two Pointers
pattern: two_pointers
topic: arrays
pattern_family: two_pointer_window
difficulty: E:5 M:8 H:1
aliases: two-pointer technique, left-right pointers, opposite pointers, fast-slow pointers
identification_signals: sorted array, pair or triplet sum, in-place manipulation, opposite ends toward the middle, cycle detection in a linked list
representative_problems: Two Sum II – Input Array Is Sorted | Medium | https://leetcode.com/problems/two-sum-ii-input-array-is-sorted/ ; 3Sum | Medium | https://leetcode.com/problems/3sum/ ; Container With Most Water | Medium | https://leetcode.com/problems/container-with-most-water/ ; Trapping Rain Water | Hard | https://leetcode.com/problems/trapping-rain-water/ ; Valid Palindrome | Easy | https://leetcode.com/problems/valid-palindrome/ ; Remove Duplicates from Sorted Array | Easy | https://leetcode.com/problems/remove-duplicates-from-sorted-array/ ; 4Sum | Medium | https://leetcode.com/problems/4sum/
---

# Two Pointers

## Overview
Two pointers scans a sorted array, a string, or a linked list using two index variables instead of checking every pair, which would cost O(n^2). Typical goals: find a pair that sums to a target, detect a palindrome, merge two sorted sequences, remove duplicates in place, or find the middle of a linked list. It is distinct from the sliding window pattern, which maintains a *contiguous range* between two pointers rather than using them as independent probes.

## When to Recognize It
The spreadsheet's cue: *"Sorted array, pair/triplet sum, in-place manipulation."* Recognize it when the input is sorted, or can be sorted without losing needed information; when you need a pair or triplet satisfying a condition; when you're comparing from both ends toward the middle (palindrome check, reverse in place); or when you need to detect a cycle or find a midpoint in a linked list (fast/slow pointers).

## Core Intuition
Moving one pointer inward is only safe because sortedness lets you rule out a whole range of candidates at once: if `nums[lo] + nums[hi] < target`, every pair using `lo` paired with anything left of `hi` is even smaller, so `lo` can never contribute a valid pair again — advancing it is the only move that can help. Repeating that one-directional elimination bounds total pointer movement to O(n) instead of the O(n^2) of checking every pair.

## Identification Signals
- "sorted array", or the array can be sorted without losing needed information
- "pair" or "triplet" summing to / matching a target
- comparing from both ends toward the middle (palindrome check, reverse in place)
- "cycle detection" or "middle of a linked list" (fast/slow pointers)
- partition or de-duplicate an array in place, in a single pass, without extra space

## General Template
```python
def two_sum_sorted(nums: list[int], target: int) -> tuple[int, int] | None:
    """Return indices (lo, hi) of two numbers summing to target, or None."""
    lo, hi = 0, len(nums) - 1
    while lo < hi:
        total = nums[lo] + nums[hi]
        if total == target:
            return lo, hi
        if total < target:
            lo += 1
        else:
            hi -= 1
    return None
```

## Complexity
O(n) time for the opposite-direction variant: each pointer moves at most n times total, versus O(n^2) (or O(n log n) for brute force plus sort). Space is O(1) beyond the input. The fast/slow (tortoise-and-hare) variant is also O(n) time, O(1) space. If sorting is required first, add O(n log n), which then dominates.

## Common Mistakes
Forgetting to sort first when the algorithm assumes sorted input, silently producing wrong answers. Using `lo <= hi` when the intent was `lo < hi` (or vice versa), causing an off-by-one. Not handling duplicate values when the problem asks for distinct pairs/triplets. Moving both pointers unconditionally instead of only the one that can improve the condition, which can skip the correct answer. Mixing up fast/slow speeds (fast should move 2 steps per 1 of slow) when detecting a cycle.

## When NOT to Use
If you need to track the *contents* of a contiguous range (a running sum, a character-frequency map, a distinct-element count) rather than just two probe positions, use `sliding_window` instead — sliding window is two pointers plus aggregate state over the range between them. If the array isn't sorted and sorting would destroy needed information (e.g. original indices matter), reach for `hashing` to turn the pair-lookup into O(1) instead.

## Variations
Opposite-direction pointers (converge from both ends) for pair-sum and palindrome checks; same-direction pointers (one reader, one writer) for in-place de-duplication or partitioning (Dutch national flag); fast/slow pointers for cycle detection (Floyd's algorithm) and finding a linked list's midpoint; three-pointer extensions for 3-sum by fixing one index and running two-pointer on the remainder.

## Representative Problems
- [Two Sum II – Input Array Is Sorted](https://leetcode.com/problems/two-sum-ii-input-array-is-sorted/) — Medium
- [3Sum](https://leetcode.com/problems/3sum/) — Medium
- [4Sum](https://leetcode.com/problems/4sum/) — Medium
- [Container With Most Water](https://leetcode.com/problems/container-with-most-water/) — Medium
- [Trapping Rain Water](https://leetcode.com/problems/trapping-rain-water/) — Hard
- [Valid Palindrome](https://leetcode.com/problems/valid-palindrome/) — Easy
- [Remove Duplicates from Sorted Array](https://leetcode.com/problems/remove-duplicates-from-sorted-array/) — Easy
