---
title: Binary Search
pattern: binary_search
topic: searching
aliases: binary search on answer, lo hi search, bisect, log n search
---

# Binary Search

## When to use
Binary search applies whenever you can define a monotonic predicate over a range -- a condition
that is `False` for a prefix of the range and `True` for the rest (or vice versa) -- and you want
the boundary between them in O(log n) instead of O(n). The most familiar form searches a sorted
array for a value, but the more general and more frequently tested form is "binary search on the
answer": guess a numeric answer, write a `feasible(x) -> bool` check that is monotonic, and binary
search over the space of possible answers (e.g. minimum capacity to ship packages in d days, or the
smallest divisor giving a quotient below a threshold).

## Recognition signals
- The input array is sorted, or the problem can be reframed as searching over a monotonic answer
  space (minimize/maximize the smallest/largest value satisfying a condition).
- You see phrases like "find the smallest x such that ...", "minimum time/capacity/speed to ...".
- A brute-force linear scan works but the answer space is large enough that O(log n) matters.
- You need the insertion point or first/last occurrence of a value in sorted data (`bisect_left`/
  `bisect_right`).

## Template
This half-open-interval form (`hi = len(nums)`, loop while `lo < hi`) avoids the classic
closed-interval off-by-one pitfalls and generalizes directly to "binary search on the answer" by
swapping the comparison for a `feasible(mid)` predicate.

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
O(log n) time per search regardless of variant, since the search space halves each iteration.
Space is O(1) iteratively (O(log n) if implemented recursively, due to call stack). "Binary search
on the answer" costs O(log(range) * cost of feasible-check); the feasible check itself is often
O(n), giving O(n log(range)) overall -- still far better than checking every candidate answer
directly.

## Common mistakes
Loop invariant confusion: mixing `lo <= hi` with `hi = len(nums) - 1` (closed interval) versus
`lo < hi` with `hi = len(nums)` (half-open interval) is fine individually but mixing conventions
mid-function causes off-by-one bugs or infinite loops. Writing `mid = (lo + hi) // 2` risks integer
overflow in other languages (not Python, but `lo + (hi - lo) // 2` is still good habit). Forgetting
to move `lo`/`hi` past `mid` (using `hi = mid` when it should be `hi = mid - 1`, or vice versa)
causes infinite loops when `lo`/`hi` stop converging. Applying binary search to a predicate that
isn't actually monotonic gives silently wrong answers with no error raised.

## Variations
Search sorted array for exact value; `bisect_left`/`bisect_right` for first/last occurrence or
insertion point; binary search on the answer for optimization problems (minimize the max, maximize
the min, "Koko eating bananas", "split array largest sum"); search in a rotated sorted array (extra
check for which half is sorted); binary search on a 2D sorted matrix (treat as a flattened 1D array,
or eliminate a row/column per step).
