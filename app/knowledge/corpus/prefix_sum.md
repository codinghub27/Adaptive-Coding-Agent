---
title: Prefix Sum
pattern: prefix_sum
topic: arrays
pattern_family: hashing
difficulty: E:5 M:8 H:1
aliases: cumulative sum, running sum, prefix sums array, difference array
identification_signals: repeated range-sum queries, subarray sum equals k, static array with no interleaved updates, prefix and suffix products, many range-update operations before one final read
representative_problems: Product of Array Except Self | Medium | https://leetcode.com/problems/product-of-array-except-self/ ; Number of Subarrays with Bounded Maximum | Medium | https://leetcode.com/problems/number-of-subarrays-with-bounded-maximum/ ; Two Sum | Easy | https://leetcode.com/problems/two-sum/ ; Find All Duplicates in Array | Medium | https://leetcode.com/problems/find-all-duplicates-in-an-array/ ; Contains Duplicate | Easy | https://leetcode.com/problems/contains-duplicate/ ; Pascal's Triangle | Easy | https://leetcode.com/problems/pascals-triangle/
---

# Prefix Sum

## Overview
Prefix sums precompute cumulative totals so the sum of any contiguous range can be answered in O(1) after O(n) preprocessing, instead of re-summing the range each time. Use it whenever a problem asks for many range-sum queries over a static array, or reframes a subarray condition ("subarray sums to k") as the difference between two prefix sums. It also underlies 2D range-sum queries and "difference array" tricks for many range-update operations before a single final read.

## When to Recognize It
Recognize it when the problem involves repeated queries of "sum (or count) of elements between index i and j" over an array that doesn't change between queries. "Subarray sum equals k" is the classic reframe: it becomes "does `prefix[j] - prefix[i]` equal k for some i < j", which is a hash-map lookup. If updates and queries interleave instead, a Fenwick tree/segment tree is the better tool.

## Core Intuition
Any range sum `sum(nums[i:j])` equals `prefix[j] - prefix[i]`, so instead of asking "what is the sum of this range" you can ask "have I seen a prefix value that, subtracted from the current running sum, gives the target" — turning an O(n) re-scan into an O(1) hash-map lookup. The running sum *is* the implicit prefix-sum array; you never need to materialize it as a separate list when only pairwise differences matter.

## Identification Signals
- repeated "sum of elements between index i and j" queries
- "subarray sum equals k" or similar difference-of-two-prefixes phrasing
- the array is static — no interleaved updates and queries
- "prefix and suffix products" (Product of Array Except Self)
- many range-update operations followed by one final read (difference array)

## General Template
```python
def subarray_sum_equals_k(nums: list[int], k: int) -> int:
    """Count subarrays summing to k using running prefix sum + hash map."""
    count_by_prefix: dict[int, int] = {0: 1}
    running_sum = 0
    result = 0
    for num in nums:
        running_sum += num
        result += count_by_prefix.get(running_sum - k, 0)
        count_by_prefix[running_sum] = count_by_prefix.get(running_sum, 0) + 1
    return result
```

## Complexity
Building the prefix-sum array is O(n) time and space; each range-sum query then costs O(1). The hash-map variant (subarray-sum-equals-k style) is O(n) time and space overall, versus O(n^2) for checking every subarray directly. A 2D prefix sum costs O(rows * cols) to build and O(1) per rectangle query. A difference array turns m range-add updates into O(m + n) total work instead of O(m * n).

## Common Mistakes
Off-by-one errors between a 1-indexed prefix array (`prefix[j+1] - prefix[i]`) and a 0-indexed one. Forgetting to seed the hash map with `{0: 1}` in subarray-sum-equals-k, which undercounts subarrays starting at index 0. Rebuilding the prefix array after every update when updates and queries actually interleave — a Fenwick tree is needed there instead.

## When NOT to Use
If the array is mutated between queries (point updates interleaved with range-sum queries), a Fenwick tree or `segment_tree` amortizes both operations to O(log n); prefix sum alone would need an O(n) rebuild per update. If the question is about the *contents* of a sliding contiguous range rather than a fixed precomputed total, `sliding_window` is more direct.

## Variations
1D prefix sum for range-sum queries; prefix XOR for range-XOR queries or "subarray XOR equals k"; prefix count of a condition (running count of 1s minus 0s) to detect balanced subarrays; 2D prefix sum for submatrix sums; difference arrays for many range-add updates before one final read; combining prefix sums with a hash map to turn an O(n^2) subarray-counting problem into O(n).

## Representative Problems
- [Product of Array Except Self](https://leetcode.com/problems/product-of-array-except-self/) — Medium
- [Number of Subarrays with Bounded Maximum](https://leetcode.com/problems/number-of-subarrays-with-bounded-maximum/) — Medium
- [Two Sum](https://leetcode.com/problems/two-sum/) — Easy
- [Find All Duplicates in Array](https://leetcode.com/problems/find-all-duplicates-in-an-array/) — Medium
- [Contains Duplicate](https://leetcode.com/problems/contains-duplicate/) — Easy
- [Pascal's Triangle](https://leetcode.com/problems/pascals-triangle/) — Easy
