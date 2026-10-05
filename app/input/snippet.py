"""Make a pasted Python snippet a runnable module, without changing what it does.

Learners paste code the way it sits in their editor, not as a module:

- a method body whose first line lost its indentation in the copy,
- a bare body that ends in `return` with no `def` around it,
- a LeetCode `class Solution` whose method no test harness can call by name.

Read as typed, each of these made the debugger report the PASTE as the bug
("syntax error ... line 5", "0/0 test cases passed") and never reach the real
one. `repair_snippet` re-aligns, wraps or adapts such a snippet so the static
checks, the test extractor and the sandbox all see the same runnable source.

Parsing only (`ast`): nothing here executes, imports or evaluates the code,
which stays **untrusted learner data**. Anything this module cannot repair
with confidence is returned unchanged, for the static checks to report.
"""

import ast
import builtins
from typing import Final

__all__ = ["WRAPPER_NAME", "is_sample_data", "repair_snippet"]

#: The function a bare body is wrapped in.
WRAPPER_NAME: Final = "solve"

_MAX_SNIPPET_CHARS: Final = 20_000
_INDENT: Final = "    "
_BUILTIN_NAMES: Final = frozenset(dir(builtins))
_TYPING_NAMES: Final = frozenset(
    {"List", "Dict", "Set", "Tuple", "Optional", "Deque", "DefaultDict", "Any", "Union"}
)
_TYPING_PRELUDE: Final = "from typing import *"

_Scope = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _parse(source: str) -> ast.Module | None:
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _realign(source: str) -> str | None:
    """`source` with a ragged left edge straightened, or `None` if no single
    shift makes it parse.

    A body copied out of a class keeps its 8 spaces on every line but the
    first. For each indentation depth found, treat that depth as column 0:
    deeper lines move left by it, shallower ones go to column 0. The first
    shift that parses wins. Line count and order never change.
    """
    lines = source.split("\n")
    depths = sorted({_indent_of(line) for line in lines if line.strip()} - {0})
    for depth in depths:
        shifted = [
            line[depth:] if _indent_of(line) >= depth else line.lstrip(" ") for line in lines
        ]
        candidate = "\n".join(shifted)
        if _parse(candidate) is not None:
            return candidate
    return None


def _own_statements(body: list[ast.stmt]) -> list[ast.stmt]:
    """Every statement reachable from `body` without entering a def or class."""
    found: list[ast.stmt] = []
    stack = list(body)
    while stack:
        node = stack.pop()
        found.append(node)
        if isinstance(node, _Scope):
            continue
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.stmt):
                stack.append(child)
            elif isinstance(child, (ast.ExceptHandler, ast.match_case)):
                stack.extend(child.body)
    return found


def _has_bare_return(tree: ast.Module) -> bool:
    return any(isinstance(node, ast.Return) for node in _own_statements(tree.body))


def _is_literal(node: ast.expr) -> bool:
    try:
        ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return False
    return True


def _sample_inputs(tree: ast.Module) -> list[ast.Assign]:
    """Leading `name = <literal>` lines: the sample input pasted above a body."""
    samples: list[ast.Assign] = []
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and _is_literal(node.value)
        ):
            samples.append(node)
        else:
            break
    return samples


def _bound_names(tree: ast.AST) -> set[str]:
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.alias):
            bound.add((node.asname or node.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
    return bound


def _free_names(tree: ast.AST) -> list[str]:
    """Names the snippet reads but never binds, in order of first use: the
    inputs the missing `def` line would have declared."""
    bound = _bound_names(tree)
    loads = sorted(
        (node for node in ast.walk(tree) if isinstance(node, ast.Name)),
        key=lambda node: (node.lineno, node.col_offset),
    )
    free: list[str] = []
    for node in loads:
        if (
            isinstance(node.ctx, ast.Load)
            and node.id not in bound
            and node.id not in _BUILTIN_NAMES
            and node.id not in free
        ):
            free.append(node.id)
    return free


def _wrap_body(source: str, tree: ast.Module) -> str:
    """A bare body with a top-level `return`, wrapped in `def solve(...)`.

    Sample-input lines above the body become parameters with those values as
    defaults, so the function runs as pasted and can still be called with
    other inputs. The header replaces those lines (blank-padded), keeping the
    body's line numbers whenever there was at least one.
    """
    lines = source.split("\n")
    samples = _sample_inputs(tree)
    sample_names = [
        target.id for node in samples for target in node.targets if isinstance(target, ast.Name)
    ]
    params = [name for name in _free_names(tree) if name not in sample_names]
    params += [
        f"{name}={ast.unparse(node.value)}"
        for name, node in zip(sample_names, samples, strict=True)
    ]
    header = f"def {WRAPPER_NAME}({', '.join(params)}):"
    consumed = (samples[-1].end_lineno or samples[-1].lineno) if samples else 0
    body = [(_INDENT + line) if line.strip() else line for line in lines[consumed:]]
    padding = [""] * max(consumed - 1, 0)
    wrapped = "\n".join([header, *padding, *body])
    if samples and len(sample_names) == len(params):
        # The paste was a script that ran on its sample input; keep it one, so
        # a crash on that input is still observed when it is run.
        wrapped = f"{wrapped.rstrip()}\n\n\n{WRAPPER_NAME}()\n"
    return wrapped


def _solution_class(tree: ast.Module) -> ast.ClassDef | None:
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    named = [node for node in classes if node.name == "Solution"]
    return (named or classes or [None])[0]


def _constructible(cls: ast.ClassDef) -> bool:
    """Can `cls()` be called with no arguments?"""
    for node in cls.body:
        if isinstance(node, ast.FunctionDef) and node.name == "__init__":
            args = node.args
            required = len(args.posonlyargs) + len(args.args) - len(args.defaults) - 1
            unfilled_kwonly = sum(default is None for default in args.kw_defaults)
            return required <= 0 and unfilled_kwonly == 0
    return True


def _is_plain_method(node: ast.stmt) -> bool:
    if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
        return False
    if node.decorator_list:  # staticmethod / classmethod / property: not `self`-first
        return False
    args = node.args
    return bool(args.args) and not args.posonlyargs and not args.kwonlyargs


def _adapter(cls: ast.ClassDef, method: ast.FunctionDef) -> str:
    """A module-level function with `method`'s own parameters (minus `self`,
    annotations dropped) that calls it on a fresh instance."""
    args = method.args
    names = [a.arg for a in args.args[1:]]
    defaults = [ast.unparse(d) for d in args.defaults][-len(names) :] if names else []
    first_default = len(names) - len(defaults)
    params = [
        name if i < first_default else f"{name}={defaults[i - first_default]}"
        for i, name in enumerate(names)
    ]
    call = list(names)
    if args.vararg is not None:
        params.append(f"*{args.vararg.arg}")
        call.append(f"*{args.vararg.arg}")
    if args.kwarg is not None:
        params.append(f"**{args.kwarg.arg}")
        call.append(f"**{args.kwarg.arg}")
    return (
        f"def {method.name}({', '.join(params)}):\n"
        f"{_INDENT}return {cls.name}().{method.name}({', '.join(call)})"
    )


def _adapt_class(source: str, tree: ast.Module) -> str:
    """Give a `class Solution`'s public methods module-level entry points."""
    cls = _solution_class(tree)
    if cls is None or not _constructible(cls):
        return source
    taken = _bound_names(ast.Module(body=[n for n in tree.body if n is not cls], type_ignores=[]))
    taken.add(cls.name)
    adapters = [
        _adapter(cls, node)
        for node in cls.body
        if _is_plain_method(node) and isinstance(node, ast.FunctionDef) and node.name not in taken
    ]
    if not adapters:
        return source
    return source.rstrip("\n") + "\n\n\n" + "\n\n\n".join(adapters) + "\n"


def _needs_typing(tree: ast.Module) -> bool:
    return any(name in _TYPING_NAMES for name in _free_names(tree))


def is_sample_data(code: str) -> bool:
    """Is `code` only literal assignments (`nums = [2, 7, 11, 15]`, `target = 9`)?

    That is the example input of a problem, picked up as "code" because it
    parses as Python -- not something the learner wrote and wants read.
    """
    tree = _parse(code.replace("\r\n", "\n"))
    if tree is None or not tree.body:
        return False
    return all(isinstance(node, ast.Assign) and _is_literal(node.value) for node in tree.body)


def repair_snippet(code: str) -> str:
    """`code` as a runnable module, or `code` itself when it already is one or
    cannot be repaired with confidence. Pure and deterministic; never raises."""
    if not code.strip() or len(code) > _MAX_SNIPPET_CHARS:
        return code
    source = code.replace("\r\n", "\n").replace("\r", "\n")
    tree = _parse(source)
    if tree is None:
        realigned = _realign(source)
        tree = _parse(realigned) if realigned is not None else None
        if realigned is None or tree is None:
            return code
        source = realigned

    has_function = any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) for n in tree.body)
    if _has_bare_return(tree):
        source = _wrap_body(source, tree)
    elif not has_function:
        source = _adapt_class(source, tree)

    repaired = _parse(source)
    if repaired is None:
        return code
    if _needs_typing(repaired) and "__future__" not in source:
        source = f"{_TYPING_PRELUDE}\n{source}"
    return source if _parse(source) is not None else code
