---
title: Depth-First Search
pattern: dfs
topic: graph_traversal
pattern_family: graphs
difficulty: E:0 M:33 H:9
aliases: dfs, depth first search, recursive traversal, backtracking traversal
identification_signals: does a path exist, connected components, islands or flood fill, cycle detection, topological order via post-order
representative_problems: Number of Islands | Medium | https://leetcode.com/problems/number-of-islands/ ; Clone Graph | Medium | https://leetcode.com/problems/clone-graph/ ; Max Area of Island | Medium | https://leetcode.com/problems/max-area-of-island/ ; Pacific Atlantic Water Flow | Medium | https://leetcode.com/problems/pacific-atlantic-water-flow/ ; Surrounded Regions | Medium | https://leetcode.com/problems/surrounded-regions/ ; Number of Connected Components | Medium | https://leetcode.com/problems/number-of-connected-components-in-an-undirected-graph/ ; Graph Valid Tree | Medium | https://leetcode.com/problems/graph-valid-tree/
---

# Depth-First Search

## Overview
DFS explores as far as possible along each branch before backtracking, making it the natural tool for exploring *all* paths/states reachable from a start point, detecting cycles, computing connected components, or processing a tree/graph where "go deep first" matches the problem. It's also the traversal underlying backtracking: DFS with pruning and explicit undo of choices. Use DFS over `bfs` when you don't need the *shortest* path and instead need reachability or structural properties.

## When to Recognize It
Recognize it in "does a path exist from A to B" (not "shortest path", which is `bfs`/Dijkstra); counting connected components, islands, or flood-fill style problems; detecting a cycle in a directed or undirected graph; computing a topological order via post-order DFS; or a problem whose natural recursive structure ("combine results of this node's children") suggests recursion.

## Core Intuition
DFS commits fully to one branch before trying the next, using the call stack (or an explicit stack) to remember exactly where to resume once the current branch is exhausted. That "go all the way down, then back up one level" order is precisely what makes post-order results available bottom-up (a node's answer is only computed after all its children's are) and what makes cycle detection possible: a back-edge to a node still on the current stack (not just previously visited) is what makes a graph cyclic.

## Identification Signals
- "does a path exist from A to B" (as opposed to "shortest path")
- counting connected components, islands in a grid, flood-fill
- detecting a cycle in a directed or undirected graph
- computing a topological order via post-order DFS
- a naturally recursive "combine children's results" structure

## General Template
```python
def count_islands(grid: list[list[int]]) -> int:
    """Count connected components of 1s in a grid via iterative DFS."""
    rows, cols = len(grid), len(grid[0])
    visited: set[tuple[int, int]] = set()
    islands = 0
    for r in range(rows):
        for c in range(cols):
            if grid[r][c] == 1 and (r, c) not in visited:
                islands += 1
                stack = [(r, c)]
                visited.add((r, c))
                while stack:
                    cr, cc = stack.pop()
                    for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nr, nc = cr + dr, cc + dc
                        if (
                            0 <= nr < rows
                            and 0 <= nc < cols
                            and grid[nr][nc] == 1
                            and (nr, nc) not in visited
                        ):
                            visited.add((nr, nc))
                            stack.append((nr, nc))
    return islands
```

## Complexity
O(V + E) time for graphs (each vertex and edge visited once) or O(rows * cols) for grids; space is O(V) for the visited set plus O(V) worst case for the recursion/explicit stack. Recursive DFS in Python is bounded by the default recursion limit (~1000), so a deep/skewed graph can raise `RecursionError` — prefer an explicit stack for inputs that might be deep.

## Common Mistakes
Marking a node visited *after* popping it instead of before pushing, which can enqueue the same node multiple times and blow up runtime on cyclic graphs. Using recursion on inputs that can be very deep, hitting Python's recursion limit. Not restoring/undoing state on backtrack when DFS backs combinatorial search, leaving stale state for sibling branches. Confusing DFS's traversal order with BFS's — DFS does *not* give shortest paths in unweighted graphs.

## When NOT to Use
If the problem asks for the shortest path or fewest steps in an unweighted graph, use `bfs` instead — DFS finds *a* path, not the shortest one. If edges carry different weights, DFS's ordering says nothing about cost; use `dijkstra` (non-negative weights) or `bellman_ford` (possible negative weights).

## Variations
Recursive DFS (simplest, risks stack depth); iterative DFS with an explicit stack (safe for deep graphs); pre-order/in-order/post-order variants on trees; DFS for cycle detection (track a "currently in recursion stack" set, distinct from "globally visited"); DFS for topological sort (push to result on post-order, then reverse); DFS as the backbone of `backtracking` (DFS + pruning + choice/undo).

## Representative Problems
- [Number of Islands](https://leetcode.com/problems/number-of-islands/) — Medium
- [Clone Graph](https://leetcode.com/problems/clone-graph/) — Medium
- [Max Area of Island](https://leetcode.com/problems/max-area-of-island/) — Medium
- [Pacific Atlantic Water Flow](https://leetcode.com/problems/pacific-atlantic-water-flow/) — Medium
- [Surrounded Regions](https://leetcode.com/problems/surrounded-regions/) — Medium
- [Number of Connected Components](https://leetcode.com/problems/number-of-connected-components-in-an-undirected-graph/) — Medium
- [Graph Valid Tree](https://leetcode.com/problems/graph-valid-tree/) — Medium
