---
title: Breadth-First Search
pattern: bfs
topic: graph_traversal
aliases: bfs, breadth first search, shortest path unweighted, level order traversal
---

# Breadth-First Search

## When to use
BFS explores a graph or tree level by level using a FIFO queue, visiting every node at distance k
from the source before any node at distance k+1. This makes it the go-to algorithm whenever a
problem asks for the **shortest path or minimum number of steps** in an unweighted graph (or a
graph where every edge has equal cost) -- grid shortest-path, word ladder, minimum knight moves,
minimum number of operations to reach a target state. It's also used for level-order tree traversal
and for finding the shortest path in implicit graphs where states are generated on the fly (e.g.
BFS over game states).

## Recognition signals
- "Shortest path", "minimum number of steps/moves", "fewest operations" in an unweighted setting.
- Level-order traversal of a tree, or "group nodes by depth/distance".
- Multi-source shortest distance (e.g. rotting oranges: start BFS from all sources at once).
- The state space is implicit (not a literal graph) but has clear transitions between states, and
  you need the minimum number of transitions.

## Template
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
O(V + E) time for graphs (each vertex enqueued/dequeued once, each edge examined once) or
O(rows * cols) for grids. Space is O(V) for the queue and visited set in the worst case (a wide
graph can have an entire level in the queue at once). Compared to DFS, BFS uses an explicit queue
rather than the call stack, so it doesn't risk Python's recursion limit on deep graphs -- but it can
use more memory on wide, shallow ones.

## Common mistakes
Marking a node visited when it's *dequeued* rather than when it's *enqueued*, which lets the same
node be added to the queue multiple times from different neighbors, wasting work and occasionally
causing incorrect distances. Using BFS when the graph is weighted (with differing edge costs) --
plain BFS only gives shortest paths when all edges cost the same; use Dijkstra otherwise.
Forgetting to check the goal condition before or as soon as a node is dequeued (checking too late
does extra unnecessary expansion). Not seeding a multi-source BFS with *all* sources at distance 0
up front.

## Variations
Single-source shortest path in unweighted graphs; multi-source BFS (seed the queue with multiple
starting nodes at distance 0, e.g. rotting oranges, walls-and-gates); level-order tree traversal
(process one queue-length "layer" at a time to group by depth); bidirectional BFS (search from both
start and end simultaneously, meeting in the middle, useful when the branching factor is large);
0-1 BFS (deque-based variant for graphs with only 0 and 1 edge weights, pushing 0-weight edges to
the front).
