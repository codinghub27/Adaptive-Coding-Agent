"""Direct unit tests for `docker/harness/run.py`'s pure logic (F2/F3/F4/F8).

Loaded via `importlib` from its file path (it lives outside the `app`
package and is never imported as `docker.harness.run` -- `docker` here is
this repo's top-level `docker/` directory, not the `docker` SDK package). No
sandbox/container is involved: these tests call the harness's own pure
helper functions directly, with test-authored objects standing in for what
would otherwise be an untrusted submission's return value -- never actual
untrusted code.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from typing import Any

HARNESS_FILE = Path(__file__).resolve().parents[2] / "docker" / "harness" / "run.py"


def _load_harness_module() -> Any:
    spec = importlib.util.spec_from_file_location("_aca_harness_run_unit", HARNESS_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_HARNESS: Any = _load_harness_module()


def _case(name: str = "c1", args: list[object] | None = None, expected: object = None) -> Any:
    return _HARNESS._Case(name=name, args=args or [], kwargs={}, expected=expected)


# --------------------------------------------------------------------------
# F2 -- `_to_json_safe` accepts only EXACT builtin types, never subclasses
# --------------------------------------------------------------------------


def test_to_json_safe_accepts_plain_builtins() -> None:
    ok, value = _HARNESS._to_json_safe([1, "a", True, None, {"x": 1.5}])
    assert ok is True
    assert value == [1, "a", True, None, {"x": 1.5}]


def test_to_json_safe_rejects_int_subclass_even_if_isinstance_int() -> None:
    class LyingInt(int):
        def __eq__(self, other: object) -> bool:  # noqa: ARG002
            return True

    ok, value = _HARNESS._to_json_safe(LyingInt(5))
    assert ok is False
    assert value is None


def test_to_json_safe_rejects_dict_subclass_key() -> None:
    class LyingStr(str):
        pass

    ok, _value = _HARNESS._to_json_safe({LyingStr("k"): 1})
    assert ok is False


def test_run_case_rejects_subclassed_return_with_forged_equality() -> None:
    """F4(a): `class C(int): __eq__ = lambda s, o: True` returned from the
    entrypoint must never be able to fake a pass via a forged comparison."""

    class LyingInt(int):
        def __eq__(self, other: object) -> bool:  # noqa: ARG002
            return True

        def __hash__(self) -> int:
            return int.__hash__(self)

    def entrypoint(x: int) -> object:
        del x
        return LyingInt(999)

    case = _case(args=[1], expected=2)
    report = _HARNESS._run_case(entrypoint, case)

    assert report["passed"] is False
    assert report["actual"] is None
    assert report["actual_sha256"] is None


def test_run_case_monkeypatched_json_equal_does_not_affect_actual_sha256() -> None:
    """F4(b): even if user code at load time monkeypatches the harness's own
    `_json_equal` (or anything else in `sys.modules['__main__']`), the
    `actual_sha256` the host verifies against is computed independently by
    `_run_case`/`_canonical`, not by calling back into mutable module state."""

    def _always_equal(a: object, b: object) -> bool:
        del a, b
        return True

    original = _HARNESS._json_equal
    _HARNESS._json_equal = _always_equal
    try:

        def entrypoint(x: int) -> int:
            del x
            return 42

        case = _case(args=[1], expected=1)  # wrong answer: 42 != 1
        report = _HARNESS._run_case(entrypoint, case)

        assert report["actual"] == 42
        expected_hash = hashlib.sha256(_HARNESS._canonical(1).encode("utf-8")).hexdigest()
        assert report["actual_sha256"] != expected_hash
    finally:
        _HARNESS._json_equal = original


# --------------------------------------------------------------------------
# F8 -- a single case's serialization failure can never crash the harness
# --------------------------------------------------------------------------


def test_run_case_self_referencing_list_fails_that_case_only() -> None:
    cyclic: list[object] = [1, 2]
    cyclic.append(cyclic)

    def entrypoint() -> object:
        return cyclic

    case = _case(args=[], expected=[1, 2])
    report = _HARNESS._run_case(entrypoint, case)

    assert report["passed"] is False
    assert report["error"] is not None
    assert report["actual_sha256"] is None
    # The report must remain plain builtins so `json.dumps` can never fail.
    import json

    json.dumps(report)


# --------------------------------------------------------------------------
# F3 -- a correct, large-but-valid result still round-trips and passes
# --------------------------------------------------------------------------


def test_run_case_large_valid_list_passes_and_hashes_match() -> None:
    big = list(range(20_000))

    def entrypoint() -> object:
        return list(big)

    case = _case(args=[], expected=big)
    report = _HARNESS._run_case(entrypoint, case)

    assert report["passed"] is True
    assert report["actual_truncated"] is True  # canonical text exceeds the 4000-char budget
    assert report["actual"] is None
    assert (
        report["actual_sha256"]
        == hashlib.sha256(_HARNESS._canonical(big).encode("utf-8")).hexdigest()
    )
