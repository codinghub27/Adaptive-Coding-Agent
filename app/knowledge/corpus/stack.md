---
title: Stack
pattern: stack
topic: stacks
pattern_family: stack
difficulty: E:2 M:10 H:2
aliases: stack, lifo, last in first out, valid parentheses, expression evaluation
identification_signals: valid parentheses, matching brackets, undo the last operation, evaluate an expression, nested structure
representative_problems: Valid Parentheses | Easy | https://leetcode.com/problems/valid-parentheses/ ; Min Stack | Medium | https://leetcode.com/problems/min-stack/ ; Evaluate Reverse Polish Notation | Medium | https://leetcode.com/problems/evaluate-reverse-polish-notation/ ; Decode String | Medium | https://leetcode.com/problems/decode-string/
---

# Stack

## Overview
A stack is a last-in-first-out (LIFO) structure: push adds to the top, pop removes from the top, and
nothing in the middle is directly reachable. It's the natural fit for any problem built around
**matching, nesting, or "the most recent thing"** — balanced brackets, expression evaluation,
undo history, and recursive-structure processing done iteratively.

## When to Recognize It
Recognize a stack problem when the input has a nested or sequential structure where resolving the
current item depends on the *most recently seen, still-unresolved* earlier item — an open bracket
waiting for its matching close, an operator waiting for its operands, a decode-string group waiting
to be repeated. If instead you need the next item in a monotonic order relative to future elements,
that's the narrower `monotonic_stack` pattern.

## Core Intuition
LIFO order mirrors nesting: the innermost, most recently opened structure must be the first one
closed. Pushing "defers" a decision until enough context arrives to resolve it, and popping resolves
exactly the most recent deferred decision — which is always the correct one to resolve first because
any structure opened after it must close before it (that's what "nested" means).

## Identification Signals
- "valid parentheses" / "matching brackets"
- "evaluate this expression" (postfix/infix with precedence)
- "undo" / "browser back button" / history of operations
- "decode string" (nested repeat groups like `3[a2[c]]`)
- "collision" between adjacent elements moving toward each other

## General Template
```python
def is_valid_parentheses(s: str) -> bool:
    """Classic bracket-matching stack: push opens, pop-and-check on closes."""
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: list[str] = []
    for char in s:
        if char in pairs:
            if not stack or stack.pop() != pairs[char]:
                return False
        else:
            stack.append(char)
    return not stack
```

## Complexity
O(n) time — each character is pushed and popped at most once. O(n) space for the stack in the worst
case (e.g. a string of all open brackets).

## Common Mistakes
Forgetting to check `not stack` before popping on a close bracket, which raises on unbalanced input
instead of returning false. Forgetting the final `not stack` check, which accepts strings with
leftover unmatched opens. Using a stack when a simple counter would do (only valid when there's a
single bracket type with no ordering constraint between types).

## When NOT to Use
If the problem is about the *next greater/smaller* element relative to a monotonic scan, use
`monotonic_stack` instead — a plain stack alone won't maintain that ordering invariant. If order
doesn't matter at all (just membership or counts), a hash set/map is simpler and doesn't need LIFO
semantics.

## Variations
Two-stack designs (e.g. Min Stack tracks the running minimum in a parallel stack); using a stack to
simulate recursion iteratively (DFS, expression parsing); operator-precedence expression evaluation
with a stack of operators and a stack of operands; asteroid-collision-style problems where a stack
models "as-yet-unresolved" elements moving toward each other.

## Representative Problems
- [Valid Parentheses](https://leetcode.com/problems/valid-parentheses/) — Easy
- [Min Stack](https://leetcode.com/problems/min-stack/) — Medium
- [Evaluate Reverse Polish Notation](https://leetcode.com/problems/evaluate-reverse-polish-notation/) — Medium
- [Generate Parentheses](https://leetcode.com/problems/generate-parentheses/) — Medium
- [Decode String](https://leetcode.com/problems/decode-string/) — Medium
- [Asteroid Collision](https://leetcode.com/problems/asteroid-collision/) — Medium
