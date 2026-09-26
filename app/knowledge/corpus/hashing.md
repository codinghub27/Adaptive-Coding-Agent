---
title: Hashing
pattern: hashing
topic: hash_tables
pattern_family: hashing
difficulty: E:5 M:8 H:1
aliases: hash map, hash set, dictionary lookup, frequency map, hash table
identification_signals: have I seen this before, complement lookup, count occurrences, group by a derived key, O(1) average lookup without needing sorted order
representative_problems: Two Sum | Easy | https://leetcode.com/problems/two-sum/ ; Contains Duplicate | Easy | https://leetcode.com/problems/contains-duplicate/ ; Valid Anagram | Easy | https://leetcode.com/problems/valid-anagram/ ; Group Anagrams | Medium | https://leetcode.com/problems/group-anagrams/ ; Top K Frequent Elements | Medium | https://leetcode.com/problems/top-k-frequent-elements/ ; Longest Consecutive Sequence | Medium | https://leetcode.com/problems/longest-consecutive-sequence/ ; Majority Element | Easy | https://leetcode.com/problems/majority-element/
---

# Hashing

## Overview
Reach for a hash map or hash set whenever a problem needs fast (average O(1)) membership testing, counting, or lookup-by-key, where you'd otherwise scan a list repeatedly. Classic uses: detecting duplicates, counting frequencies, grouping items by a computed key (anagrams by sorted letters), turning an O(n^2) "does a complement exist" check into O(n), and memoization (caching a recursive function's results by its arguments).

## When to Recognize It
Recognize it when the brute force is "for each element, scan the rest of the array to find something" — a hash map that remembers what's been seen so far usually collapses that to a single pass. Also reach for it when you need to count occurrences of items, group elements sharing a derived key, or need O(1) average lookup/insert/delete and don't need the keys in sorted order.

## Core Intuition
A hash map trades the O(n) cost of "does this value exist somewhere in what I've already seen" for O(1) average lookup, by mapping each value to a bucket via its hash. Checking for the complement of the current element *before* inserting it (rather than scanning ahead) is what collapses a nested loop into a single pass: every element is looked up once and inserted once, so total work is O(n) instead of O(n^2).

## Identification Signals
- "have I seen this before" or "does the complement of this value exist" while scanning
- need to count occurrences (words, characters, values) — frequency map / `collections.Counter`
- need to group elements sharing a derived key (anagram groups, same remainder mod k)
- need O(1) average lookup/insert/delete, and don't need sorted key order
- memoizing a recursive function's results by its arguments

## General Template
```python
def two_sum(nums: list[int], target: int) -> tuple[int, int] | None:
    """Return indices of two numbers summing to target, one pass."""
    seen_index: dict[int, int] = {}
    for i, num in enumerate(nums):
        complement = target - num
        if complement in seen_index:
            return seen_index[complement], i
        seen_index[num] = i
    return None
```

## Complexity
Average-case O(1) time per insert/lookup/delete, so a single pass over n elements is O(n) overall, versus O(n^2) for the nested-loop brute force. Space is O(n) to store the map/set in the worst case. Worst-case per-operation time degrades to O(n) under pathological hash collisions, not a practical concern for Python's dict/set on typical inputs.

## Common Mistakes
Using a mutable/unhashable type (a `list`) as a dict key or set element, which raises `TypeError` — convert to `tuple` first. Relying on dict/set iteration order as if it were sorted order. Checking `if complement in nums` (O(n) list scan) instead of a set/dict built alongside the scan. Double-counting: for two-sum-style problems, check the map *before* inserting the current element.

## When NOT to Use
If the problem needs the keys or elements in sorted order (finding a range, the kth smallest), a heap (`heaps`) or a sorted structure is the right tool — a hash map gives no ordering. If the "lookup" is actually a range-sum query over a static array, `prefix_sum` answers it in O(1) without any hashing at all.

## Variations
Frequency counting with `collections.Counter` (majority element, anagram detection, top-k frequent). Grouping by derived key with `collections.defaultdict(list)` (group anagrams by sorted-tuple key). Set-based deduplication and O(1) membership tests (longest consecutive sequence, checking only run-starts). Hashing for memoization in dynamic programming (`functools.lru_cache` or an explicit dict cache). Rolling hash for substring matching (Rabin-Karp).

## Representative Problems
- [Two Sum](https://leetcode.com/problems/two-sum/) — Easy
- [Contains Duplicate](https://leetcode.com/problems/contains-duplicate/) — Easy
- [Valid Anagram](https://leetcode.com/problems/valid-anagram/) — Easy
- [Group Anagrams](https://leetcode.com/problems/group-anagrams/) — Medium
- [Top K Frequent Elements](https://leetcode.com/problems/top-k-frequent-elements/) — Medium
- [Longest Consecutive Sequence](https://leetcode.com/problems/longest-consecutive-sequence/) — Medium
- [Majority Element](https://leetcode.com/problems/majority-element/) — Easy
