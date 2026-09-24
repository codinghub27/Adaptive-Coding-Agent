---
title: Prefix Sum
pattern: prefix_sum
topic: arrays
aliases: cumulative sum, running sum, prefix sums array, difference array
---

# Prefix Sum

## When to use
Prefix sums precompute cumulative totals so that the sum of any contiguous range can be answered in
O(1) after O(n) preprocessing, instead of re-summing the range each time. Use this pattern whenever
a problem asks for many range-sum queries over a static array, or reframes a subarray condition
("subarray sums to k", "equal number of 0s and 1s") in terms of the difference between two prefix
sums. It also underlies 2D range-sum queries (build a 2D prefix sum) and "difference array" tricks
for applying many range-update operations efficiently before a single final read.

## Recognition signals
- The problem involves repeated queries of "sum (or count) of elements between index i and j".
- You see "subarray sum equals k" or similar -- this becomes "does prefix[j] - prefix[i] equal k"
  for some i < j, which is a hash-map lookup problem.
- Multiple range-update operations followed by a single final read (use a difference array: add at
  start, subtract just after end, then prefix-sum once).
- The array is static (no interleaved updates and queries); if updates and queries interleave, a
  Fenwick tree/segment tree is usually the better tool.

## Template
The running sum plays the role of an implicit prefix-sum array; the hash map remembers how many
times each prefix value has occurred so far, turning the "does an earlier prefix exist" check into
O(1).

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
Building the prefix-sum array is O(n) time and O(n) space; each range-sum query then costs O(1).
For the hash-map variant (subarray-sum-equals-k style problems), the whole scan is O(n) time and
O(n) space for the map, versus O(n^2) for checking every subarray directly. A 2D prefix sum costs
O(rows * cols) to build and O(1) per rectangle query, versus O(rows * cols) per query without
precomputation. A difference array turns m range-add updates into O(m + n) total work instead of
O(m * n).

## Common mistakes
Off-by-one errors when the prefix array is 1-indexed (`prefix[i]` = sum of first i elements) versus
0-indexed -- range sum from i to j inclusive is `prefix[j+1] - prefix[i]` with a 1-indexed prefix
array, easy to get wrong. Forgetting to seed the hash map with `{0: 1}` in the subarray-sum-equals-k
pattern, which undercounts subarrays that start at index 0. Rebuilding the prefix array after every
update in a problem that actually has interleaved updates and queries, which is correct but slow --
a Fenwick tree is needed there instead. Integer overflow is not a concern in Python but can silently
bite in fixed-width-integer languages.

## Variations
1D prefix sum for range-sum queries; prefix XOR for range-XOR queries or "subarray XOR equals k";
prefix count of a condition (e.g. running count of 1s minus 0s) to detect balanced subarrays; 2D
prefix sum for submatrix sums; difference arrays for efficiently applying many range-add updates
before a single final read; combining prefix sums with a hash map to turn an O(n^2)
subarray-counting problem into O(n).
