---
title: Bit Manipulation
pattern: bit_manipulation
topic: bit_manipulation
pattern_family: bit_manipulation
difficulty: E:6 M:4 H:0
aliases: bitwise tricks, xor tricks, bit twiddling, bitmasking
identification_signals: find the unique element, count set bits, power of two check, no extra space allowed, xor cancellation
representative_problems: Single Number | Easy | https://leetcode.com/problems/single-number/ ; Number of 1 Bits | Easy | https://leetcode.com/problems/number-of-1-bits/ ; Counting Bits | Easy | https://leetcode.com/problems/counting-bits/ ; Sum of Two Integers | Medium | https://leetcode.com/problems/sum-of-two-integers/
---

# Bit Manipulation

## Overview
Bit manipulation solves problems by operating directly on a number's binary representation — XOR, AND, OR, and shifts — to find duplicates or uniques, count bits, or do arithmetic without the built-in `+`/`-` operators. It trades a bitwise identity for what would otherwise cost O(n) extra space or a slower comparison-based approach.

## When to Recognize It
Cue: "Find unique element, count bits, power-of-two checks." Recognize it when the problem forbids extra space for a "find the odd one out" question; asks you to count or manipulate binary digits; restricts arithmetic operators directly; or when every element in a collection appears an even number of times except one.

## Core Intuition
XOR is its own inverse and commutative: `a ^ a == 0` and `a ^ 0 == a`, so XOR-ing a whole list cancels every value appearing an even number of times, leaving only the odd one out. `n & (n - 1)` clears the lowest set bit, which underlies counting bits and power-of-two checks (a power of two has exactly one set bit, so `n & (n-1) == 0`). Addition without `+` mimics grade-school carry logic: XOR gives sum-without-carry, AND-then-shift gives the carry to add next.

## Identification Signals
- "find the single/unique number"
- "count the number of set bits"
- "determine if a number is a power of two"
- "without using extra memory"
- "add two integers without using + or -"

## General Template
```python
def single_number(nums: list[int]) -> int:
    """Every element appears twice except one; XOR cancels the pairs."""
    result = 0
    for num in nums:
        result ^= num
    return result
```

## Complexity
Time O(n) for a single pass (or O(1) for fixed-width checks like power-of-two). Space O(1) — avoiding the hash set/map that would otherwise cost O(n) is the whole point of the pattern.

## Common Mistakes
Reaching for a hash set out of habit when XOR solves "find the unique element" in O(1) space. Off-by-one on bit widths when reversing bits or masking negatives (Python ints are arbitrary-precision, so masking with e.g. `0xFFFFFFFF` is needed to simulate fixed-width overflow). Confusing `^` (XOR) with `|` (OR), or misplacing operator precedence around `&`/`|`/`^` and comparisons. Forgetting that "every element appears three times except one" needs bit-counting per position, not plain XOR.

## When NOT to Use
If duplicates can appear an arbitrary number of times with no structure, a hash map is simpler and clearer than a bitwise trick. If the problem is really about combinatorial subsets rather than binary arithmetic, backtracking or DP over a bitmask state is the better frame, not raw bit tricks.

## Variations
Single Number I/II/III (XOR for pairs, bit-counting mod 3 for triples, partitioning by a distinguishing bit for "two uniques"); population count via Brian Kernighan's `n & (n-1)`; DP-over-bitmask (Counting Bits builds on smaller counts); simulating add/subtract with XOR and carry; bit reversal via divide-and-conquer swaps.

## Representative Problems
- [Single Number](https://leetcode.com/problems/single-number/) — Easy
- [Number of 1 Bits](https://leetcode.com/problems/number-of-1-bits/) — Easy
- [Counting Bits](https://leetcode.com/problems/counting-bits/) — Easy
- [Reverse Bits](https://leetcode.com/problems/reverse-bits/) — Easy
- [Missing Number](https://leetcode.com/problems/missing-number/) — Easy
- [Sum of Two Integers](https://leetcode.com/problems/sum-of-two-integers/) — Medium
- [Power of Two](https://leetcode.com/problems/power-of-two/) — Easy
- [Single Number II](https://leetcode.com/problems/single-number-ii/) — Medium
