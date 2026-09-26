---
title: Topological Sort
pattern: topological_sort
topic: graphs
pattern_family: graphs
difficulty: E:0 M:33 H:9
aliases: topo sort, dependency ordering, kahn's algorithm, dag ordering
identification_signals: prerequisites, course schedule, must finish before, build order, dependency graph, valid ordering
representative_problems: Course Schedule | Medium | https://leetcode.com/problems/course-schedule/ ; Course Schedule II | Medium | https://leetcode.com/problems/course-schedule-ii/ ; Alien Dictionary | Hard | https://leetcode.com/problems/alien-dictionary/ ; Parallel Courses | Medium | https://leetcode.com/problems/parallel-courses/
---

# Topological Sort

## Overview
Topological sort produces a linear ordering of a **directed acyclic graph's (DAG)** nodes such that
every edge `u -> v` places `u` before `v` in the ordering. It solves scheduling problems: given a
set of tasks with "must happen before" constraints, find a valid execution order — or detect that
no valid order exists because the constraints form a cycle.

## When to Recognize It
The spreadsheet's cue: *"Dependency ordering, course prerequisites."* Recognize it whenever the
problem states pairwise "X must come before Y" constraints (prerequisites, build steps, alien
dictionary letter order) and asks for a valid overall sequence, whether all tasks can be completed,
or the letters/items ranked by that partial order.

## Core Intuition
A node can safely be placed in the output only once **all of its prerequisites are already placed**
— that's exactly what in-degree tracks. Kahn's algorithm repeatedly removes nodes with in-degree 0
(no remaining unmet prerequisite), decrementing the in-degree of their neighbors as it does. If every
node gets removed, the order is valid; if some remain stuck with in-degree > 0, they're part of a
cycle and no valid order exists. The DFS variant reaches the same conclusion by post-order: a node is
prepended to the result only after all its dependents have been fully explored.

## Identification Signals
- "prerequisites" / "course schedule"
- "must be completed before"
- "build order" / "task scheduling with dependencies"
- "return any valid order" or "is it possible to finish all"
- "alien dictionary" / letter ordering from sorted words

## General Template
```python
from collections import deque


def topological_order(num_nodes: int, edges: list[tuple[int, int]]) -> list[int]:
    """Kahn's algorithm. `edges` are (prerequisite, dependent) pairs.
    Returns [] if the graph has a cycle (no valid ordering)."""
    graph: list[list[int]] = [[] for _ in range(num_nodes)]
    in_degree = [0] * num_nodes
    for prereq, dependent in edges:
        graph[prereq].append(dependent)
        in_degree[dependent] += 1

    queue = deque(node for node in range(num_nodes) if in_degree[node] == 0)
    order: list[int] = []
    while queue:
        node = queue.popleft()
        order.append(node)
        for neighbor in graph[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    return order if len(order) == num_nodes else []
```

## Complexity
O(V + E) time — each node enters the queue once and each edge is examined once when decrementing
in-degrees. O(V + E) space for the adjacency list and in-degree array. Identical complexity for the
DFS-with-post-order variant.

## Common Mistakes
Forgetting to check `len(order) == num_nodes` at the end, which silently returns a partial order
instead of detecting a cycle. Building the edge direction backwards (prerequisite should point to
the course that depends on it, not the reverse). Using plain DFS without cycle detection (a
three-color/visiting-state check), which can infinite-loop on a cyclic graph.

## When NOT to Use
If the graph is undirected, topological sort is undefined — use `union_find` or BFS/DFS for
connectivity instead. If edges carry weights and you need shortest distances rather than an
ordering, use `dijkstra` or `bellman_ford`. If the graph has no dependency structure at all, plain
BFS/DFS traversal is enough.

## Variations
Lexicographically smallest topological order (use a min-heap instead of a plain queue so ties break
by node id); detecting *all* valid orderings (backtracking over the DAG); "parallel courses" style
problems that ask for the minimum number of rounds (BFS layer-by-layer, one round per queue-drain);
DFS-based topological sort using post-order and reversing the result.

## Representative Problems
- [Course Schedule](https://leetcode.com/problems/course-schedule/) — Medium
- [Course Schedule II](https://leetcode.com/problems/course-schedule-ii/) — Medium
- [Alien Dictionary](https://leetcode.com/problems/alien-dictionary/) — Hard
- [Find Eventual Safe States](https://leetcode.com/problems/find-eventual-safe-states/) — Medium
- [Minimum Height Trees](https://leetcode.com/problems/minimum-height-trees/) — Medium
- [Parallel Courses](https://leetcode.com/problems/parallel-courses/) — Medium
