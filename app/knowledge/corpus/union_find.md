---
title: Union-Find (Disjoint Set Union)
pattern: union_find
topic: graphs
pattern_family: graphs
difficulty: E:0 M:33 H:9
aliases: dsu, disjoint set union, disjoint-set, connected components, cycle detection undirected
identification_signals: connected components, merge two groups, are these in the same set, cycle in an undirected graph, number of provinces, redundant edge, accounts belong to the same person
representative_problems: Graph Valid Tree | Medium | https://leetcode.com/problems/graph-valid-tree/ ; Redundant Connection | Medium | https://leetcode.com/problems/redundant-connection/ ; Accounts Merge | Medium | https://leetcode.com/problems/accounts-merge/ ; Number of Provinces | Medium | https://leetcode.com/problems/number-of-provinces/
---

# Union-Find (Disjoint Set Union)

## Overview
Union-Find (Disjoint Set Union, DSU) maintains a collection of disjoint sets under two operations:
`find(x)` (which set does x belong to?) and `union(x, y)` (merge x's set and y's set). It solves the
family of problems that ask about grouping, connectivity, or cycles in an **undirected** graph
without ever building an adjacency list or running a traversal — you just process edges/pairs one
at a time and ask "are these already connected?"

## When to Recognize It
The spreadsheet's cue: *"Connected components, cycle detection in undirected graph."* Reach for
Union-Find when the input is a stream or list of pairwise relationships (edges, equations, accounts
sharing an email) rather than an already-built graph, and the question is about grouping or about
whether adding one more relationship creates a cycle — not about paths, distances, or ordering.

## Core Intuition
Each set is represented as a tree; `find` walks parent pointers to the root, and the root is the
set's canonical id. **Union by rank/size** always attaches the smaller tree under the bigger tree's
root, keeping trees shallow. **Path compression** makes every visited node during `find` point
directly at the root, so future lookups are near O(1). Together they make each operation run in
amortized O(α(n)) — effectively constant. A cycle is detected for free: if `find(u) == find(v)`
before you union them, the edge (u, v) connects two nodes already in the same set.

## Identification Signals
- "connected components"
- "are these two nodes/accounts in the same group"
- "merge accounts / merge groups"
- "redundant edge" or "extra edge that creates a cycle"
- "number of provinces / islands of a network"
- "minimum spanning tree"

## General Template
```python
class UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x: int) -> int:
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])  # path compression
        return self.parent[x]

    def union(self, x: int, y: int) -> bool:
        root_x, root_y = self.find(x), self.find(y)
        if root_x == root_y:
            return False  # already connected -> this edge is redundant/a cycle
        if self.rank[root_x] < self.rank[root_y]:
            root_x, root_y = root_y, root_x
        self.parent[root_y] = root_x
        if self.rank[root_x] == self.rank[root_y]:
            self.rank[root_x] += 1
        return True
```

## Complexity
Each `find`/`union` is O(α(n)) amortized, where α is the inverse Ackermann function — effectively
O(1) for any n that fits in memory. Processing m edges is O(m·α(n)) time and O(n) space for the
parent/rank arrays. Without path compression and union by rank, a chain of unions can degrade
`find` to O(n) per call.

## Common Mistakes
Forgetting path compression or union by rank, which lets the tree degenerate into a linked list and
turns every `find` into O(n). Comparing `x == y` instead of `find(x) == find(y)` to test connectivity.
Re-initializing the structure per query instead of reusing it across all edges. Off-by-one errors
when node ids are 1-indexed but the parent array is 0-indexed.

## When NOT to Use
Use Union-Find only for undirected connectivity/grouping questions with no need for path length or
order. If the graph is directed, or you need the shortest path or prerequisite ordering, Union-Find
doesn't apply — reach for `topological_sort` (dependency ordering) or `dijkstra`/BFS (shortest
paths) instead.

## Variations
Weighted Union-Find (parent pointers carry a relative offset, for equations like `a = b * k`);
Union-Find with rollback (for offline queries that must be undone); counting connected components
by tracking the number of distinct roots; Kruskal's MST algorithm, which is Union-Find applied to
edges sorted by weight, skipping any edge that would connect an already-connected pair.

## Representative Problems
- [Graph Valid Tree](https://leetcode.com/problems/graph-valid-tree/) — Medium
- [Number of Connected Components](https://leetcode.com/problems/number-of-connected-components-in-an-undirected-graph/) — Medium
- [Redundant Connection](https://leetcode.com/problems/redundant-connection/) — Medium
- [Accounts Merge](https://leetcode.com/problems/accounts-merge/) — Medium
- [Number of Provinces](https://leetcode.com/problems/number-of-provinces/) — Medium
- [Satisfiability of Equality Equations](https://leetcode.com/problems/satisfiability-of-equality-equations/) — Medium
- [Number of Operations to Make Network Connected](https://leetcode.com/problems/number-of-operations-to-make-network-connected/) — Medium
- [Making a Large Island](https://leetcode.com/problems/making-a-large-island/) — Hard
- [Minimum Spanning Tree – Kruskal](https://leetcode.com/problems/connecting-cities-with-minimum-cost/) — Medium
