---
title: Sliding Window
pattern: sliding_window
topic: arrays
aliases: variable-size window, fixed-size window, shrinking window, two-pointer window
---

# Sliding Window

## When to use
Use sliding window whenever you need an answer over every contiguous subarray or substring of an
array/string and can maintain the answer incrementally as the window moves, rather than recomputing
it from scratch. There are two shapes: a **fixed-size window** (e.g. maximum sum of any k
consecutive elements), where the window length never changes, and a **variable-size window** (e.g.
the longest substring without repeating characters, or the smallest subarray with sum >= target),
where you expand the window by moving the right edge and **shrink a variable-size window** by
moving the left edge whenever the window becomes invalid. The key requirement is that adding or
removing one element from either edge should be cheap (O(1) amortized), which is what makes the
whole scan linear instead of quadratic.

## Recognition signals
- The problem asks for a longest/shortest/count of contiguous subarrays or substrings satisfying a
  condition.
- The condition can be checked incrementally (a running sum, a character-frequency map, a count of
  distinct elements) rather than needing to re-scan the window each time.
- A brute-force solution enumerates all O(n^2) subarrays; sliding window collapses this to O(n).
- Keywords like "contiguous", "substring", "subarray", "consecutive", "at most k distinct",
  "without repeating" are strong hints.

## Template
```python
def longest_no_repeat(s: str) -> int:
    """Length of the longest substring of s with all distinct characters
    (variable-size window: expand right, shrink left while invalid)."""
    window: set[str] = set()
    left = 0
    best = 0
    for right, ch in enumerate(s):
        while ch in window:  # shrink from the left until the window is valid
            window.remove(s[left])
            left += 1
        window.add(ch)  # expand: s[left..right] now has no repeats
        best = max(best, right - left + 1)
    return best
```

## Complexity
Time is O(n) for both fixed and variable window variants: each index enters and leaves the window
at most once, so total pointer movement is O(n) even though it looks like a nested loop. Space is
O(1) for numeric aggregates (sum, count) or O(k) for a frequency map/set bounded by the alphabet or
distinct-value count k. Compare to the naive O(n^2) or O(n^3) approach of checking every subarray
and recomputing its property from scratch.

## Common mistakes
Recomputing the window's property from scratch on every shift instead of updating it incrementally
when the left/right edges move (turns an O(n) algorithm back into O(n^2)). For variable-size
windows, shrinking by a fixed amount instead of shrinking in a `while` loop until the window is
valid again -- a single `if` can leave the window still invalid. Off-by-one errors in the length
calculation (`right - left + 1` vs `right - left`). Forgetting to update the tracking structure
(map/set) when an element leaves the window during a shrink, causing stale state.

## Variations
Fixed-size window (sum/average of every k-length window, often solved by subtracting the outgoing
element and adding the incoming one). Variable-size shrinking window (longest/shortest substring or
subarray meeting a constraint). Monotonic-deque window (e.g. sliding window maximum), where a deque
of indices is kept in decreasing value order so the front is always the current window's max, each
element pushed and popped at most once for O(n) total. Two-pointer counting variant ("number of
subarrays with at most k distinct elements" via `atMost(k) - atMost(k-1)`).
