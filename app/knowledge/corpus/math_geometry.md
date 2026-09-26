---
title: Math and Geometry
pattern: math_geometry
topic: math
pattern_family: math_geometry
difficulty: E:3 M:9 H:0
aliases: number theory tricks, matrix manipulation, geometry tricks, modular arithmetic
identification_signals: rotate the matrix in place, spiral traversal, modular arithmetic, prime sieve, big integer arithmetic without overflow
representative_problems: Rotate Image | Medium | https://leetcode.com/problems/rotate-image/ ; Spiral Matrix | Medium | https://leetcode.com/problems/spiral-matrix/ ; Pow(x, n) | Medium | https://leetcode.com/problems/powx-n/ ; Count Primes | Medium | https://leetcode.com/problems/count-primes/
---

# Math and Geometry

## Overview
Math & Geometry is a schedule *topic* rather than one algorithmic pattern: it bundles matrix manipulation (rotation, spiral traversal, zeroing), number theory (primality, modular arithmetic), and ad-hoc arithmetic tricks (string-based big-number multiplication, digit manipulation) that don't fit neatly into a search/DP/graph pattern. What unites them is careful index arithmetic and known closed-form identities rather than a shared recurrence.

## When to Recognize It
Recognize it when a problem is phrased purely in terms of array/matrix indices and arithmetic (rotate, transpose, spiral-walk, zero a row/column); when it asks for a mathematical property of a number (prime, happy number, digit sum); or when built-in arithmetic operators are restricted (implement `pow`, multiply strings, add without `+`) so the operation must be derived from first principles.

## Core Intuition
Most matrix tricks reduce to a coordinate transform: rotating 90 degrees in place is "transpose, then reverse each row," because `matrix[i][j] -> matrix[j][n-1-i]` decomposes into those two simpler operations. Number-theory tricks lean on known invariants — a happy number cycles or reaches 1 under repeated digit-square-sum (detect with a cycle-finder); a sieve marks composites once per prime factor for O(n log log n) primality up to n. The unifying discipline is finding the closed-form identity on paper before writing any loop.

## Identification Signals
- "rotate the matrix in place"
- "spiral order traversal"
- "modular arithmetic"
- "count primes less than n"
- "implement pow(x, n) without built-in exponent"

## General Template
```python
def rotate_image(matrix: list[list[int]]) -> None:
    """Rotate an n x n matrix 90 degrees clockwise, in place."""
    n = len(matrix)
    for i in range(n):
        for j in range(i + 1, n):
            matrix[i][j], matrix[j][i] = matrix[j][i], matrix[i][j]
    for row in matrix:
        row.reverse()
```

## Complexity
Time O(n^2) for an n x n matrix rotation or traversal, since every cell is touched a constant number of times; O(n log log n) for a Sieve of Eratosthenes up to n; O(log n) for fast exponentiation. Space is typically O(1) extra for in-place matrix tricks, O(n) for a sieve's boolean array.

## Common Mistakes
Rotating a matrix by allocating a new one when "in place" is required, wasting the O(1)-space guarantee being tested. Off-by-one on spiral traversal boundaries after each of the four direction changes. Using trial division per number for primality instead of a sieve, asymptotically far worse across a range. Assuming native-language integer overflow rules carry over — Python ints don't overflow, but string-based multiplication problems still expect digit-by-digit simulation rather than casting to int.

## When NOT to Use
If the problem is really a search/DP/graph problem wearing a matrix's clothing (e.g. shortest path on a grid), reach for `bfs`/`dfs`/`dynamic_programming` instead of a pure index-arithmetic trick. If exponentiation is embedded in a recursion with combination logic beyond simple squaring, see `divide_and_conquer`.

## Variations
In-place matrix transforms (rotate, transpose, zero row/column); traversal orders (spiral, diagonal); number theory (primality sieves, happy numbers, digit manipulation); big-number arithmetic without native overflow-free types (multiply strings, add binary); fast exponentiation via repeated squaring.

## Representative Problems
- [Rotate Image](https://leetcode.com/problems/rotate-image/) — Medium
- [Spiral Matrix](https://leetcode.com/problems/spiral-matrix/) — Medium
- [Set Matrix Zeroes](https://leetcode.com/problems/set-matrix-zeroes/) — Medium
- [Happy Number](https://leetcode.com/problems/happy-number/) — Easy
- [Pow(x, n)](https://leetcode.com/problems/powx-n/) — Medium
- [Multiply Strings](https://leetcode.com/problems/multiply-strings/) — Medium
- [Count Primes](https://leetcode.com/problems/count-primes/) — Medium
- [Excel Sheet Column Number](https://leetcode.com/problems/excel-sheet-column-number/) — Easy
