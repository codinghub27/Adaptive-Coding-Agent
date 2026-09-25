"""Static guard: `app/execution/` (and the graph node that dispatches to it)
must never run code on the host directly.

All code execution for untrusted submissions must go through the Docker
Engine API (docker-py) into the locked-down sandbox container. This AST scan
fails the build if anything under `app/execution/`, or `app/graph/nodes.py`
(whose `execute_code` node is the graph's only path to the sandbox), imports
`subprocess` or `pty`, calls `os.system`/`os.popen`/`os.exec*`/`os.spawn*`, or
calls the builtins `exec`, `eval`, or `compile`.
"""

from __future__ import annotations

import ast
from pathlib import Path

FORBIDDEN_MODULES = {"subprocess", "pty"}
FORBIDDEN_OS_ATTR_PREFIXES = ("system", "popen", "exec", "spawn")
FORBIDDEN_BUILTINS = {"exec", "eval", "compile"}

_APP_DIR = Path(__file__).resolve().parents[2] / "app"
EXECUTION_DIR = _APP_DIR / "execution"
GRAPH_NODES_FILE = _APP_DIR / "graph" / "nodes.py"


def _iter_python_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def _check_file(path: Path) -> list[str]:
    violations: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in FORBIDDEN_MODULES:
                    violations.append(f"{path}:{node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in FORBIDDEN_MODULES:
                violations.append(f"{path}:{node.lineno}: from {node.module} import ...")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in FORBIDDEN_BUILTINS:
                violations.append(f"{path}:{node.lineno}: call to builtin {func.id}()")
            elif (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "os"
                and any(
                    func.attr == prefix or func.attr.startswith(prefix)
                    for prefix in FORBIDDEN_OS_ATTR_PREFIXES
                )
            ):
                violations.append(f"{path}:{node.lineno}: call to os.{func.attr}()")

    return violations


def test_execution_package_never_runs_code_on_host() -> None:
    assert EXECUTION_DIR.is_dir(), f"expected {EXECUTION_DIR} to exist"

    all_violations: list[str] = []
    for path in _iter_python_files(EXECUTION_DIR):
        all_violations.extend(_check_file(path))

    assert not all_violations, "forbidden host-execution calls found:\n" + "\n".join(all_violations)


def test_graph_nodes_never_runs_code_on_host() -> None:
    assert GRAPH_NODES_FILE.is_file(), f"expected {GRAPH_NODES_FILE} to exist"

    violations = _check_file(GRAPH_NODES_FILE)

    assert not violations, "forbidden host-execution calls found:\n" + "\n".join(violations)
