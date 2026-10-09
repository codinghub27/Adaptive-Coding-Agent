"""Code the tutor writes is shown as plain Python: no type hints.

Owner decision (2026-10-09): generated code must read like

    def expand(left, right):

and not `def expand(left: int, right: int) -> tuple[int, int]:`. The prompts
ask for that; `without_type_hints` guarantees it, because a model asked for
plain code still annotates about as often as not.

The source is edited in place, by the positions `ast` reports, so comments,
blank lines and formatting survive (`ast.unparse` would drop the comments the
learner is meant to read). Nothing is executed. Annotations do not change what
Python code does, so the result behaves exactly like the code the sandbox ran.
Left alone: annotated fields in a class body (a dataclass needs them), bare
declarations with no value, and any annotation that spans lines.
"""

import ast
import re
from typing import Final

__all__ = ["has_type_hints", "without_type_hints"]

_Span = tuple[int, int, int, bytes]  # 1-based line, start byte, end byte, what goes there

_PARSE_ERRORS: Final = (SyntaxError, ValueError, RecursionError, MemoryError)
_DEFAULT_RE: Final = re.compile(rb"\s*=\s*")
_TYPING_IMPORT_RE: Final = re.compile(r"^\s*(?:from\s+typing\s+import\s+(.+)|import\s+typing)\s*$")


def _parse(code: str) -> ast.Module | None:
    try:
        return ast.parse(code)
    except _PARSE_ERRORS:
        return None


def _arguments(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> list[ast.arg]:
    args = node.args
    found = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    found += [extra for extra in (args.vararg, args.kwarg) if extra is not None]
    return found


def _spans(tree: ast.Module, lines: list[bytes]) -> list[_Span]:
    """Every stretch of source that is only a type hint."""
    spans: list[_Span] = []
    in_class_body = {
        id(stmt) for node in ast.walk(tree) if isinstance(node, ast.ClassDef) for stmt in node.body
    }
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in _arguments(node):
                hint = arg.annotation
                if hint is None or hint.end_lineno != arg.lineno or hint.end_col_offset is None:
                    continue
                start = arg.col_offset + len(arg.arg.encode())
                end = hint.end_col_offset
                # `limit: int = None` -> `limit=None`: an unannotated default
                # is written without spaces around the equals sign.
                default = _DEFAULT_RE.match(lines[arg.lineno - 1], end)
                if default is not None:
                    spans.append((arg.lineno, start, default.end(), b"="))
                else:
                    spans.append((arg.lineno, start, end, b""))
            returns = node.returns
            if (
                returns is not None
                and returns.end_lineno == returns.lineno
                and returns.end_col_offset is not None
            ):
                line = lines[returns.lineno - 1]
                arrow = line.rfind(b"->", 0, returns.col_offset)
                if arrow != -1:
                    start = len(line[:arrow].rstrip())
                    spans.append((returns.lineno, start, returns.end_col_offset, b""))
        elif (
            isinstance(node, ast.AnnAssign)
            and node.value is not None
            and id(node) not in in_class_body
            and node.annotation.end_lineno == node.target.end_lineno
            and node.target.end_col_offset is not None
            and node.annotation.end_col_offset is not None
        ):
            line_no = node.target.end_lineno or node.lineno
            spans.append((line_no, node.target.end_col_offset, node.annotation.end_col_offset, b""))
    return spans


def has_type_hints(code: str) -> bool:
    """Does `code` carry a hint `without_type_hints` would remove?"""
    tree = _parse(code)
    if tree is None:
        return False
    return bool(_spans(tree, [line.encode() for line in code.split("\n")]))


def _drop_unused_typing_imports(code: str) -> str:
    """Remove a `typing` import whose names nothing uses any more."""
    lines = code.split("\n")
    kept: list[str] = []
    for index, line in enumerate(lines):
        match = _TYPING_IMPORT_RE.match(line)
        if match is None:
            kept.append(line)
            continue
        rest = "\n".join(lines[:index] + lines[index + 1 :])
        imported = match.group(1)
        if imported is None:
            names = ["typing"]
        else:
            names = [part.split(" as ")[-1].strip(" ()") for part in imported.split(",")]
        if any(name and re.search(rf"\b{re.escape(name)}\b", rest) for name in names):
            kept.append(line)
    return "\n".join(kept)


def without_type_hints(code: str) -> str:
    """`code` with its parameter, return and variable type hints removed.

    Returns `code` unchanged when it does not parse, has no hints, or when the
    edited text would no longer parse: plain code is a presentation rule, and
    it never costs the learner a working answer.
    """
    tree = _parse(code)
    if tree is None:
        return code
    lines = [line.encode() for line in code.split("\n")]
    spans = _spans(tree, lines)
    if not spans:
        return code
    for line_no, start, end, put in sorted(spans, key=lambda span: (span[0], -span[1])):
        row = lines[line_no - 1]
        if 0 <= start <= end <= len(row):
            lines[line_no - 1] = row[:start] + put + row[end:]
    try:
        plain = "\n".join(line.decode() for line in lines)
    except UnicodeDecodeError:
        return code
    plain = _drop_unused_typing_imports(plain)
    return plain if _parse(plain) is not None else code
