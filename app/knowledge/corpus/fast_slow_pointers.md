---
title: Fast & Slow Pointers
pattern: fast_slow_pointers
topic: linked_list
pattern_family: two_pointer_window
difficulty: E:8 M:18 H:2
aliases: fast slow pointers, tortoise and hare, floyd cycle detection, linked list cycle
identification_signals: cycle in a linked list, find the middle node, detect a loop, find the duplicate number
representative_problems: Linked List Cycle | Easy | https://leetcode.com/problems/linked-list-cycle/ ; Find the Duplicate Number | Medium | https://leetcode.com/problems/find-the-duplicate-number/ ; Middle of the Linked List | Easy | https://leetcode.com/problems/middle-of-the-linked-list/ ; Reorder List | Medium | https://leetcode.com/problems/reorder-list/
---

# Fast & Slow Pointers

## Overview
Fast and slow pointers (Floyd's tortoise and hare) walk two pointers through a linked list (or
implicit functional graph) at different speeds — typically one step and two steps per iteration —
to find a list's middle, detect a cycle, or locate the entry point of a cycle, all in O(1) extra
space.

## When to Recognize It
The spreadsheet's cue: *"Cycle detection, find middle of a list."* Recognize it whenever a problem
involves a singly linked list (or an array/function treated as an implicit linked structure, as in
Find the Duplicate Number) and asks for a cycle, a loop's starting node, or the midpoint — all
without the O(n) extra space a hash set or length-counting pass would need.

## Core Intuition
If a cycle exists, the fast pointer (moving 2 steps) gains exactly one step on the slow pointer
(moving 1 step) every iteration once both are inside the loop, so it is guaranteed to lap and meet
the slow pointer — a list with no cycle simply lets the fast pointer run off the end first. For the
midpoint: when the fast pointer has traveled to the end (2x distance), the slow pointer, having
traveled exactly half that, is standing at the middle.

## Identification Signals
- "detect a cycle in a linked list"
- "find the node where the cycle begins"
- "find the middle node"
- "find the duplicate number" (array values as a linked structure, `next = nums[i]`)
- constant extra space required (no hash set allowed)

## General Template
```python
class ListNode:
    def __init__(self, val: int = 0, next: "ListNode | None" = None) -> None:
        self.val = val
        self.next = next


def has_cycle(head: ListNode | None) -> bool:
    """Floyd's cycle detection: True if the list loops back on itself."""
    slow = fast = head
    while fast is not None and fast.next is not None:
        slow = slow.next  # type: ignore[union-attr]
        fast = fast.next.next
        if slow is fast:
            return True
    return False
```

## Complexity
O(n) time — the fast pointer traverses at most twice the list length before either exiting or
meeting the slow pointer. O(1) space, which is the entire point of the pattern versus a hash-set
based cycle check.

## Common Mistakes
Checking only `fast is not None` and forgetting `fast.next is not None`, which crashes on
`fast.next.next` when the list has an even length and no cycle. Comparing node *values* (`slow.val ==
fast.val`) instead of node *identity* (`slow is fast`) — values can coincide without it being the
same node. For "find the cycle start," forgetting the second phase (resetting one pointer to `head`
and advancing both one step at a time until they meet again).

## When NOT to Use
If you need to modify or reverse the list (not just detect a property), plain iterative traversal
with previous/current pointers is clearer than dressing it up as fast/slow. If extra O(n) space is
acceptable and simplicity matters more than optimality, a hash set of visited nodes is an easier-to-
read alternative for cycle detection.

## Variations
Finding the cycle's entry node (two-phase: detect meeting point, then advance a reset pointer from
head at the same speed as the pointer left at the meeting point); finding the middle when the list
has even length (decide whether "middle" means the first or second of the two center nodes, based on
when the loop condition stops the slow pointer); applying the same two-speed idea to detect cycles in
an array treated as `next[i] = nums[i]` (Find the Duplicate Number).

## Representative Problems
- [Linked List Cycle](https://leetcode.com/problems/linked-list-cycle/) — Easy
- [Find the Duplicate Number](https://leetcode.com/problems/find-the-duplicate-number/) — Medium
- [Middle of the Linked List](https://leetcode.com/problems/middle-of-the-linked-list/) — Easy
- [Palindrome Linked List](https://leetcode.com/problems/palindrome-linked-list/) — Easy
- [Reorder List](https://leetcode.com/problems/reorder-list/) — Medium
- [Remove Nth Node From End of List](https://leetcode.com/problems/remove-nth-node-from-end-of-list/) — Medium
