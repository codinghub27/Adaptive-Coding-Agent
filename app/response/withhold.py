"""Keep a withheld fix out of the prose (target behaviour sections 6 and 9).

"Explain it, but don't give me the final code yet" is the learner's own cap.
The patch and the code block are already withheld in code
(`app.response.generate`); the agent prompt is also told not to state the fix
(`app.tutoring.adaptation`). A prompt instruction is not a guarantee: measured
live (audit section 14, scenario 15), the model quoted the corrected line
inside a sentence in one run of two. This module is the guarantee for what can
be recognised without reading meaning:

- the lines the debugger's own patch ADDS to the learner's code, and the
  expression each one computes (in either order for `a + b` / `a * b`),
  wherever they appear in the reply, in backticks or not;
- any statement in backticks that is code the learner did not write (a
  `return`, an assignment of a computed value, a loop or branch header), and
  an augmented assignment they did not write, in backticks or not;
- one of the learner's own computed expressions with something added to it
  (`max(left, right)` in their code, `1 + max(left, right)` in the reply):
  that is the corrected expression, with or without a patch to compare with;
- a fenced block of Python that carries any of the above. A traceback, the
  sandbox's output and the learner's own code (with or without a marker
  comment) are evidence for the diagnosis and stay.

What is wrong and why stays: "you are not counting the current node" is the
diagnosis, not the fix. A fix said entirely in words ("add one to the
result") is not recognised here; that remains the prompt's job.

Pure and `ast`-only: nothing is executed.
"""

from __future__ import annotations

import ast
import re
import textwrap
from collections.abc import Sequence
from typing import Final

__all__ = ["WITHHELD", "redact_fix"]

#: What stands where the fix was stated.
WITHHELD: Final = "(that change is yours to make)"

_FENCE: Final = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_SPAN: Final = re.compile(r"`([^`\n]+)`")
_TOKEN: Final = re.compile(r"\w+|[^\w\s]")
#: A fragment shorter than this without its spaces (`i+1`, `x=0`) is too common
#: in ordinary explanation to be called the fix.
_MIN_FRAGMENT: Final = 6
_COMMUTATIVE: Final = (ast.Add, ast.Mult)


def _flat(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _statement(line: str) -> ast.stmt | None:
    """`line` as one Python statement; a block header gets an empty body."""
    source = line.strip()
    if not source:
        return None
    for candidate in (source, source + " pass", source + "\n    pass"):
        try:
            body = ast.parse(candidate).body
        except SyntaxError:
            continue
        return body[0] if len(body) == 1 else None
    return None


def _expression(statement: ast.stmt) -> ast.expr | None:
    """The value a statement computes: what `return` returns, what `=` assigns,
    what `if` / `while` tests."""
    if isinstance(statement, (ast.Return, ast.Expr)):
        return statement.value
    if isinstance(statement, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
        return statement.value
    if isinstance(statement, (ast.If, ast.While)):
        return statement.test
    return None


def _spellings(expression: ast.expr) -> list[str]:
    out = [ast.unparse(expression)]
    if isinstance(expression, ast.BinOp) and isinstance(expression.op, _COMMUTATIVE):
        swapped = ast.BinOp(left=expression.right, op=expression.op, right=expression.left)
        out.append(ast.unparse(swapped))
    return out


def _fix_fragments(learner_code: str, patched_code: str) -> list[str]:
    """The corrected lines and expressions, longest first, none of them
    something the learner's own code already says."""
    theirs = _flat(learner_code)
    own_lines = {_flat(line) for line in learner_code.splitlines()}
    fragments: set[str] = set()
    for line in patched_code.splitlines():
        if not line.strip() or _flat(line) in own_lines:
            continue
        candidates = [line.strip().rstrip(":")]
        statement = _statement(line)
        expression = _expression(statement) if statement is not None else None
        if expression is not None:
            candidates += _spellings(expression)
        for candidate in candidates:
            flat = _flat(candidate)
            if len(flat) >= _MIN_FRAGMENT and flat not in theirs and re.search(r"\W", flat):
                fragments.add(candidate)
    return sorted(fragments, key=len, reverse=True)


def _spaced(fragment: str) -> str:
    """A regex for `fragment` however it is spaced."""
    return r"\s*".join(re.escape(token) for token in _TOKEN.findall(fragment))


def _pattern(fragment: str) -> re.Pattern[str]:
    tokens = _TOKEN.findall(fragment)
    start = r"(?<!\w)" if tokens[0][0].isalnum() or tokens[0][0] == "_" else ""
    end = r"(?!\w)" if tokens[-1][-1].isalnum() or tokens[-1][-1] == "_" else ""
    return re.compile(start + _spaced(fragment) + end)


_COMPUTED: Final = (ast.Call, ast.BinOp, ast.Compare, ast.BoolOp, ast.Subscript)
_OPERAND: Final = r"[\w.]+(?:\([^()]*\))?"
#: A bare operation in backticks: `+ 1`, `- 1`, `* 2`, `+= 1`.
#: `-1` is a value, so a minus needs its space.
_OPERATION: Final = re.compile(r"(?:\+\s*|(?:-|\*|//|/|%)\s+|(?:\+|-|\*|//|/|%)=\s*)[\w.]+")
_AUGMENTED: Final = re.compile(r"(?<![\w`])[A-Za-z_][\w.\[\]]*\s*(?:\+|-|\*|//|/)=\s*[\w.\[\]]+")


def _own_expressions(learner_code: str) -> list[str]:
    """What the learner's code computes: each returned, assigned or tested
    expression that is more than a name or a value."""
    statements = [_statement(line) for line in learner_code.splitlines()]
    found: set[str] = set()
    for statement in statements:
        expression = _expression(statement) if statement is not None else None
        if isinstance(expression, _COMPUTED):
            source = ast.unparse(expression)
            if len(_flat(source)) >= _MIN_FRAGMENT:
                found.add(source)
    return sorted(found, key=len, reverse=True)


def _extended(expression: str) -> re.Pattern[str]:
    """`expression` with an operand joined to either side of it."""
    body = _spaced(expression)
    operator = r"\s*(?:\+|-|\*|//|/|%)\s*"
    return re.compile(rf"(?<![\w.])(?:{_OPERAND}{operator}{body}|{body}{operator}{_OPERAND})")


def _is_new_statement(source: str, theirs: str) -> bool:
    """Code the learner did not write that DOES something: returns, assigns a
    computed value, branches or loops. A name, a value, a call or a state
    ("`left_max = 2`") is how a diagnosis talks and is left alone."""
    if _flat(source) in theirs:
        return False
    statement = _statement(source)
    if statement is None:
        return False
    if isinstance(statement, (ast.Return, ast.AugAssign, ast.If, ast.While, ast.For)):
        return True
    if isinstance(statement, ast.Assign):
        return not _is_literal(statement.value)
    return False


def _is_literal(expression: ast.expr) -> bool:
    """A value written out (`2`, `[2, 0, 2]`, `-1`), not something computed."""
    try:
        ast.literal_eval(expression)
    except (ValueError, TypeError, SyntaxError):
        return False
    return True


def _is_their_code(block: str, own_lines: set[str]) -> bool:
    """Every line is one of theirs; a comment added to mark a line is not code."""
    lines = [_flat(line.split("#", 1)[0]) for line in block.splitlines()]
    lines = [line for line in lines if line]
    return bool(lines) and all(line in own_lines for line in lines)


def _is_new_code(block: str, theirs: str, flat_fragments: Sequence[str]) -> bool:
    """A fenced block that carries the fix: one of its lines, or Python that
    does something the learner did not write. A traceback, the sandbox's
    output or a test input is evidence for the diagnosis and is kept."""
    flat = _flat(block)
    if any(fragment in flat for fragment in flat_fragments):
        return True
    try:
        ast.parse(textwrap.dedent(block))
    except SyntaxError:
        return False
    return any(_is_new_statement(line, theirs) for line in block.splitlines())


def _tidy(text: str) -> str:
    """One placeholder where several landed side by side."""
    held = re.escape(WITHHELD)
    doubled = re.compile(held + r"(?:[\s,;]*(?:and|or)?\s*" + held + ")+")
    return re.sub(r"\n{3,}", "\n\n", doubled.sub(WITHHELD, text)).strip()


def redact_fix(text: str, learner_code: str | None, patched_code: str | None = None) -> str:
    """`text` with the fix to `learner_code` taken out of it.

    `patched_code` is the debugger's corrected program when it has one; its
    new lines are what is looked for. Without it only statements the learner
    did not write are recognised. Text with nothing to withhold is returned
    unchanged.
    """
    if not text.strip() or not learner_code or not learner_code.strip():
        return text
    theirs = _flat(learner_code)
    own_lines = {_flat(line) for line in learner_code.splitlines() if line.strip()}
    fragments = _fix_fragments(learner_code, patched_code) if patched_code else []
    flat_fragments = [_flat(fragment) for fragment in fragments]

    def fence(match: re.Match[str]) -> str:
        block = match.group(1)
        if _is_their_code(block, own_lines) or not _is_new_code(block, theirs, flat_fragments):
            return match.group(0)
        return WITHHELD

    def span(match: re.Match[str]) -> str:
        inner = match.group(1)
        flat = _flat(inner)
        if any(fragment in flat for fragment in flat_fragments):
            return WITHHELD
        if _OPERATION.fullmatch(inner.strip()) and flat not in theirs:
            # "the missing `+ 1`" is the change itself, said as an operation.
            return WITHHELD
        return WITHHELD if _is_new_statement(inner, theirs) else match.group(0)

    def unless_theirs(match: re.Match[str]) -> str:
        return match.group(0) if _flat(match.group(0)) in theirs else WITHHELD

    out = _SPAN.sub(span, _FENCE.sub(fence, text))
    for fragment in fragments:
        out = _pattern(fragment).sub(WITHHELD, out)
    for expression in _own_expressions(learner_code):
        out = _extended(expression).sub(unless_theirs, out)
    out = _AUGMENTED.sub(unless_theirs, out)
    out = out.replace(f"`{WITHHELD}`", WITHHELD)
    return out if out == text else _tidy(out)
