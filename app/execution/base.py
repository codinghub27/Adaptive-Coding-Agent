"""Lightweight contracts for the code-execution sandbox.

Deliberately depends only on `app.schemas.execution` and `typing` -- never on
`docker`, which pulls in a heavyweight SDK. `app.execution.runner` (the
language-agnostic dispatcher) and its tests need the `SandboxBackend`/
`RawRun` shapes without paying for the `docker` import; `app.execution.sandbox`
(the one place that talks to the Docker Engine API) re-imports `RawRun` from
here so `app.execution.sandbox.RawRun` keeps working unchanged.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pydantic import JsonValue

from app.schemas.execution import ExecutionRequest, ExecutionResult

__all__ = ["CodeRunner", "RawRun", "SandboxBackend", "canonical", "truncate_text"]


def truncate_text(text: str, max_chars: int, suffix: str = "…[truncated]") -> tuple[str, bool]:
    """Cap `text` at `max_chars`, appending `suffix` when truncated.

    The single shared truncation helper for the host side (`app.execution.
    runner` and `app.execution.verification`); the in-container harness
    (`docker/harness/run.py`) keeps its own copy since it can never import
    `app`.
    """
    if len(text) <= max_chars:
        return text, False
    keep = max(max_chars - len(suffix), 0)
    return text[:keep] + suffix, True


def _canonicalize_numbers(value: JsonValue) -> JsonValue:
    """Recursively fold whole-valued floats (e.g. `2.0`) into ints (`2`) so
    `2` and `2.0` canonicalize identically, while leaving bools untouched
    (bool is a subclass of int but must never be conflated with a number)."""
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        if value == value and value not in (float("inf"), float("-inf")) and value.is_integer():
            return int(value)
        return value
    if isinstance(value, list):
        return [_canonicalize_numbers(item) for item in value]
    return {key: _canonicalize_numbers(item) for key, item in value.items()}


def canonical(value: JsonValue) -> str:
    """Canonical JSON text for a JSON value: numerically-equal floats/ints
    collapse to the same representation, keys are sorted, and separators are
    fixed -- so byte-identical `canonical()` output means the values are
    equal under the harness/host's shared JSON equality rules. Must be
    reimplemented (not imported) by the harness, which cannot import `app`;
    see `docker/harness/run.py::_canonical` and the parity test asserting the
    two agree.
    """
    return json.dumps(
        _canonicalize_numbers(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=True,
    )


@dataclass(frozen=True, slots=True)
class RawRun:
    """The process-level outcome of a single sandbox container run."""

    exit_code: int | None
    stdout: bytes
    stderr: bytes
    duration_ms: float
    timed_out: bool
    oom_killed: bool
    output_truncated: bool


@runtime_checkable
class SandboxBackend(Protocol):
    """What `SandboxRunner` needs from a per-language container backend."""

    async def run(self, env: Mapping[str, str], timeout_s: float) -> RawRun: ...


@runtime_checkable
class CodeRunner(Protocol):
    """The high-level, language-agnostic code-execution entrypoint."""

    async def run(self, request: ExecutionRequest) -> ExecutionResult: ...
