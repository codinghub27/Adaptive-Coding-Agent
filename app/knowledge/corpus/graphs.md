---
title: Graph Algorithms
pattern: graphs
topic: graphs
pattern_family: graphs
difficulty: E:0 M:33 H:9
aliases: graph theory, adjacency list, adjacency matrix, edge list, nodes and edges, graph modelling
identification_signals: adjacency list representation, nodes and edges reframing, model this as a graph, build the graph from an edge list, which graph algorithm applies
representative_problems: Course Schedule | Medium | https://leetcode.com/problems/course-schedule/ ; Course Schedule II | Medium | https://leetcode.com/problems/course-schedule-ii/ ; Graph Valid Tree | Medium | https://leetcode.com/problems/graph-valid-tree/ ; Redundant Connection | Medium | https://leetcode.com/problems/redundant-connection/ ; Network Delay Time | Medium | https://leetcode.com/problems/network-delay-time/ ; Is Graph Bipartite? | Medium | https://leetcode.com/problems/is-graph-bipartite/ ; Alien Dictionary | Hard | https://leetcode.com/problems/alien-dictionary/
---

# Graph Algorithms

## Overview
This is the umbrella pattern for the general graph toolbox beyond plain traversal: representing a graph, ordering nodes under dependencies, finding connected structure without traversal, and shortest paths with weighted edges. For plain reachability/enumeration use `dfs`; for shortest paths in unweighted graphs use `bfs`. For the specialized techniques below, see the dedicated docs: `topological_sort`, `union_find`, `dijkstra`, `bellman_ford`.

## When to Recognize It
"Course prerequisites", "build order", "can these tasks be completed" point to `topological_sort` (a valid order exists iff the graph is a DAG). "Are these two elements connected", "number of connected components", "will adding this edge create a cycle" point to `union_find`. "Shortest/cheapest path" with non-negative weighted edges points to `dijkstra`; with possible negative edges, `bellman_ford`. Any problem stated on "nodes" and "edges" (or reframable that way) is a candidate.

## Core Intuition
An **adjacency list** (`dict[node, list[neighbor]]`) is the default representation because most graph algorithms only ever need "what are this node's neighbors", which it answers in O(degree) — an adjacency matrix's O(1) edge-existence check is rarely worth its O(V^2) space for the sparse graphs interview problems typically use. Each specialized algorithm below exploits one structural fact about the problem (a DAG's dependency order, a union-find's near-constant merge, an edge-relaxation invariant) to avoid brute-force pairwise comparison.

## Identification Signals
- "course prerequisites", "build order", "can these tasks be completed"
- "are these two elements connected/in the same group", "number of connected components"
- "will adding this edge create a cycle"
- "shortest/cheapest path" with weighted edges (non-negative or possibly negative)
- any problem stated on, or reframable onto, "nodes" and "edges"

## General Template
The umbrella skill is *building the graph and picking the right algorithm* —
each algorithm has its own doc with its own template.

```python
from collections import defaultdict


def build_adjacency(
    num_nodes: int, edges: list[tuple[int, int]], *, directed: bool
) -> dict[int, list[int]]:
    """The step almost every graph problem starts with: turn an edge list into
    an adjacency map. Then dispatch on what the question asks for --
    `bfs` (fewest edges), `dfs` (reachability/components),
    `topological_sort` (ordering), `union_find` (connectivity),
    `dijkstra` (weighted, non-negative), `bellman_ford` (negative or
    at-most-K-edges)."""
    graph: dict[int, list[int]] = defaultdict(list)
    for node in range(num_nodes):
        graph[node] = []
    for u, v in edges:
        graph[u].append(v)
        if not directed:
            graph[v].append(u)
    return dict(graph)
```


## Complexity
Kahn's topological sort: O(V + E) time, O(V) space. Union-find with path compression and union by rank: each operation is O(alpha(n)) amortized, practically constant. Dijkstra with a binary heap: O((V + E) log V) time, O(V) space. Bellman-Ford (negative edges, detects negative cycles): O(V * E) time. Floyd-Warshall (all-pairs): O(V^3) time, O(V^2) space.

## Common Mistakes
Using an adjacency matrix for a large sparse graph, wasting O(V^2) memory. Not detecting the cycle case in topological sort (output order shorter than the graph) and silently returning a partial/wrong order. Implementing union-find without path compression or union by rank, degrading to O(n) per operation. Running Dijkstra's relaxation logic on a graph with negative edges, which gives wrong answers with no warning.

## When NOT to Use
If you only need reachability or exhaustive enumeration with no dependency ordering, weights, or connectivity-merging involved, plain `dfs` or `bfs` is simpler and doesn't need any of this specialized machinery. If the "graph" is actually a tree (no cycles, one path between any two nodes), the `trees` doc's traversal techniques are more direct than general graph algorithms.

## Variations
Adjacency list vs matrix representation; Kahn's (BFS-based) vs DFS-based (post-order + reverse) topological sort; union-find with path compression + union by rank; Dijkstra vs Bellman-Ford vs Floyd-Warshall; minimum spanning tree via Kruskal's (union-find driven) or Prim's (heap-driven); bipartite checking via two-coloring during BFS/DFS.

## Representative Problems
- [Course Schedule](https://leetcode.com/problems/course-schedule/) — Medium
- [Course Schedule II](https://leetcode.com/problems/course-schedule-ii/) — Medium
- [Graph Valid Tree](https://leetcode.com/problems/graph-valid-tree/) — Medium
- [Redundant Connection](https://leetcode.com/problems/redundant-connection/) — Medium
- [Network Delay Time](https://leetcode.com/problems/network-delay-time/) — Medium
- [Is Graph Bipartite?](https://leetcode.com/problems/is-graph-bipartite/) — Medium
- [Alien Dictionary](https://leetcode.com/problems/alien-dictionary/) — Hard
