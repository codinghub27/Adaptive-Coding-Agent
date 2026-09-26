---
title: Monotonic Stack
pattern: monotonic_stack
topic: stacks
pattern_family: stack
difficulty: E:2 M:10 H:2
aliases: monotonic stack, next greater element, next smaller element, histogram stack
identification_signals: next greater element, next warmer day, largest rectangle, stock span, car fleet
representative_problems: Daily Temperatures | Medium | https://leetcode.com/problems/daily-temperatures/ ; Next Greater Element I | Easy | https://leetcode.com/problems/next-greater-element-i/ ; Largest Rectangle in Histogram | Hard | https://leetcode.com/problems/largest-rectangle-in-histogram/ ; Car Fleet | Medium | https://leetcode.com/problems/car-fleet/
---

# Monotonic Stack

## Overview
A monotonic stack keeps its elements in strictly increasing or strictly decreasing order at all
times, popping elements that violate that order before pushing a new one. It solves "next
greater/smaller element" style problems in a single left-to-right pass, and histogram-style area
problems, both in O(n) instead of the naive O(n^2) pairwise comparison.

## When to Recognize It
The spreadsheet's cue: *"Next greater/smaller element, histogram-style problems."* Recognize it when
a problem asks, for every element, "what is the next element to the right (or left) that is
greater/smaller than this one" — or when it asks for the largest rectangle/area under a histogram-
shaped sequence of bars.

## Core Intuition
When a new element arrives that is bigger than the stack's top, every smaller element still on the
stack has just found its "next greater element" — it can never be "waiting" for anything bigger to
its right besides this new value, since this new value came first. Popping and resolving those
elements right then, instead of scanning back for them later, is what makes the whole pass O(n): each
element is pushed once and popped at most once.

## Identification Signals
- "next greater element" / "next smaller element"
- "next warmer/colder day"
- "largest rectangle in histogram"
- "stock span" (consecutive days with price <= today's)
- "car fleet" (collision/merging based on speed and position)

## General Template
```python
def next_greater_elements(nums: list[int]) -> list[int]:
    """For each index, the value of the next strictly greater element to its
    right, or -1 if none exists."""
    result = [-1] * len(nums)
    stack: list[int] = []  # indices whose next-greater is still unresolved
    for i, value in enumerate(nums):
        while stack and nums[stack[-1]] < value:
            result[stack.pop()] = value
        stack.append(i)
    return result
```

## Complexity
O(n) time — each index is pushed onto the stack exactly once and popped at most once, so the total
number of push/pop operations is bounded by 2n regardless of the while-loop's apparent nesting. O(n)
space for the stack in the worst case (a strictly monotonic input never pops anything until the end).

## Common Mistakes
Using `<=` instead of `<` (or vice versa) in the comparison, which silently changes whether equal
elements count as "greater" — matters for duplicate values. Storing values on the stack instead of
indices when the answer needs a distance or original position (daily temperatures needs `i - stack.pop()`).
Trying to solve a "next greater to the *left*" variant with a left-to-right pass instead of iterating
right-to-left (or reversing the array first).

## When NOT to Use
If the problem needs LIFO order but no ordering invariant on the values themselves (e.g. matching
brackets, expression evaluation), a plain `stack` is enough. If you need the k largest/smallest
elements rather than a per-element "next" relationship, a heap is the right tool.

## Variations
Next smaller element (flip the comparison direction); previous greater/smaller element (iterate
right-to-left, or reverse the pop logic); circular array variants (iterate the array twice,
`i % n`); histogram-area problems that track index *and* height on the stack simultaneously to
compute width once a bar is popped.

## Representative Problems
- [Daily Temperatures](https://leetcode.com/problems/daily-temperatures/) — Medium
- [Next Greater Element I](https://leetcode.com/problems/next-greater-element-i/) — Easy
- [Largest Rectangle in Histogram](https://leetcode.com/problems/largest-rectangle-in-histogram/) — Hard
- [Maximal Rectangle](https://leetcode.com/problems/maximal-rectangle/) — Hard
- [Car Fleet](https://leetcode.com/problems/car-fleet/) — Medium
- [Online Stock Span](https://leetcode.com/problems/online-stock-span/) — Medium
- [132 Pattern](https://leetcode.com/problems/132-pattern/) — Medium
- [Remove K Digits](https://leetcode.com/problems/remove-k-digits/) — Medium
