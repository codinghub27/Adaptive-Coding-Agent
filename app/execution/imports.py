"""Which packages a piece of code needs that the sandbox does not have.

Read with `ast` only: nothing is imported or run on the API host. The sandbox
image carries the standard library and nothing else, so code written for a
framework (FastAPI, Django, pandas, ...) cannot run there. Run anyway, it
stops at its first import, and that `ModuleNotFoundError` used to be reported
as the learner's bug, or a framework example was dropped as "did not run
cleanly" (read live: a FastAPI question).
"""

import ast
import sys
from typing import Final

__all__ = ["missing_packages"]

#: Top-level modules the sandbox image has besides the standard library.
_IN_IMAGE: Final = frozenset[str]()


def missing_packages(code: str | None) -> list[str]:
    """Top-level packages `code` imports that the sandbox lacks, in order of
    first import. Empty when the code does not parse (the run reports that)."""
    if not code or not code.strip():
        return []
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return []
    found: list[str] = []
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        for name in names:
            top = name.split(".", 1)[0]
            if top in sys.stdlib_module_names or top in _IN_IMAGE or top in found:
                continue
            found.append(top)
    return found
