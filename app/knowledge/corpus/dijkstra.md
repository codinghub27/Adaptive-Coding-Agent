---
title: Dijkstra's Algorithm
pattern: dijkstra
topic: graphs
pattern_family: graphs
difficulty: E:0 M:33 H:9
aliases: dijkstra, shortest path non-negative weights, weighted bfs, priority queue shortest path
identification_signals: shortest path with weights, minimum cost to reach, cheapest route, network delay time, no negative edges
representative_problems: Network Delay Time | Medium | https://leetcode.com/problems/network-delay-time/ ; Cheapest Flights Within K Stops | Medium | https://leetcode.com/problems/cheapest-flights-within-k-stops/ ; Path with Minimum Effort | Medium | https://leetcode.com/problems/path-with-minimum-effort/ ; Swim in Rising Water | Hard | https://leetcode.com/problems/swim-in-rising-water/
---

# Dijkstra's Algorithm

## Overview
Dijkstra's algorithm finds the shortest distance from a single source to every other node in a
weighted graph whose edge weights are **all non-negative**. It generalizes BFS: instead of a plain
FIFO queue (which assumes every edge costs 1), it uses a min-heap keyed by distance-so-far, always
expanding the currently-closest unvisited node next.

## When to Recognize It
The spreadsheet's cue: *"Shortest path with weights, no negative edges."* Reach for Dijkstra when
edges have differing positive costs (travel time, price, effort) and you need the minimum total cost
from one node to one or all others. If the problem explicitly allows negative edge weights, this is
the wrong tool.

## Core Intuition
Once a node is popped from the min-heap with its final distance, that distance can never improve —
every other path to it would have to go through a node that's already farther away (since all
remaining edges are non-negative, no shortcut can make a longer prefix shorter). This "settle once,
never revisit" greedy property is exactly what breaks under negative weights, where a longer prefix
could still lead to a cheaper total via a negative edge later.

## Identification Signals
- "shortest path" with weighted edges
- "minimum cost to reach" a target
- "cheapest flights" / "network delay time"
- "minimum effort path" (weight = max edge on path, not sum — same relaxation idea)
- guaranteed non-negative costs

## General Template
```python
import heapq


def dijkstra(n: int, graph: list[list[tuple[int, int]]], source: int) -> list[float]:
    """`graph[u]` is a list of (neighbor, weight). Returns shortest distance
    from `source` to every node (float('inf') if unreachable)."""
    dist: list[float] = [float("inf")] * n
    dist[source] = 0
    heap: list[tuple[int, int]] = [(0, source)]
    while heap:
        d, node = heapq.heappop(heap)
        if d > dist[node]:
            continue  # stale entry, a shorter path was already found
        for neighbor, weight in graph[node]:
            new_dist = d + weight
            if new_dist < dist[neighbor]:
                dist[neighbor] = new_dist
                heapq.heappush(heap, (new_dist, neighbor))
    return dist
```

## Complexity
O((V + E) log V) with a binary heap — each edge can push a new heap entry (O(log V)) and each node
is popped at most as many times as it's pushed. Space is O(V + E) for the adjacency list and
distance array, plus O(E) for the heap in the worst case.

## Common Mistakes
Running Dijkstra on a graph with negative edges — it produces silently wrong answers instead of
erroring. Forgetting the `d > dist[node]: continue` stale-entry check, which still works correctly
but wastes time re-processing outdated heap entries. Using a plain list instead of a heap for the
"find minimum unvisited" step, which degrades to O(V^2). Confusing "shortest path" with "minimum
bottleneck along a path" — the latter needs a modified relaxation (`max` instead of `+`).

## When NOT to Use
If the graph has negative edge weights, use `bellman_ford` instead. If every edge has the same cost
(unweighted), plain BFS is simpler and faster. If you need all-pairs shortest paths on a small dense
graph, Floyd-Warshall (O(V^3)) is often simpler to write than running Dijkstra from every node.

## Variations
Dijkstra with a state that includes extra constraints (e.g. "at most K stops" — track (node, stops
used) as the visited key); "minimum effort"/minimax-path Dijkstra (relax with `max(d, weight)`
instead of `d + weight`); 0-1 BFS (deque-based shortcut when weights are only 0 or 1); A* search
(Dijkstra plus a heuristic that never overestimates true distance).

## Representative Problems
- [Network Delay Time](https://leetcode.com/problems/network-delay-time/) — Medium
- [Cheapest Flights Within K Stops](https://leetcode.com/problems/cheapest-flights-within-k-stops/) — Medium
- [Path with Minimum Effort](https://leetcode.com/problems/path-with-minimum-effort/) — Medium
- [Swim in Rising Water](https://leetcode.com/problems/swim-in-rising-water/) — Hard
- [Find the City with Smallest Neighbors](https://leetcode.com/problems/find-the-city-with-the-smallest-number-of-neighbors-at-a-threshold-distance/) — Medium
- [Minimum Cost to Reach Destination](https://leetcode.com/problems/minimum-cost-to-reach-destination-in-time/) — Hard
