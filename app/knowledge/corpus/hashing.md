---
title: Hashing
pattern: hashing
topic: hash_tables
aliases: hash map, hash set, dictionary lookup, frequency map, hash table
---

# Hashing

## When to use
Reach for a hash map or hash set whenever a problem needs fast (average O(1)) membership testing,
counting, or lookup-by-key, and you'd otherwise be scanning a list repeatedly. Classic uses:
detecting duplicates, counting frequencies, grouping items by a computed key (e.g. anagrams by
sorted letters), turning an O(n^2) "does a complement exist" check into O(n) (two-sum), and caching
results of expensive computations (memoization is hashing under the hood). If the problem's brute
force is "for each element, scan the rest of the array to find something," a hash map that
remembers what's been seen so far usually collapses it to a single pass.

## Recognition signals
- Need to check "have I seen this before" or "does the complement of this value exist" while
  scanning.
- Need to count occurrences of items (words, characters, values) -- use a frequency map or
  `collections.Counter`.
- Need to group elements sharing a derived key (anagram groups, same remainder mod k).
- Need O(1) average lookup/insert/delete, and don't need the keys in sorted order (if you did, a
  balanced tree or sorted structure would be better).
- Memoizing a recursive function's results by its arguments.

## Template
Build the lookup structure incrementally as you scan, checking for the complement of the current
element *before* inserting it, so a single pass suffices instead of a nested loop.

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
Average-case O(1) time per insert/lookup/delete, so a single pass over n elements with hash-map
operations is O(n) time overall, versus O(n^2) for the nested-loop brute force. Space is O(n) to
store the map/set in the worst case. Worst-case per-operation time degrades to O(n) under
pathological hash collisions, but Python's dict/set are engineered so this is not a practical
concern for typical inputs.

## Common mistakes
Using a mutable or unhashable type (a `list`) as a dict key or set element, which raises
`TypeError` -- convert to `tuple` first. Forgetting that dict/set iteration order, while
insertion-ordered in modern Python, is not sorted order, so don't rely on it when a problem implies
ordering. Checking `if complement in nums` (O(n) list scan) instead of checking membership in a
set/dict built alongside the scan. Double-counting: for two-sum-style problems, checking the map
before inserting the current element avoids using the same index twice.

## Variations
Frequency counting with `collections.Counter` (majority element, anagram detection, top-k
frequent). Grouping by derived key with `collections.defaultdict(list)` (group anagrams by
sorted-tuple key). Set-based deduplication and O(1) membership tests (longest consecutive sequence,
using a set to check only run-starts). Hashing for memoization in dynamic programming
(`functools.lru_cache` or an explicit dict cache). Rolling hash for substring matching (Rabin-Karp).
Two-hash-map problems like isomorphic strings, where a consistent bijection must be maintained in
both directions.
