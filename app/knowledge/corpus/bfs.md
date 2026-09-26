---
title: Breadth-First Search
pattern: bfs
topic: graph_traversal
pattern_family: graphs
difficulty: E:0 M:33 H:9
aliases: bfs, breadth first search, shortest path unweighted, level order traversal
identification_signals: shortest path in unweighted graph, minimum number of steps or operations, level order traversal, multi-source shortest distance, implicit state graph
representative_problems: Rotting Oranges | Medium | https://leetcode.com/problems/rotting-oranges/ ; Walls and Gates | Medium | https://leetcode.com/problems/walls-and-gates/ ; Word Ladder | Hard | https://leetcode.com/problems/word-ladder/ ; Course Schedule II | Medium | https://leetcode.com/problems/course-schedule-ii/ ; Shortest Path in Binary Matrix | Medium | https://leetcode.com/problems/shortest-path-in-binary-matrix/ ; Snakes and Ladders | Medium | https://leetcode.com/problems/snakes-and-ladders/ ; Open the Lock | Medium | https://leetcode.com/problems/open-the-lock/
---

# Breadth-First Search

## Overview
BFS explores a graph or tree level by level using a FIFO queue, visiting every node at distance k from the source before any node at distance k+1. This makes it the go-to algorithm whenever a problem asks for the **shortest path or minimum number of steps** in an unweighted graph — grid shortest-path, word ladder, minimum knight moves. It's also used for level-order tree traversal and shortest paths in implicit graphs where states are generated on the fly.

## When to Recognize It
Recognize it in "shortest path", "minimum number of steps/moves", "fewest operations" phrased over an unweighted setting; level-order traversal of a tree or "group nodes by depth/distance"; multi-source shortest distance (rotting oranges: start from all sources at once); or a state space that's implicit (not a literal graph) but has clear transitions, needing the minimum number of transitions.

## Core Intuition
Processing the queue strictly in FIFO order guarantees every node at distance k is dequeued (and its neighbors discovered) before any node at distance k+1 is ever enqueued. That invariant is exactly what makes the first time a node is reached also its shortest distance from the source — there is no way to reach it "later but cheaper" once every closer node has already been fully explored.

## Identification Signals
- "shortest path", "minimum number of steps/moves", "fewest operations" (unweighted)
- level-order traversal, or "group nodes by depth/distance"
- multi-source shortest distance (seed the queue with all sources at once)
- an implicit state space with clear transitions, needing the minimum number of them

## General Template
```python
from collections import deque


def shortest_path_grid(grid: list[list[int]], start: tuple[int, int], end: tuple[int, int]) -> int:
    """Shortest number of steps from start to end through open (0) cells."""
    rows, cols = len(grid), len(grid[0])
    queue: deque[tuple[int, int, int]] = deque([(*start, 0)])
    visited = {start}
    while queue:
        r, c, dist = queue.popleft()
        if (r, c) == end:
            return dist
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols and grid[nr][nc] == 0 and (nr, nc) not in visited:
                visited.add((nr, nc))
                queue.append((nr, nc, dist + 1))
    return -1
```

## Complexity
O(V + E) time for graphs (each vertex/edge examined once) or O(rows * cols) for grids. Space is O(V) for the queue and visited set in the worst case. BFS uses an explicit queue rather than the call stack, so it doesn't risk Python's recursion limit on deep graphs, but it can use more memory on wide, shallow ones.

## Common Mistakes
Marking a node visited when it's *dequeued* rather than *enqueued*, letting the same node enter the queue multiple times from different neighbors. Using BFS when edges have differing costs — plain BFS only gives shortest paths when every edge costs the same; use `dijkstra` otherwise. Forgetting to check the goal condition as soon as a node is dequeued. Not seeding a multi-source BFS with *all* sources at distance 0 up front.

## When NOT to Use
If edges have weights (even all-positive but unequal), BFS's level-by-level guarantee no longer gives the shortest path — use `dijkstra`. If the problem needs *all* paths, or reachability/structural properties (cycles, components) rather than the shortest one, plain `dfs` is simpler and uses less memory on wide graphs.

## Variations
Single-source shortest path in unweighted graphs; multi-source BFS (seed multiple starting nodes at distance 0); level-order tree traversal (process one queue-length "layer" at a time); bidirectional BFS (search from both ends, meeting in the middle); 0-1 BFS (deque-based variant for graphs with only 0/1 edge weights, pushing 0-weight edges to the front).

## Representative Problems
- [Rotting Oranges](https://leetcode.com/problems/rotting-oranges/) — Medium
- [Walls and Gates](https://leetcode.com/problems/walls-and-gates/) — Medium
- [Word Ladder](https://leetcode.com/problems/word-ladder/) — Hard
- [Course Schedule II](https://leetcode.com/problems/course-schedule-ii/) — Medium
- [Shortest Path in Binary Matrix](https://leetcode.com/problems/shortest-path-in-binary-matrix/) — Medium
- [Snakes and Ladders](https://leetcode.com/problems/snakes-and-ladders/) — Medium
- [Open the Lock](https://leetcode.com/problems/open-the-lock/) — Medium
