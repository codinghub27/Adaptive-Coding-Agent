---
title: Graph Algorithms
pattern: graphs
topic: graphs
aliases: graph theory, adjacency list, topological sort, union find, dijkstra, shortest path
---

# Graph Algorithms

## When to use
This pattern covers the general graph toolbox beyond plain DFS/BFS traversal: representing a graph
efficiently, ordering nodes under dependency constraints, finding connected structure without
traversal, and finding shortest paths with weighted edges. Use an **adjacency list**
(`dict[node, list[neighbor]]`) as the default representation -- it's O(V + E) space and lets you
iterate a node's neighbors in O(degree) time, versus O(V^2) space for an adjacency matrix (which is
only worth it for dense graphs or O(1) edge-existence checks). Reach for **topological sort** when
nodes have "must come before" dependencies (course scheduling, build order). Reach for
**union-find** when you need to repeatedly ask "are these two nodes connected" or merge groups,
without needing full path info. Reach for **Dijkstra** when edges have non-negative weights and you
need shortest paths from a source.

## Recognition signals
- "Course prerequisites", "build order", "can these tasks be completed" -> topological sort
  (detects cycles as a side effect: a valid topological order exists iff the graph is a DAG).
- "Are these two elements connected/in the same group", "number of connected components", "will
  adding this edge create a cycle" -> union-find (disjoint set union).
- "Shortest/cheapest path" with weighted, non-negative edges -> Dijkstra; with possible negative
  edges -> Bellman-Ford.
- Any problem stated on "nodes" and "edges", or reframable that way (word ladder, course
  dependencies, network connections).

## Template
```python
from collections import deque


def topological_sort(graph: dict[int, list[int]]) -> list[int]:
    """Kahn's algorithm: order nodes so every edge points forward."""
    in_degree: dict[int, int] = dict.fromkeys(graph, 0)
    for neighbors in graph.values():
        for v in neighbors:
            in_degree[v] = in_degree.get(v, 0) + 1
    queue = deque(n for n, deg in in_degree.items() if deg == 0)
    order: list[int] = []
    while queue:
        node = queue.popleft()
        order.append(node)
        for neighbor in graph.get(node, []):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)
    if len(order) != len(in_degree):
        raise ValueError("graph has a cycle")
    return order
```

## Complexity
Kahn's topological sort: O(V + E) time, O(V) space. Union-find with path compression and union by
rank: each operation is O(alpha(n)) amortized (effectively constant, where alpha is the inverse
Ackermann function), so m operations cost O(m * alpha(n)), practically linear. Dijkstra with a
binary heap: O((V + E) log V) time, O(V) space for the heap and distance array. Bellman-Ford
(handles negative edges, detects negative cycles): O(V * E) time, O(V) space. Floyd-Warshall
(all-pairs shortest paths): O(V^3) time, O(V^2) space.

## Common mistakes
Using an adjacency matrix for a large sparse graph, wasting O(V^2) memory when O(V + E) would do.
Forgetting that topological sort only exists for DAGs -- not detecting the cycle case (when the
output order has fewer nodes than the graph) and silently returning a partial/wrong order.
Implementing union-find without path compression or union by rank, degrading to O(n) per operation
on adversarial inputs. Using plain BFS/Dijkstra's relaxation logic interchangeably -- Dijkstra
requires non-negative weights; running it on a graph with negative edges gives wrong answers without
any warning.

## Variations
Adjacency list vs matrix representation; Kahn's (BFS-based) vs DFS-based (post-order + reverse)
topological sort; union-find with path compression + union by rank; Dijkstra (non-negative weights)
vs Bellman-Ford (handles negative weights, detects negative cycles) vs Floyd-Warshall (all-pairs
shortest paths); minimum spanning tree via Kruskal's (union-find driven, sort edges then greedily
add ones that don't form a cycle) or Prim's (heap-driven, greedily grows a single tree from an
arbitrary start vertex). Bipartite checking via two-coloring during BFS/DFS is another common
extension of the same traversal machinery.
