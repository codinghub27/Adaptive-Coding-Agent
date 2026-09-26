---
title: Trees
pattern: trees
topic: trees
pattern_family: trees
difficulty: E:14 M:24 H:4
aliases: binary tree, binary search tree, tree traversal, lowest common ancestor, bst
identification_signals: TreeNode with left and right children, property computed by combining children's results, lowest common ancestor, level order traversal, in-order traversal gives sorted output
representative_problems: Invert Binary Tree | Easy | https://leetcode.com/problems/invert-binary-tree/ ; Maximum Depth of Binary Tree | Easy | https://leetcode.com/problems/maximum-depth-of-binary-tree/ ; Diameter of Binary Tree | Easy | https://leetcode.com/problems/diameter-of-binary-tree/ ; Validate Binary Search Tree | Medium | https://leetcode.com/problems/validate-binary-search-tree/ ; Lowest Common Ancestor of BST | Medium | https://leetcode.com/problems/lowest-common-ancestor-of-a-binary-search-tree/ ; Binary Tree Level Order Traversal | Medium | https://leetcode.com/problems/binary-tree-level-order-traversal/ ; Binary Tree Maximum Path Sum | Hard | https://leetcode.com/problems/binary-tree-maximum-path-sum/
---

# Trees

## Overview
Trees are hierarchical structures where each node has one parent (except the root) and zero or more children; most interview problems specialize to **binary trees** and **binary search trees** (BSTs, where every node's left subtree is smaller and right subtree is larger). Use tree techniques whenever the problem is naturally recursive on a hierarchical structure: computing a property bottom-up (height, diameter, sum), validating an ordering constraint, or finding relationships between nodes.

## When to Recognize It
Recognize it when the input is explicitly a tree (`TreeNode` with `left`/`right`, or a general `children` list); the problem asks for a property computed by combining children's results ("max depth", "is balanced", "diameter") — a strong signal for post-order recursion; it mentions "lowest common ancestor", "path from root to leaf", or "level order" (level order is `bfs`); or it mentions "binary search tree" / "in-order traversal gives sorted order".

## Core Intuition
Because every subtree is itself a tree, a property of the whole tree can almost always be expressed as a small combination of the same property computed on its children — that's what makes post-order recursion the default shape ("compute my children's answers first, then combine them into mine"). A BST's ordering invariant lets you additionally discard an entire subtree at each step during search, the same elimination idea as `binary_search` but walking a tree instead of an array.

## Identification Signals
- input is a `TreeNode` with `left`/`right`, or a general `children` list
- a property is computed by combining children's results (post-order signal)
- "lowest common ancestor", "path from root to leaf", "level order"
- "binary search tree" or "in-order traversal gives sorted order"

## General Template
```python
from dataclasses import dataclass


@dataclass
class TreeNode:
    val: int
    left: "TreeNode | None" = None
    right: "TreeNode | None" = None


def lowest_common_ancestor(root: TreeNode | None, p: TreeNode, q: TreeNode) -> TreeNode | None:
    """LCA in a general binary tree via post-order recursion."""
    if root is None or root is p or root is q:
        return root
    left = lowest_common_ancestor(root.left, p, q)
    right = lowest_common_ancestor(root.right, p, q)
    if left and right:
        return root
    return left or right
```

## Complexity
Most single-pass tree recursions (height, sum, validate BST, LCA) are O(n) time, visiting each node once, and O(h) space for the recursion stack (O(log n) balanced, O(n) skewed). BST search/insert/delete are O(h) — O(log n) balanced, O(n) worst case unbalanced.

## Common Mistakes
Forgetting the base case `if root is None: return ...`, causing an `AttributeError` on a null child. Confusing traversal orders — in-order gives sorted output *only* for a valid BST. Validating a BST by comparing a node only to its immediate children instead of tracking the full valid `(low, high)` range inherited from ancestors. Using recursion on a tree that could be extremely unbalanced without considering Python's recursion limit.

## When NOT to Use
If the "tree" question is actually about level-by-level distance or shortest number of edges between nodes, treat it as `bfs` over the tree-as-graph rather than reaching for tree-specific recursion. If the structure isn't hierarchical at all — arbitrary connections, possible cycles — it's a general graph problem; see `graphs`.

## Variations
Traversals: pre-order/in-order/post-order (DFS-based) and level-order (BFS-based). BST-specific: O(h) search/insert/delete, in-order traversal for sorted output, kth-smallest via in-order counting. Structural: diameter, balance-checking, serialization/deserialization, lowest common ancestor. Path problems: root-to-leaf path sums, maximum path sum (post-order, tracking best path through vs upward contribution).

## Representative Problems
- [Invert Binary Tree](https://leetcode.com/problems/invert-binary-tree/) — Easy
- [Maximum Depth of Binary Tree](https://leetcode.com/problems/maximum-depth-of-binary-tree/) — Easy
- [Diameter of Binary Tree](https://leetcode.com/problems/diameter-of-binary-tree/) — Easy
- [Validate Binary Search Tree](https://leetcode.com/problems/validate-binary-search-tree/) — Medium
- [Lowest Common Ancestor of BST](https://leetcode.com/problems/lowest-common-ancestor-of-a-binary-search-tree/) — Medium
- [Binary Tree Level Order Traversal](https://leetcode.com/problems/binary-tree-level-order-traversal/) — Medium
- [Binary Tree Maximum Path Sum](https://leetcode.com/problems/binary-tree-maximum-path-sum/) — Hard
