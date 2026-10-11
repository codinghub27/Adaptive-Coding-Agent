"""Checks a reply's stated facts against what is on the page (target 15, 21).

Audit section 15: "Accuracy is asked for in prompts, not checked. Nothing
verifies a stated complexity or that a walk-through's indices match its
input." Two checks, both pure and `ast`-only, both deliberately one-sided:
they only speak when the text contradicts something that can be read off
without judgement, and otherwise leave the tutor's words alone.

`consistent_time(code, stated)` -- the cost stated beside code the tutor shows:

- constant time claimed for code that loops or recurses: not stated;
- less than O(n log n) claimed for code that sorts: O(n log n) at least, so
  not stated;
- less than O(n^2) claimed for a `for` directly inside a `for` over the SAME
  input (`range(n)` in `range(n)`, `range(i + 1, len(nums))` in
  `range(len(nums))`): corrected to O(n^2) (O(n^3) for three).

A `while` inside a loop is left alone (two pointers and sliding windows are
linear), and so is a cost in other variables ("O(n * m)", "O(V + E)").

`trace_problems(text)` -- a walk-through that quotes its input ("aba",
[2, 7, 11, 15]) and then names an index that input does not have, or says an
index holds a value it does not hold.

Neither is a proof of correctness. A cost that passes may still be wrong; only
the sandbox judges code.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Sequence
from typing import Final

__all__ = ["consistent_time", "trace_problems", "without_trace_problems"]

# --------------------------------------------------------------------------
# Stated time against the code
# --------------------------------------------------------------------------

_LOG: Final = 1
_LINEAR: Final = 2
_LINEARITHMIC: Final = 3
_QUADRATIC: Final = 4
_CUBIC: Final = 5

_RANKS: Final[tuple[tuple[str, int], ...]] = (
    ("o(1)", 0),
    ("o(logn)", _LOG),
    ("o(n)", _LINEAR),
    ("o(nlogn)", _LINEARITHMIC),
    ("o(n^2)", _QUADRATIC),
    ("o(n*n)", _QUADRATIC),
    ("o(n^3)", _CUBIC),
)
_BY_RANK: Final = {_QUADRATIC: "O(n^2)", _CUBIC: "O(n^3)"}
_LOOPS: Final = (ast.For, ast.AsyncFor, ast.While, ast.ListComp, ast.SetComp, ast.DictComp)
_NOT_INPUT: Final = frozenset({"range", "len", "enumerate", "zip", "reversed", "sorted", "min"})


def _rank(stated: str) -> int | None:
    """Where a one-variable cost sits, or `None` for anything else."""
    flat = re.sub(r"\s+", "", stated.lower())
    flat = flat.replace("²", "^2").replace("³", "^3").replace("**", "^").replace("·", "*")
    for spelling, rank in _RANKS:
        if flat == spelling:
            return rank
    return None


def _roots(iterable: ast.expr, loop_vars: frozenset[str]) -> frozenset[str]:
    """The names an iterable's length comes from (`nums` in
    `range(i + 1, len(nums))`), without the enclosing loops' own variables.

    Empty for an iterable picked out BY an enclosing loop's variable
    (`graph[u]`, `grid[r]`, `word` itself): that is one item's own collection,
    and walking every one of them is not the input squared.
    """
    for node in ast.walk(iterable):
        if isinstance(node, ast.Subscript) and any(
            isinstance(inner, ast.Name) and inner.id in loop_vars for inner in ast.walk(node.slice)
        ):
            return frozenset()
    names = {node.id for node in ast.walk(iterable) if isinstance(node, ast.Name)}
    return frozenset(names - _NOT_INPUT - loop_vars)


def _targets(node: ast.For | ast.AsyncFor) -> frozenset[str]:
    return frozenset(n.id for n in ast.walk(node.target) if isinstance(n, ast.Name))


_LEAVES: Final = (ast.Return, ast.Break, ast.Raise)


def _last_before_leaving(node: ast.AST) -> frozenset[int]:
    """The `for` statements directly in `node`'s blocks that are followed, in
    the same block, by an unconditional `return`, `break` or `raise`.

    Measured live (2026-10-11): a fallback `for x in arr: ...` followed by
    `return None`, inside `for num in arr:`, was called O(n^2). The outer loop
    never reaches a second pass after it.
    """
    found: set[int] = set()
    for name in ("body", "orelse", "finalbody"):
        block = getattr(node, name, None)
        if not isinstance(block, list):
            continue
        statements = [item for item in block if isinstance(item, ast.stmt)]  # pyright: ignore[reportUnknownVariableType]
        for position, statement in enumerate(statements):
            if isinstance(statement, (ast.For, ast.AsyncFor)) and any(
                isinstance(later, _LEAVES) for later in statements[position + 1 :]
            ):
                found.add(id(statement))
    return frozenset(found)


def _chains(node: ast.AST, roots: frozenset[str], bound: frozenset[str]) -> tuple[int, int]:
    """(how far the enclosing chain of same-input `for` loops is extended
    inside `node`, the longest such chain anywhere inside it).

    A chain is `for` loops, each inside the last, that all range over one
    shared input. A function defined inside a loop starts over: defining it
    costs nothing.
    """
    extended = best = 0
    last = _last_before_leaving(node)
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            best = max(best, _chains(child, frozenset(), frozenset())[1])
            continue
        if not isinstance(child, (ast.For, ast.AsyncFor)):
            further, inside = _chains(child, roots, bound)
            extended, best = max(extended, further), max(best, inside)
            continue
        own = _roots(child.iter, bound)
        inner_bound = bound | _targets(child)
        # A loop the enclosing one leaves right after runs once in all, not
        # once per pass: it does not multiply the loop around it.
        shared = frozenset[str]() if id(child) in last else own & roots
        if shared:
            further, inside = _chains(child, shared, inner_bound)
            extended, best = max(extended, 1 + further), max(best, inside)
        if own:
            further, inside = _chains(child, own, inner_bound)
            best = max(best, 1 + further, inside)
        else:
            further, inside = _chains(child, roots, inner_bound)
            extended, best = max(extended, further), max(best, inside)
    return extended, best


def _sorts(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "sorted":
            return True
        if isinstance(node.func, ast.Attribute) and node.func.attr == "sort":
            return True
    return False


def _repeats(tree: ast.AST) -> bool:
    """The code loops, or a function in it calls itself."""
    for node in ast.walk(tree):
        if isinstance(node, _LOOPS):
            return True
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, (ast.Name, ast.Attribute))
                    and (inner.func.id if isinstance(inner.func, ast.Name) else inner.func.attr)
                    == node.name
                ):
                    return True
    return False


def consistent_time(code: str | None, stated: str | None) -> str | None:
    """`stated` when the code does not contradict it; the cost the loops show
    when they do and it can be read off; `None` (say nothing) otherwise.

    Unparseable code, no code, or a cost in other variables: `stated` as is.
    """
    if stated is None or not stated.strip() or code is None or not code.strip():
        return stated
    rank = _rank(stated)
    if rank is None:
        return stated
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return stated
    depth = min(_chains(tree, frozenset(), frozenset())[1], 3)
    nested = {2: _QUADRATIC, 3: _CUBIC}.get(depth)
    if nested is not None and rank < nested:
        return _BY_RANK[nested]
    if rank < _LINEARITHMIC and _sorts(tree):
        return None
    if rank == 0 and _repeats(tree):
        return None
    return stated


# --------------------------------------------------------------------------
# A worked trace against the input it quotes
# --------------------------------------------------------------------------

#: A quoted word is the input only when the text presents it as one ("take
#: "aba"", "s = "aba""); "seen" in quotes is a name, not a string to index.
_STRING: Final = re.compile(
    r"""(?:\b(?:take|consider|in|for|with|string|input|word|example)\s*:?\s+|=\s*)"""
    r"""(["'])([A-Za-z0-9]{2,40})\1""",
    re.I,
)
_LIST: Final = re.compile(r"\[\s*-?\w+(?:\s*,\s*-?\w+)+\s*\]")
_INDEX: Final = re.compile(r"\bind(?:ex|ices)\s+(\d+)(?:\s*(?:,|and|to|through|-)\s*(\d+))*", re.I)
#: "index 1 is 7", "index 0 holds 'a'". A letter must be quoted ("index 0 is
#: a match" names no value). `name[1]` is not read at all: `seen[7]` beside a
#: quoted list is a dictionary lookup, and which name is the input is not known.
_HOLDS: Final = re.compile(
    r"\bindex\s+(\d+)\s*(?:is|holds|=|==|:|,\s*which is)\s*"
    r"""(?:(-?\d+)(?!\w|\.\d)|[`"']([A-Za-z0-9])[`"'])""",
    re.I,
)
_SENTENCE: Final = re.compile(r"(?<=[.!?])\s+|\n+")


def _sequences(text: str) -> list[Sequence[object]]:
    found: list[Sequence[object]] = [match.group(2) for match in _STRING.finditer(text)]
    for match in _LIST.finditer(text):
        try:
            value = ast.literal_eval(match.group(0))
        except (ValueError, SyntaxError):
            continue
        if isinstance(value, list):
            found.append(tuple(value))  # pyright: ignore[reportUnknownArgumentType]
    return found


def _holds(sequence: Sequence[object], index: int, value: str) -> bool:
    return index < len(sequence) and str(sequence[index]) == value


def _sentence_problems(sentence: str, sequences: list[Sequence[object]]) -> list[str]:
    problems: list[str] = []
    longest = max(len(sequence) for sequence in sequences)
    named = [int(n) for match in _INDEX.finditer(sentence) for n in match.groups() if n]
    for index in named:
        if index >= longest:
            problems.append(f"index {index} is past the end of an input of length {longest}")
    for match in _HOLDS.finditer(sentence):
        index = int(match.group(1))
        value = match.group(2) or match.group(3)
        if index < longest and not any(_holds(s, index, value) for s in sequences):
            problems.append(f"index {index} does not hold {value}")
    return problems


def trace_problems(text: str) -> list[str]:
    """What a walk-through says about its own quoted input that is not so.

    Only when the text quotes an input (a string of letters or digits, or a
    list). An index is wrong when it is past the end of EVERY input quoted; a
    value is wrong when no quoted input holds it at that index. Text that
    quotes nothing, or says nothing checkable, has no problems.
    """
    sequences = _sequences(text)
    if not sequences:
        return []
    problems: list[str] = []
    for sentence in _SENTENCE.split(text):
        problems += _sentence_problems(sentence, sequences)
    return problems


def without_trace_problems(text: str) -> str | None:
    """`text` without the sentences `trace_problems` objects to, or `None`
    when nothing sound is left to say (under 40 characters)."""
    sequences = _sequences(text)
    if not sequences:
        return text
    kept: list[str] = []
    dropped = False
    for part in re.split(r"((?<=[.!?])\s+|\n+)", text):
        if part.strip() and _sentence_problems(part, sequences):
            dropped = True
            continue
        kept.append(part)
    if not dropped:
        return text
    out = "".join(kept).strip()
    return out if len(out) >= 40 else None
