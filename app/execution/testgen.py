"""Deterministic, LLM-free extraction of a `TestSuite` from a learner's turn.

`extract_test_suite` is the only public entry point: given the learner's
`StructuredInput` (untrusted content -- exactly as documented in
`app.graph.nodes`'s module docstring), it tries to recover an entrypoint name
from the learner's own submitted code (`ast.parse`, never executed) and a
handful of worked-example test cases from the problem statement's own prose
("Example 1: Input: ... Output: ...").

This is intentionally conservative: no LLM is involved anywhere in this
module, and every value is parsed with `ast.literal_eval` only -- never
`eval`, never `exec`. `literal_eval` can still raise `MemoryError` or
`RecursionError` on adversarial input, so every call here is wrapped and the
statement text it runs against is capped in both length and case count
(`MAX_TEST_CASES`) before parsing starts.

A wrong test suite is worse than none: it would make the verifier report a
false failure against otherwise-correct code. Every code path that cannot
recover a *confident* entrypoint-plus-cases pair returns `None` rather than
guessing, and nothing here ever raises out to its caller.
"""

from __future__ import annotations

import ast
import re
from typing import Final, cast

from pydantic import JsonValue, ValidationError

from app.agents.debugger import extract_learner_code
from app.schemas.execution import MAX_TEST_CASES, TestCase, TestSuite
from app.schemas.input import StructuredInput

__all__ = ["extract_test_suite"]

_MAX_STATEMENT_CHARS: Final = 20_000
_MAX_VALUE_CHARS: Final = 2_000

_PARSE_FAILED: Final = object()

_LABEL_LINE_RE: Final = re.compile(
    r"(?im)^[ \t]*[*`]*[ \t]*(input|output)[ \t]*[*`]*[ \t]*:[ \t]*[*`]*[ \t]*(?P<value>.*)$"
)
"""Matches an `Input:`/`Output:` line, tolerating the markdown a pasted problem
statement carries: `**Input:**`, `**Input**:`, and backtick-wrapped labels."""
_KWARG_RE: Final = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=(?!=)\s*(.+)$", re.DOTALL)


class _UnsupportedValue(Exception):
    """Raised internally when a parsed literal is not JSON-compatible."""


# --------------------------------------------------------------------------
# Entrypoint (from the learner's own code, never the statement)
# --------------------------------------------------------------------------


def _top_level_functions(code: str | None) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Every top-level `def`/`async def` in the learner's code, in order."""
    if not code:
        return []
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return []
    return [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _accepts(func: ast.FunctionDef | ast.AsyncFunctionDef, case: TestCase) -> bool:
    """Can `func` actually be called the way `case` describes?

    Calling the wrong function is worse than deriving no tests at all: the
    sandbox raises `TypeError`, `verify` turns that into a real `fail` verdict,
    the debugger burns LLM budget "fixing" a non-bug, and
    `update_learner_model` persists `solved=False` against correct code. So a
    case must fit the signature: every keyword name must be a real parameter,
    and the positional count must be within arity.
    """
    args = func.args
    if args.kwarg is None:  # no **kwargs to absorb unknown names
        names = {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
        if not set(case.kwargs).issubset(names):
            return False
    if args.vararg is None:  # no *args to absorb extra positionals
        positional = len(args.posonlyargs) + len(args.args)
        if len(case.args) > positional:
            return False
    required = len(args.posonlyargs) + len(args.args) - len(args.defaults)
    supplied = len(case.args) + len(case.kwargs)
    return supplied >= max(required, 0) or args.vararg is not None


def _select_entrypoint(
    functions: list[ast.FunctionDef | ast.AsyncFunctionDef], cases: list[TestCase]
) -> str | None:
    """The public-looking top-level function every case can actually call.

    Submissions often define helpers before the solution, so "the first `def`"
    is the wrong guess. Prefer a non-underscore function that fits all cases;
    fall back to any fitting function; give up rather than pick a misfit.
    """
    fitting = [f for f in functions if all(_accepts(f, case) for case in cases)]
    if not fitting:
        return None
    public = [f for f in fitting if not f.name.startswith("_")]
    return (public or fitting)[-1].name if len(fitting) > 1 else fitting[0].name


# --------------------------------------------------------------------------
# Value parsing: `ast.literal_eval` only, bounded, JSON-compatible output
# --------------------------------------------------------------------------


def _clean_expr(text: str) -> str:
    return text.strip().strip("`*").strip()


def _safe_literal_eval(expr: str) -> object:
    if not expr or len(expr) > _MAX_VALUE_CHARS:
        return _PARSE_FAILED
    try:
        return ast.literal_eval(expr)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError, OverflowError):
        return _PARSE_FAILED


def _to_json_value(value: object) -> JsonValue:
    """Convert a `literal_eval` result to a `JsonValue`, converting tuples to
    lists where unambiguous. Raises `_UnsupportedValue` for anything else
    (sets, bytes, complex, non-str dict keys, ...)."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        items = cast("list[object] | tuple[object, ...]", value)
        return [_to_json_value(item) for item in items]
    if isinstance(value, dict):
        mapping = cast("dict[object, object]", value)
        converted: dict[str, JsonValue] = {}
        for key, item in mapping.items():
            if not isinstance(key, str):
                raise _UnsupportedValue
            converted[key] = _to_json_value(item)
        return converted
    raise _UnsupportedValue


def _parse_value(text: str) -> JsonValue | object:
    """Parse `text` into a `JsonValue`, or return the `_PARSE_FAILED` sentinel."""
    raw = _safe_literal_eval(_clean_expr(text))
    if raw is _PARSE_FAILED:
        return _PARSE_FAILED
    try:
        return _to_json_value(raw)
    except _UnsupportedValue:
        return _PARSE_FAILED


def _split_top_level(text: str) -> list[str]:
    """Split `text` on top-level commas, respecting bracket/quote nesting."""
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    in_string: str | None = None
    for char in text:
        if in_string is not None:
            current.append(char)
            if char == in_string:
                in_string = None
            continue
        if char in "'\"":
            in_string = char
            current.append(char)
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


# --------------------------------------------------------------------------
# Worked-example extraction
# --------------------------------------------------------------------------


def _find_labeled_lines(statement: str) -> list[tuple[str, str]]:
    """Return `(label, value)` for each `Input:`/`Output:` line, in order."""
    return [
        (m.group(1).lower(), _clean_expr(m.group("value")))
        for m in _LABEL_LINE_RE.finditer(statement)
    ]


def _build_case(name: str, input_text: str, output_text: str) -> TestCase | None:
    expected = _parse_value(output_text)
    if expected is _PARSE_FAILED:
        return None

    parts = [_clean_expr(part) for part in _split_top_level(input_text)]
    parts = [part for part in parts if part]
    if not parts:
        return None

    # Each part is cleaned as well as the whole value: a statement may wrap the
    # expression once (`` `nums = [1], target = 2` ``) or each part separately
    # (`` `nums = [1]`, `target = 2` ``).
    kwarg_matches = [_KWARG_RE.match(part) for part in parts]
    if all(match is not None for match in kwarg_matches):
        kwargs: dict[str, JsonValue] = {}
        for match in kwarg_matches:
            assert match is not None  # narrowed by the `all(...)` check above
            value = _parse_value(match.group(2))
            if value is _PARSE_FAILED:
                return None
            kwargs[match.group(1)] = value  # pyright: ignore[reportArgumentType]
        try:
            return TestCase(name=name, kwargs=kwargs, expected=expected)  # pyright: ignore[reportArgumentType]
        except ValidationError:
            return None

    if len(parts) == 1:
        value = _parse_value(parts[0])
        if value is _PARSE_FAILED:
            return None
        try:
            return TestCase(name=name, args=[value], expected=expected)  # pyright: ignore[reportArgumentType]
        except ValidationError:
            return None

    return None


def _extract_cases(statement: str) -> list[TestCase]:
    labeled = _find_labeled_lines(statement[:_MAX_STATEMENT_CHARS])
    cases: list[TestCase] = []
    index = 0
    example_number = 0
    while index < len(labeled) and len(cases) < MAX_TEST_CASES:
        label, value = labeled[index]
        if label != "input":
            index += 1
            continue
        if index + 1 >= len(labeled) or labeled[index + 1][0] != "output":
            index += 1
            continue
        example_number += 1
        case = _build_case(f"example_{example_number}", value, labeled[index + 1][1])
        if case is not None:
            cases.append(case)
        index += 2
    return cases


def _combined_statement(problem: StructuredInput) -> str:
    parts = [text for text in (problem.problem, problem.question) if text]
    return "\n\n".join(parts)


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------


def extract_test_suite(problem: StructuredInput | None) -> TestSuite | None:
    """Best-effort, LLM-free `TestSuite` recovered from `problem`, or `None`.

    Reads **untrusted** learner input (the problem statement and submitted
    code). Never executes anything -- `ast.parse` for the entrypoint,
    `ast.literal_eval` for example values -- and never raises: any failure to
    confidently recover both an entrypoint and at least one case yields
    `None` rather than a guessed/wrong suite.
    """
    if problem is None:
        return None

    try:
        functions = _top_level_functions(extract_learner_code(problem))
        if not functions:
            return None

        statement = _combined_statement(problem)
        if not statement:
            return None

        cases = _extract_cases(statement)
        if not cases:
            return None

        # Entrypoint is chosen *after* the cases, so it can be checked against
        # how they actually call it (see `_select_entrypoint`).
        entrypoint = _select_entrypoint(functions, cases)
        if entrypoint is None:
            return None

        return TestSuite(entrypoint=entrypoint, cases=cases)
    except Exception:  # noqa: BLE001 - never let malformed learner input cost the turn
        return None
