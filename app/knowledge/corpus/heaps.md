---
title: Heaps
pattern: heaps
topic: heaps
pattern_family: heap
difficulty: E:2 M:8 H:4
aliases: priority queue, min heap, max heap, heapq, top-k, two heaps median
identification_signals: top k largest or smallest, merge k sorted lists or streams, running median of a stream, task scheduling by priority or deadline, current best option that updates over time
representative_problems: Kth Largest Element in a Stream | Easy | https://leetcode.com/problems/kth-largest-element-in-a-stream/ ; Kth Largest Element in an Array | Medium | https://leetcode.com/problems/kth-largest-element-in-an-array/ ; K Closest Points to Origin | Medium | https://leetcode.com/problems/k-closest-points-to-origin/ ; Task Scheduler | Medium | https://leetcode.com/problems/task-scheduler/ ; Find Median from Data Stream | Hard | https://leetcode.com/problems/find-median-from-data-stream/ ; Top K Frequent Words | Medium | https://leetcode.com/problems/top-k-frequent-words/ ; Meeting Rooms II | Medium | https://leetcode.com/problems/meeting-rooms-ii/
---

# Heaps

## Overview
A heap (priority queue) keeps the minimum (or maximum) of a dynamic collection accessible in O(1) and lets you insert or remove that extreme element in O(log n) — the tool of choice whenever a problem repeatedly asks "give me the smallest/largest remaining item": merging k sorted lists, scheduling by earliest deadline, finding the k largest/smallest elements, or a Dijkstra/Prim frontier. Python's `heapq` only implements a **min-heap**; negate values (or push `(-value, item)`) for max-heap behavior.

## When to Recognize It
Quick-reference cue: *"K-th element, merge K sorted, streaming median."* Recognize it in "top k" / "k largest/smallest/most frequent" (maintain a heap of size k rather than sorting the whole input); "merge k sorted lists/streams" (a heap holding each list's current head); "running median" (the two-heap technique); or task scheduling by priority/deadline where the option set updates over time.

## Core Intuition
A heap only maintains a partial order (parent <= both children, not a full sort), which is exactly enough information to answer "what's the current extreme" in O(1) while still supporting O(log n) insert/remove — sorting the whole collection to get the same answer would waste the work of ordering elements you'll never need to compare against each other. Bounding a heap to size k and evicting the current worst-of-the-best on every larger arrival is what keeps a "top-k" heap at O(n log k) instead of O(n log n).

## Identification Signals
- "top k", "k largest/smallest/most frequent"
- "merge k sorted lists/streams"
- "running median" or "median of a stream"
- task scheduling by priority/deadline, or a greedy algorithm needing the current best option as the option set changes

## General Template
```python
import heapq


def k_largest(nums: list[int], k: int) -> list[int]:
    """The k largest elements via a size-k min-heap (evict the smallest)."""
    if k <= 0:
        return []
    heap: list[int] = nums[:k]
    heapq.heapify(heap)
    for num in nums[k:]:
        if num > heap[0]:
            heapq.heapreplace(heap, num)
    return heap
```

## Complexity
`heapify` is O(n); a single push/pop is O(log n); a size-k top-k heap over n elements is O(n log k), better than sorting (O(n log n)) when k is small. Space is O(k) for a bounded top-k heap or O(n) for a full-input heap. The two-heap running-median technique does O(log n) per insertion and O(1) per median query.

## Common Mistakes
Forgetting `heapq` is a min-heap and pushing raw values when max-heap behavior is needed. Pushing into a size-k top-k heap unconditionally instead of comparing against `heap[0]` first (still correct, just unnecessary work). Letting the two heaps in a running-median setup drift more than one element apart in size, breaking the O(1) median lookup. Storing uncomparable objects directly without a sortable key, causing a `TypeError` on tie-breaks.

## When NOT to Use
If you need every element in fully sorted order (not just the extremes), sorting once is simpler and no slower asymptotically than repeated heap operations. If the problem is "does this exact value exist" rather than "what's the current smallest/largest", a hash set (`hashing`) answers that in O(1) with no ordering overhead at all.

## Variations
Fixed-size top-k heap (k largest/smallest, k most frequent via `Counter` + heap); k-way merge (a heap of `(value, list_index, elem_index)` tuples); two-heap median-of-stream (max-heap for the lower half, min-heap for the upper half); heap-based Dijkstra/Prim (a min-heap frontier of `(distance, node)`); Huffman coding (repeatedly pop the two smallest-frequency nodes and push their merge).

## Representative Problems
- [Kth Largest Element in a Stream](https://leetcode.com/problems/kth-largest-element-in-a-stream/) — Easy
- [Kth Largest Element in an Array](https://leetcode.com/problems/kth-largest-element-in-an-array/) — Medium
- [K Closest Points to Origin](https://leetcode.com/problems/k-closest-points-to-origin/) — Medium
- [Task Scheduler](https://leetcode.com/problems/task-scheduler/) — Medium
- [Find Median from Data Stream](https://leetcode.com/problems/find-median-from-data-stream/) — Hard
- [Top K Frequent Words](https://leetcode.com/problems/top-k-frequent-words/) — Medium
- [Meeting Rooms II](https://leetcode.com/problems/meeting-rooms-ii/) — Medium
