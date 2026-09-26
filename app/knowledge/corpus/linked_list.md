---
title: Linked List
pattern: linked_list
topic: linked_list
pattern_family: linked_list
difficulty: E:8 M:18 H:2
aliases: linked list, singly linked list, doubly linked list, pointer manipulation
identification_signals: reverse a linked list, merge two sorted lists, add two numbers, lru cache, copy a list with random pointer
representative_problems: Reverse Linked List | Easy | https://leetcode.com/problems/reverse-linked-list/ ; Merge Two Sorted Lists | Easy | https://leetcode.com/problems/merge-two-sorted-lists/ ; Add Two Numbers | Medium | https://leetcode.com/problems/add-two-numbers/ ; LRU Cache | Medium | https://leetcode.com/problems/lru-cache/
---

# Linked List

## Overview
A linked list is a chain of nodes, each holding a value and a pointer to the next node (and,
for doubly linked lists, the previous one). It solves problems that need O(1) insertion/removal at
an arbitrary position given a reference to it, or that need to rebuild/traverse a sequence node by
node without the contiguous-memory constraints of an array.

## When to Recognize It
Recognize a linked-list problem whenever the input is explicitly given as `ListNode`/`Node` objects,
or the task inherently needs O(1) splicing (removing/inserting a node without shifting everything
after it) — reversing a sequence, merging sorted chains, or implementing a cache that needs O(1)
"move this entry to the front."

## Core Intuition
Every linked-list manipulation reduces to correctly re-pointing a small, fixed set of `.next`
references without losing access to the rest of the chain. The recurring risk is overwriting a
pointer before you've saved what it pointed to — so the standard discipline is: save `node.next`
into a temporary variable *before* reassigning it, whether you're reversing, deleting, or inserting.
A dummy head node sidesteps special-casing "the first node" for insert/delete-at-head operations.

## Identification Signals
- "reverse a linked list" (whole list or a sub-range)
- "merge two/k sorted lists"
- "add two numbers represented as linked lists"
- "copy a list with a random pointer"
- "design an LRU cache" (doubly linked list + hash map)

## General Template
```python
class ListNode:
    def __init__(self, val: int = 0, next: "ListNode | None" = None) -> None:
        self.val = val
        self.next = next


def reverse_list(head: ListNode | None) -> ListNode | None:
    """Reverses a singly linked list in place, returning the new head."""
    previous: ListNode | None = None
    current = head
    while current is not None:
        next_node = current.next  # save before overwriting
        current.next = previous
        previous = current
        current = next_node
    return previous
```

## Complexity
O(n) time for any single traversal-based operation (reverse, merge, find middle). O(1) extra space
for in-place pointer manipulation, versus O(n) if the approach copies values into an array first.
Merging k sorted lists with a heap costs O(N log k) for N total nodes across k lists.

## Common Mistakes
Losing the rest of the list by reassigning `.next` before saving it in a temporary variable. Off-by-
one errors when using a slow/fast pointer to find a node "n from the end" or the exact middle.
Forgetting a dummy head node, which forces awkward special-casing when the head itself needs to be
removed or replaced. Mutating a node's value instead of splicing pointers when the problem expects
true node removal (matters for anything holding external references to nodes).

## When NOT to Use
If the problem needs random access by index or frequent lookups by value, an array or hash map is
the better underlying structure — linked lists only pay off for O(1) splicing given a node
reference. If the task is specifically about a cycle or the list's midpoint, the narrower
`fast_slow_pointers` pattern is the more precise tool to reach for.

## Variations
Doubly linked lists (O(1) removal given only a node reference, no need to track the previous node
separately — used inside LRU Cache); circular linked lists; merging k sorted lists via a min-heap or
divide-and-conquer pairwise merging; in-place reversal of a sub-range (`[left, right]`) rather than
the whole list; deep-copying a list that has an extra `random` pointer using an old-node-to-new-node
hash map.

## Representative Problems
- [Reverse Linked List](https://leetcode.com/problems/reverse-linked-list/) — Easy
- [Merge Two Sorted Lists](https://leetcode.com/problems/merge-two-sorted-lists/) — Easy
- [Add Two Numbers](https://leetcode.com/problems/add-two-numbers/) — Medium
- [Copy List with Random Pointer](https://leetcode.com/problems/copy-list-with-random-pointer/) — Medium
- [LRU Cache](https://leetcode.com/problems/lru-cache/) — Medium
- [Merge k Sorted Lists](https://leetcode.com/problems/merge-k-sorted-lists/) — Hard
- [Reverse Nodes in k-Group](https://leetcode.com/problems/reverse-nodes-in-k-group/) — Hard
- [Sort List](https://leetcode.com/problems/sort-list/) — Medium
