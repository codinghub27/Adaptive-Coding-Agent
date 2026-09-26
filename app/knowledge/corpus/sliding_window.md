---
title: Sliding Window
pattern: sliding_window
topic: arrays
pattern_family: two_pointer_window
difficulty: E:1 M:10 H:3
aliases: variable-size window, fixed-size window, shrinking window, two-pointer window
identification_signals: contiguous subarray or substring with a constraint, incrementally checkable condition, longest or shortest window, at most k distinct, without repeating characters, monotonic deque window maximum
representative_problems: Longest Substring Without Repeating Characters | Medium | https://leetcode.com/problems/longest-substring-without-repeating-characters/ ; Minimum Window Substring | Hard | https://leetcode.com/problems/minimum-window-substring/ ; Longest Repeating Character Replacement | Medium | https://leetcode.com/problems/longest-repeating-character-replacement/ ; Sliding Window Maximum | Hard | https://leetcode.com/problems/sliding-window-maximum/ ; Permutation in String | Medium | https://leetcode.com/problems/permutation-in-string/ ; Minimum Size Subarray Sum | Medium | https://leetcode.com/problems/minimum-size-subarray-sum/ ; Best Time to Buy and Sell Stock | Easy | https://leetcode.com/problems/best-time-to-buy-and-sell-stock/
---

# Sliding Window

## Overview
Sliding window answers a question over every contiguous subarray or substring of an array/string, maintaining the answer incrementally as the window moves instead of recomputing it from scratch. A **fixed-size window** (e.g. maximum sum of any k consecutive elements) never changes length; a **variable-size window** (e.g. longest substring without repeating characters) expands the right edge and shrinks the left edge whenever the window becomes invalid.

## When to Recognize It
Cue: *"Subarray/substring with a constraint (max, sum, distinct count)."* Recognize it when the problem asks for a longest/shortest/count of contiguous subarrays or substrings satisfying a condition, and that condition can be checked incrementally (a running sum, a frequency map, a distinct-element count) rather than needing a fresh scan of the window each time.

## Core Intuition
Because adding or removing one element from either edge is O(1) amortized, the window's validity check never has to re-examine elements already accounted for. The right edge advances at most n times over the whole scan, and the left edge also advances at most n times (it never moves backward) — so even though shrinking looks like a nested loop, total pointer movement across the entire run is bounded by O(n), not O(n^2).

## Identification Signals
- "longest", "shortest", or "count" of contiguous subarrays/substrings satisfying a condition
- "contiguous", "substring", "subarray", "consecutive"
- "at most k distinct", "without repeating characters"
- the condition can be tracked incrementally (running sum, frequency map, distinct count)
- "monotonic deque window maximum" (sliding window maximum)

## General Template
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
O(n) time for both fixed and variable window variants, since each index enters and leaves the window at most once. Space is O(1) for numeric aggregates or O(k) for a frequency map/set bounded by the alphabet or distinct-value count k. Compare to the naive O(n^2)/O(n^3) of checking every subarray from scratch.

## Common Mistakes
Recomputing the window's property from scratch on every shift instead of updating it incrementally (turns O(n) back into O(n^2)). For variable-size windows, shrinking with an `if` instead of a `while` loop, which can leave the window still invalid. Off-by-one errors in the length calculation (`right - left + 1` vs `right - left`). Forgetting to update the tracking structure when an element leaves the window during a shrink.

## When NOT to Use
If the two pointers are independent probes rather than the edges of a range you're tracking aggregate state over (e.g. searching for a pair sum in a sorted array), reach for `two_pointers` instead — no window contents to maintain. If the query needs an arbitrary (non-contiguous) range's sum answered repeatedly, `prefix_sum` answers it in O(1) without any expand/shrink logic at all.

## Variations
Fixed-size window (sum/average of every k-length window, subtract the outgoing element and add the incoming one). Variable-size shrinking window (longest/shortest substring or subarray meeting a constraint). Monotonic-deque window (sliding window maximum), where a deque of indices stays in decreasing value order so the front is always the current max. Two-pointer counting variant ("subarrays with at most k distinct" via `atMost(k) - atMost(k-1)`).

## Representative Problems
- [Longest Substring Without Repeating Characters](https://leetcode.com/problems/longest-substring-without-repeating-characters/) — Medium
- [Longest Repeating Character Replacement](https://leetcode.com/problems/longest-repeating-character-replacement/) — Medium
- [Permutation in String](https://leetcode.com/problems/permutation-in-string/) — Medium
- [Minimum Window Substring](https://leetcode.com/problems/minimum-window-substring/) — Hard
- [Sliding Window Maximum](https://leetcode.com/problems/sliding-window-maximum/) — Hard
- [Minimum Size Subarray Sum](https://leetcode.com/problems/minimum-size-subarray-sum/) — Medium
- [Best Time to Buy and Sell Stock](https://leetcode.com/problems/best-time-to-buy-and-sell-stock/) — Easy
