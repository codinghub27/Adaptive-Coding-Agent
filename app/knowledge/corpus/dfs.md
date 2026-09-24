---
title: Depth-First Search
pattern: dfs
topic: graph_traversal
aliases: dfs, depth first search, recursive traversal, backtracking traversal
---

# Depth-First Search

## When to use
DFS explores as far as possible along each branch before backtracking, making it the natural tool
whenever you need to explore *all* paths/states reachable from a start point, detect cycles,
compute connected components, or process a tree/graph in a way where the order "go deep first"
matches the problem (e.g. path existence, counting islands, topological ordering via post-order).
It's also the traversal underlying backtracking: DFS with pruning and explicit undo of choices. Use
DFS over BFS when you don't need the *shortest* path and instead need reachability, exhaustive
enumeration, or structural properties like cycles and connected components.

## Recognition signals
- "Does a path exist from A to B" (not "what is the shortest path", which is BFS/Dijkstra).
- Counting connected components, islands in a grid, or flood-fill style problems.
- Detecting a cycle in a directed or undirected graph.
- Computing a topological order via post-order DFS.
- Exploring a tree's structure recursively (subtree properties, ancestor/descendant relationships).
- The problem's natural recursive structure ("solve for this node, combining results of its
  children") suggests recursion, which is DFS on the implicit call tree.

## Template
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
O(V + E) time for graphs (each vertex and edge visited once) or O(rows * cols) for grids; space is
O(V) for the visited set plus O(V) worst case for the recursion stack or explicit stack (a long
skinny graph can recurse as deep as it has vertices). Recursive DFS in Python is bounded by the
default recursion limit (~1000), so a deep/skewed graph (e.g. a long linked-list-like chain) can
raise `RecursionError` -- prefer an explicit stack (iterative DFS) for inputs that might be deep.

## Common mistakes
Forgetting to mark a node visited *before* recursing/pushing (rather than when popped), which can
enqueue/visit the same node multiple times and blow up runtime or cause infinite loops on cyclic
graphs. Using recursion on inputs that can be very deep, hitting Python's recursion limit -- switch
to an explicit stack. Not restoring/undoing state on backtrack when DFS is being used for
combinatorial search (leaves stale state for sibling branches). Confusing DFS's traversal order
guarantees with BFS's -- DFS does *not* give shortest paths in unweighted graphs.

## Variations
Recursive DFS (simplest to write, risks stack depth); iterative DFS with an explicit stack (safe
for deep graphs); pre-order/in-order/post-order variants on trees; DFS for cycle detection (track a
"currently in recursion stack" set, distinct from "globally visited", to catch back-edges in
directed graphs); DFS for topological sort (push to result on post-order, then reverse); DFS as the
backbone of backtracking (add DFS + pruning + choice/undo = backtracking).
