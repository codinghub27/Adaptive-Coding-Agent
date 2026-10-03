"""The closed misconception catalog and its deterministic code detectors (G3).

Catalog entries live in `app/knowledge/tutoring/misconceptions.json`; each is
anchored to a sentence of a corpus `## Common Mistakes` section (enforced by a
test), and its id is the ONLY form a misconception ever takes in state, events,
the profile or the API. An LLM may *propose* an id (the grader's judge), but
anything not in the catalog is dropped (`is_catalog_id`).

Code detectors read the learner's code with `ast` only -- nothing is executed --
and look for the specific shape each misconception names. They are tuned to be
precise rather than eager: a false "you have this misconception" is worse than
a missed one, because the reaction teaches against it.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Callable, Iterator
from functools import cache
from pathlib import Path
from typing import Final

from pydantic import Field

from app.schemas.base import APIModel

__all__ = [
    "Misconception",
    "catalog",
    "catalog_ids",
    "detect_in_code",
    "get_misconception",
    "is_catalog_id",
]

CATALOG_PATH: Final = (
    Path(__file__).resolve().parent.parent / "knowledge" / "tutoring" / "misconceptions.json"
)


class Misconception(APIModel):
    id: str
    pattern: str
    corpus_pattern: str
    corpus_quote: str
    name: str
    correct_model: str
    example: str
    question_id: str
    detector: str | None = None


class _Catalog(APIModel):
    misconceptions: list[Misconception] = Field(default_factory=list[Misconception])


@cache
def catalog() -> tuple[Misconception, ...]:
    raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    raw.pop("_doc", None)
    return tuple(_Catalog.model_validate(raw).misconceptions)


def catalog_ids() -> frozenset[str]:
    return frozenset(m.id for m in catalog())


def is_catalog_id(value: str | None) -> bool:
    return value is not None and value in catalog_ids()


def get_misconception(misconception_id: str) -> Misconception | None:
    for item in catalog():
        if item.id == misconception_id:
            return item
    return None


# --------------------------------------------------------------------------
# AST helpers
# --------------------------------------------------------------------------


def _functions(tree: ast.AST) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _src(node: ast.AST) -> str:
    try:
        return ast.unparse(node).replace(" ", "")
    except Exception:  # noqa: BLE001 - unparse of odd trees must not crash a turn
        return ""


def _names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _subscripted(func: ast.AST) -> set[str]:
    return {
        n.value.id
        for n in ast.walk(func)
        if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name)
    }


# --------------------------------------------------------------------------
# Detectors
# --------------------------------------------------------------------------


def _checked_key_vs_accessed_key(tree: ast.AST) -> bool:
    """`if X in D:` followed (in its body) by a read `D[Y]` with Y != X."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not (
            isinstance(test, ast.Compare)
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.In)
            and isinstance(test.comparators[0], ast.Name)
        ):
            continue
        mapping = test.comparators[0].id
        checked = _src(test.left)
        for stmt in node.body:
            for sub in ast.walk(stmt):
                if (
                    isinstance(sub, ast.Subscript)
                    and isinstance(sub.ctx, ast.Load)
                    and isinstance(sub.value, ast.Name)
                    and sub.value.id == mapping
                    and _src(sub.slice) != checked
                ):
                    return True
    return False


def _list_scan_lookup(tree: ast.AST) -> bool:
    """`x in nums` / `x in nums[:i]` inside `for ... in (enumerate)(nums)`."""
    for loop in ast.walk(tree):
        if not isinstance(loop, ast.For):
            continue
        iterable = loop.iter
        if isinstance(iterable, ast.Call) and iterable.args:
            iterable = iterable.args[0]
        if not isinstance(iterable, ast.Name):
            continue
        source = iterable.id
        for node in ast.walk(loop):
            if not isinstance(node, ast.Compare):
                continue
            for op, comparator in zip(node.ops, node.comparators, strict=False):
                if not isinstance(op, (ast.In, ast.NotIn)):
                    continue
                target = comparator.value if isinstance(comparator, ast.Subscript) else comparator
                if isinstance(target, ast.Name) and target.id == source:
                    return True
    return False


def _recursive_call_on(node: ast.AST, func_name: str, side: str) -> bool:
    for sub in ast.walk(node):
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Name)
            and sub.func.id == func_name
            and any(isinstance(a, ast.Attribute) and a.attr == side for a in sub.args)
        ):
            return True
    return False


def _add_operands(node: ast.AST) -> list[ast.AST]:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _add_operands(node.left) + _add_operands(node.right)
    return [node]


def _refers(expr: ast.AST, side_names: dict[str, set[str]], func_name: str, side: str) -> bool:
    return bool(_names(expr) & side_names[side]) or _recursive_call_on(expr, func_name, side)


def _returnable_vs_global_path(tree: ast.AST) -> bool:
    """A recursive tree function RETURNING left-result + right-result (+ node)."""
    for func in _functions(tree):
        side_names: dict[str, set[str]] = {"left": set(), "right": set()}
        for node in ast.walk(func):
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name):
                    for side in ("left", "right"):
                        if _recursive_call_on(node.value, func.name, side):
                            side_names[side].add(target.id)
        for node in ast.walk(func):
            if not isinstance(node, ast.Return) or node.value is None:
                continue
            operands = _add_operands(node.value)
            if len(operands) < 2:
                continue

            name = func.name
            has_left = any(
                _refers(op, side_names, name, "left") and not _refers(op, side_names, name, "right")
                for op in operands
            )
            has_right = any(
                _refers(op, side_names, name, "right") and not _refers(op, side_names, name, "left")
                for op in operands
            )
            if has_left and has_right:
                return True
    return False


_LOW_LINK_NAMES: Final = frozenset({"low", "disc", "tin", "low_link", "lowlink", "ids", "order"})


def _parent_node_vs_parent_edge(tree: ast.AST) -> bool:
    """Bridge-finding DFS that skips the parent by NODE id (`v == parent`)."""
    for func in _functions(tree):
        params = [a.arg for a in func.args.args]
        if len(params) < 2:
            continue
        if not (_subscripted(func) & _LOW_LINK_NAMES):
            continue
        parent = params[-1]
        for loop in ast.walk(func):
            if not isinstance(loop, ast.For) or not isinstance(loop.target, ast.Name):
                continue
            var = loop.target.id
            for node in ast.walk(loop):
                if not isinstance(node, ast.Compare) or len(node.ops) != 1:
                    continue
                if not isinstance(node.ops[0], (ast.Eq, ast.NotEq, ast.Is, ast.IsNot)):
                    continue
                sides = {_src(node.left), _src(node.comparators[0])}
                if sides == {var, parent}:
                    return True
    return False


def _popped_names(stmt: ast.stmt, *, queue: bool) -> set[str]:
    """Names bound by `x = Q.popleft()` (queue) / `x = S.pop()` (stack)."""
    if not isinstance(stmt, ast.Assign) or not isinstance(stmt.value, ast.Call):
        return set()
    call = stmt.value
    if not isinstance(call.func, ast.Attribute):
        return set()
    method = call.func.attr
    is_queue_pop = method == "popleft" or (
        method == "pop" and len(call.args) == 1 and _src(call.args[0]) == "0"
    )
    is_stack_pop = method == "pop" and not call.args
    if (queue and not is_queue_pop) or (not queue and not is_stack_pop):
        return set()
    names: set[str] = set()
    for target in stmt.targets:
        names |= _names(target)
    return names


def _marks(stmt: ast.stmt, names: set[str]) -> bool:
    """`S.add(name...)` / `S[name] = True` at this statement."""
    for node in ast.walk(stmt):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add"
            and node.args
            and _names(node.args[0]) & names
        ):
            return True
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and _names(target.slice) & names
                    and isinstance(node.value, ast.Constant)
                    and node.value.value is True
                ):
                    return True
    return False


def _marks_after_pop(tree: ast.AST, *, queue: bool) -> bool:
    for loop in ast.walk(tree):
        if not isinstance(loop, ast.While):
            continue
        popped: set[str] = set()
        for stmt in loop.body:  # top level of the loop body only, not neighbour loops
            if not popped:
                popped = _popped_names(stmt, queue=queue)
                continue
            if isinstance(stmt, (ast.For, ast.While)):
                break
            if _marks(stmt, popped):
                return True
    return False


_DETECTORS: Final[dict[str, Callable[[ast.AST], bool]]] = {
    "checked_key_vs_accessed_key": _checked_key_vs_accessed_key,
    "list_scan_lookup": _list_scan_lookup,
    "returnable_vs_global_path": _returnable_vs_global_path,
    "parent_node_vs_parent_edge": _parent_node_vs_parent_edge,
    "mark_visited_on_dequeue": lambda tree: _marks_after_pop(tree, queue=True),
    "mark_visited_after_pop": lambda tree: _marks_after_pop(tree, queue=False),
}


def detect_in_code(code: str | None, topic: str | None = None) -> list[str]:
    """Catalog ids whose detector matches `code` (untrusted; parsed, never run).

    `topic` is accepted for the call sites' clarity but does not filter: each
    detector already requires the specific shape it names.
    """
    del topic
    if not code or not code.strip():
        return []
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return []
    found: list[str] = []
    for item in catalog():
        detector = _DETECTORS.get(item.detector or "")
        if detector is None:
            continue
        try:
            if detector(tree):
                found.append(item.id)
        except RecursionError:
            continue
    return found
