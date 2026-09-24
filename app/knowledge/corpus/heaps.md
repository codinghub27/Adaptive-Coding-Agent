---
title: Heaps
pattern: heaps
topic: heaps
aliases: priority queue, min heap, max heap, heapq, top-k, two heaps median
---

# Heaps

## When to use
A heap (priority queue) keeps the minimum (or maximum) of a dynamic collection accessible in O(1)
and lets you insert or remove that extreme element in O(log n), which is what you want whenever a
problem repeatedly asks "give me the smallest/largest remaining item" -- merging k sorted lists,
scheduling by earliest deadline, finding the k largest/smallest elements, or running a variant of
Dijkstra/Prim. Python's `heapq` module only implements a **min-heap**; to get max-heap behavior,
negate values on push and pop, or push `(-value, item)` tuples.

## Recognition signals
- "Top k", "k largest/smallest/most frequent" -- maintain a heap of size k rather than sorting the
  whole input.
- "Merge k sorted lists/streams" -- a heap holding the current head of each list.
- "Running median" or "median of a stream" -- the two-heap technique (a max-heap for the lower
  half, a min-heap for the upper half).
- "Task scheduling by priority/deadline", or any greedy algorithm that needs the current best
  option and updates the option set over time (Huffman coding, Dijkstra's frontier).

## Template
Keep the heap bounded to size k and evict the current minimum whenever a larger candidate arrives,
so the heap always holds exactly the k largest elements seen so far without ever sorting the input.

```python
import heapq


def k_largest(nums: list[int], k: int) -> list[int]:
    """The k largest elements via a size-k min-heap (evict the smallest)."""
    heap: list[int] = nums[:k]
    heapq.heapify(heap)
    for num in nums[k:]:
        if num > heap[0]:
            heapq.heapreplace(heap, num)
    return heap
```

## Complexity
`heapify` is O(n); a single push/pop is O(log n); building a size-k "top-k" heap over n elements is
O(n log k), notably better than sorting everything (O(n log n)) when k is small. Space is O(k) for
a bounded top-k heap or O(n) for a heap holding the whole input. The two-heap running-median
technique does O(log n) work per insertion and O(1) per median query.

## Common mistakes
Forgetting `heapq` is a min-heap and pushing raw values when max-heap behavior is needed -- negate
on push and pop, or store `(-value, item)`. Pushing a value into a size-k top-k heap unconditionally
instead of comparing against `heap[0]` first, which still works but does unnecessary O(log k) work
on elements that don't belong in the result. Letting the two heaps in a running-median setup drift
more than one element apart in size, which breaks the O(1) median lookup invariant. Storing
mutable/uncomparable objects directly in the heap without a sortable key, causing a `TypeError` on
tie-breaks -- pair with an index or explicit key.

## Variations
Fixed-size top-k heap (k largest/smallest, k most frequent via `Counter` + heap); k-way merge (a
heap of `(value, list_index, elem_index)` tuples, one entry per list's current head); two-heap
median-of-stream (max-heap for the lower half, min-heap for the upper half, rebalanced after every
insert); heap-based Dijkstra/Prim (a min-heap frontier of `(distance, node)`); Huffman coding
(repeatedly pop the two smallest-frequency nodes and push their merge).
