---
title: Two Pointers
pattern: two_pointers
topic: arrays
aliases: two-pointer technique, left-right pointers, opposite pointers, fast-slow pointers
---

# Two Pointers

## When to use
Reach for two pointers when you're scanning a sorted array, a string, or a linked list and need to
relate two positions without checking every pair, which would cost O(n^2). Typical goals: find a
pair that sums to a target, detect a palindrome, merge two sorted sequences, remove duplicates in
place, or find the middle of a linked list. The technique works because moving one pointer lets you
discard a whole range of candidates at once, based on a monotonic property of the data (usually
sortedness). It is distinct from the sliding window pattern, which maintains a *contiguous range*
between two pointers rather than using them as independent probes -- if you find yourself tracking a
window's contents (sum, count, set), you likely want sliding window instead.

## Recognition signals
- The input is sorted, or can be sorted without losing needed information.
- You need to find a pair or triplet satisfying a condition (sum, difference, closest value).
- You're comparing from both ends toward the middle (palindrome check, reverse in place).
- You need to detect a cycle or find a midpoint in a linked list (fast/slow pointers).
- You want to partition or de-duplicate an array in a single pass without extra space.
- The brute-force solution is a nested loop over index pairs, and the data has ordering you can
  exploit to avoid one of the loops.

## Template
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
Time is O(n) for the opposite-direction variant (each pointer moves at most n times total, so work
is linear, versus O(n^2) or O(n log n) for brute force plus sort). Space is O(1) beyond the input,
since only a constant number of index variables are tracked. The fast/slow (tortoise-and-hare)
variant is also O(n) time and O(1) space. If sorting is required first and the input isn't already
sorted, add O(n log n) for that step, which then dominates overall complexity.

## Common mistakes
Forgetting to sort first when the algorithm assumes sorted input, silently producing wrong answers
instead of an error. Using `lo <= hi` when the intent was `lo < hi` (or vice versa), causing an
off-by-one that either misses the last valid pair or double-counts an element against itself. Not
handling duplicate values when the problem asks for distinct pairs/triplets, leading to repeated
output. Moving both pointers unconditionally instead of moving only the one that can improve the
condition, which can skip over the correct answer. Mixing up fast/slow pointer speeds (fast should
move 2 steps for every 1 of slow) when detecting a cycle.

## Variations
Opposite-direction pointers (converge from both ends) for pair-sum and palindrome checks;
same-direction pointers (one reader, one writer) for in-place de-duplication or partitioning (e.g.
Dutch national flag); fast/slow pointers for cycle detection (Floyd's algorithm) and finding a
linked list's midpoint; three-pointer extensions for 3-sum by fixing one index and running
two-pointer on the remainder. Two pointers is also the mechanism *underneath* sliding window, but
sliding window additionally tracks aggregate state over the range between the pointers.
