---
title: Trees
pattern: trees
topic: trees
aliases: binary tree, binary search tree, tree traversal, lowest common ancestor, bst
---

# Trees

## When to use
Trees are hierarchical structures where each node has one parent (except the root) and zero or more
children; most interview problems specialize to **binary trees** (at most two children) and
**binary search trees** (BSTs, where every node's left subtree is smaller and right subtree is
larger). Use tree techniques whenever the problem is naturally recursive on a hierarchical
structure: computing a property bottom-up from leaves to root (height, diameter, sum), searching or
validating an ordering constraint (BST validation, BST search in O(log n) on a balanced tree), or
finding relationships between nodes (lowest common ancestor, path sums, serialization).

## Recognition signals
- The input is explicitly a tree (`TreeNode` with `left`/`right`, or a general `children` list).
- The problem asks for a property computed by combining children's results ("max depth", "is
  balanced", "diameter") -- a strong signal for post-order recursion.
- "Lowest common ancestor", "path from root to leaf", "level order" (level order is BFS, see the
  BFS pattern).
- Mentions "binary search tree" or "in-order traversal gives sorted order" -- a hint to exploit the
  BST ordering property directly instead of general tree search.

## Template
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
Most single-pass tree recursions (height, sum, validate BST, LCA in a general binary tree) are O(n)
time, visiting each node once, and O(h) space for the recursion stack where h is the tree height
(O(log n) for a balanced tree, O(n) for a completely skewed one). BST search/insert/delete are
O(h) -- O(log n) balanced, O(n) worst case unbalanced (a self-balancing variant like AVL/red-black
keeps h at O(log n) guaranteed, but those are rarely hand-implemented in interviews).

## Common mistakes
Forgetting the base case `if root is None: return ...`, causing an `AttributeError` on a null child
instead of a clean base-case return. Confusing traversal orders -- in-order (left, node, right)
gives sorted output *only* for a valid BST, not an arbitrary binary tree. Validating a BST by only
comparing a node to its immediate children instead of tracking the full valid `(low, high)` range
inherited from ancestors, which misses violations from a grandparent. Using recursion on a tree that
could be extremely unbalanced/deep without considering Python's recursion limit.

## Variations
Traversals: pre-order/in-order/post-order (DFS-based, recursive or iterative with an explicit
stack) and level-order (BFS-based). BST-specific: O(h) search/insert/delete exploiting the ordering
property, in-order traversal for sorted output, kth-smallest via in-order counting. Structural:
diameter, balance-checking, serialization/deserialization, lowest common ancestor (general binary
tree post-order vs BST's direct ordering-based descent, which is more efficient). Path problems:
root-to-leaf path sums, maximum path sum (post-order, tracking both "best path through this node"
and "best contribution upward").
