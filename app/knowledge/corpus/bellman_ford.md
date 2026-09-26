---
title: Bellman-Ford Algorithm
pattern: bellman_ford
topic: graphs
pattern_family: graphs
difficulty: E:0 M:33 H:9
aliases: bellman-ford, shortest path negative edges, k-edge relaxation, bounded-hop shortest path
identification_signals: shortest path with negative edges, at most k stops, at most k edges, detect negative cycle
representative_problems: Cheapest Flights Within K Stops | Medium | https://leetcode.com/problems/cheapest-flights-within-k-stops/ ; Network Delay Time | Medium | https://leetcode.com/problems/network-delay-time/ ; Minimum Cost to Reach Destination | Hard | https://leetcode.com/problems/minimum-cost-to-reach-destination-in-time/
---

# Bellman-Ford Algorithm

## Overview
Bellman-Ford computes shortest distances from a single source in a weighted graph that may contain
**negative edge weights**, and it can detect negative-weight cycles. It works by relaxing every edge
repeatedly instead of greedily settling the closest node like Dijkstra does, which is what lets it
tolerate edges that temporarily make a path look longer before a later negative edge makes it
cheaper overall.

## When to Recognize It
The spreadsheet's cue: *"Shortest path with negative edges."* Reach for it when weights can be
negative, or — just as commonly on LeetCode — when the problem caps the number of edges/hops the
path may use (e.g. "at most K stops"), since Bellman-Ford's edge-relaxation rounds map directly onto
"at most K edges" by simply stopping after K rounds.

## Core Intuition
After relaxing every edge once, all shortest paths using **at most 1 edge** are correct. After
relaxing every edge a second full pass, all shortest paths using **at most 2 edges** are correct —
and so on. Since any simple shortest path in a graph with V nodes uses at most V-1 edges, V-1 full
passes guarantee convergence. This "distance after exactly i rounds" invariant is exactly what makes
the algorithm a natural fit for "at most K stops/edges" constraints: just stop at round K instead of
V-1.

## Identification Signals
- "shortest path" with possible negative weights
- "at most K stops" / "at most K edges" / "within K stops"
- "detect a negative cycle"
- arbitrage-style problems (currency exchange with a profitable cycle)

## General Template
```python
def bellman_ford(
    n: int, edges: list[tuple[int, int, int]], source: int, max_edges: int
) -> list[float]:
    """`edges` are (u, v, weight). Relaxes for `max_edges` rounds so distances
    reflect paths using at most that many edges (pass n - 1 for the classic
    all-pairs-reachable version)."""
    dist: list[float] = [float("inf")] * n
    dist[source] = 0
    for _ in range(max_edges):
        updated = dist[:]
        for u, v, weight in edges:
            if dist[u] != float("inf") and dist[u] + weight < updated[v]:
                updated[v] = dist[u] + weight
        dist = updated
    return dist
```

## Complexity
O(V · E) time — up to V-1 rounds, each examining every edge once. O(V) space for the distance array
(O(V) extra for the per-round copy used to avoid relaxing with an already-updated-this-round value).
Slower than Dijkstra's O((V+E) log V) on graphs where Dijkstra is legal, which is exactly why
Bellman-Ford is reserved for negative weights or explicit hop limits.

## Common Mistakes
Relaxing edges using the array being updated in-place within the same round, which can let a single
round chain multiple hops together and violate the "distance after i rounds" invariant — copy the
distance array (or process a round's updates from a snapshot) before writing. Running V-1 full
rounds when the problem actually wants a hop cap of K < V-1 (or vice versa). Forgetting that a graph
with a negative cycle has no well-defined shortest path — a Vth round that still finds an improvement
signals one.

## When NOT to Use
If all edge weights are non-negative and there's no hop cap, `dijkstra` reaches the same answer
faster. If you need all-pairs shortest paths on a small dense graph, Floyd-Warshall (O(V^3)) is
simpler to write than running Bellman-Ford from every source.

## Variations
SPFA (queue-based optimization that skips rounds with no updates, faster in practice though same
worst case); negative-cycle detection (run one extra round — any further improvement means a
negative cycle exists); bounded-hop shortest path (stop after exactly K rounds instead of V-1, the
"cheapest flights within K stops" framing).

## Representative Problems
- [Cheapest Flights Within K Stops](https://leetcode.com/problems/cheapest-flights-within-k-stops/) — Medium
- [Network Delay Time](https://leetcode.com/problems/network-delay-time/) — Medium
- [Minimum Cost to Reach Destination](https://leetcode.com/problems/minimum-cost-to-reach-destination-in-time/) — Hard
