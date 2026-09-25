"""In-container test harness for the Adaptive Coding Agent sandbox.

This is the ONLY place untrusted user code is executed, and it only ever runs
inside the locked-down `aca-sandbox` container (see `docker/sandbox.Dockerfile`).
It is stdlib-only by design: the sandbox image has no `pip`, and this module
must never import anything from `app`.

Wire protocol
--------------
Input (environment variables, consumed and deleted before any user code runs):
    ACA_PAYLOAD -- base64 (standard alphabet) of a UTF-8 JSON document:
        {"version": 1, "language": "python", "code": str,
         "tests": null | {"entrypoint": str, "cases": [
             {"name": str, "args": [...], "kwargs": {...}, "expected": ...}
         ]}}
    ACA_MARKER  -- a per-run random token used to delimit the final report
                   line so it can be located unambiguously in stdout.

Output: exactly one line written to a duplicated stdout file descriptor
(captured before any user code can touch fd 1):
    <ACA_MARKER><json report><ACA_MARKER>\n

Exit codes: 0 whenever the harness itself completed (pass or fail alike);
2 only if the protocol environment variables are missing, in which case
nothing is written.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import copy
import hashlib
import io
import json
import os
import sys
import time
import types
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeAlias, cast

JsonValue: TypeAlias = "str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]"

_MAX_STRING_LEN = 2000
_TRUNCATION_SUFFIX = "…[truncated]"
_MAX_CASES = 100
_SOLUTION_FILENAME = "solution.py"
#: Per-case report budget so the whole report (as many as `_MAX_CASES` cases)
#: stays small -- see the module docstring's wire protocol note.
_MAX_CASE_STDOUT_LEN = 1000
_MAX_ACTUAL_REPR_LEN = 500
_MAX_ACTUAL_CANONICAL_LEN = 4000


# --------------------------------------------------------------------------
# Small shared helpers
# --------------------------------------------------------------------------


def _truncate(value: str, max_len: int = _MAX_STRING_LEN) -> str:
    """Cap a string field at `max_len` chars, appending a truncation marker."""
    if len(value) <= max_len:
        return value
    keep = max(max_len - len(_TRUNCATION_SUFFIX), 0)
    return value[:keep] + _TRUNCATION_SUFFIX


def _traceback_lineno(exc: BaseException) -> int | None:
    """Return the deepest traceback line number inside solution.py, if any.

    User tracebacks must never leak harness file paths, so only frames whose
    filename is exactly "solution.py" (set via compile(..., "solution.py",
    "exec")) are considered.
    """
    lineno: int | None = None
    tb = exc.__traceback__
    while tb is not None:
        if tb.tb_frame.f_code.co_filename == _SOLUTION_FILENAME:
            lineno = tb.tb_lineno
        tb = tb.tb_next
    return lineno


def _safe_str(exc: BaseException) -> str:
    """`str(exc)`, but never raise: user `__str__` overrides can misbehave."""
    try:
        return str(exc)
    except BaseException:  # noqa: BLE001 - must never crash the harness
        return f"<unprintable {type(exc).__name__}>"


def _safe_repr(obj: object) -> str:
    """`repr(obj)`, but never raise: user `__repr__` overrides can misbehave."""
    try:
        return repr(obj)
    except BaseException:  # noqa: BLE001 - must never crash the harness
        return f"<unprintable {type(obj).__name__}>"


def _make_error(error_type: str, message: str, lineno: int | None) -> dict[str, JsonValue]:
    return {"type": error_type, "message": _truncate(message), "lineno": lineno}


def _json_equal(a: JsonValue, b: JsonValue) -> bool:
    """Strict JSON equality: bool is distinct from int/float, but int==float
    numeric equality holds (2 == 2.0). Lists compare elementwise in order;
    dicts compare by key set + recursively-equal values."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return False
        return all(_json_equal(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, dict) and isinstance(b, dict):
        if a.keys() != b.keys():
            return False
        return all(_json_equal(a[key], b[key]) for key in a)
    return type(a) is type(b) and a == b


def _to_json_safe(value: object) -> tuple[bool, JsonValue]:
    """Recursively convert a Python return value to a JSON-safe value.

    Only EXACT builtin types are accepted -- `type(v) is int`, not
    `isinstance(v, int)` -- so a hostile subclass (e.g. `class C(int):
    __eq__ = lambda s, o: True`) can never impersonate a plain value and
    smuggle a forged comparison result past the host's hash check. Tuples
    become lists. Dict keys must be exactly `str`. Anything else that cannot
    be represented as JSON causes ok=False.
    """
    value_type = type(value)
    if value is None or value_type in (bool, int, float, str):
        return True, cast("JsonValue", value)
    if value_type in (list, tuple):
        ok = True
        items: list[JsonValue] = []
        for item in cast("list[object] | tuple[object, ...]", value):
            item_ok, item_value = _to_json_safe(item)
            ok = ok and item_ok
            items.append(item_value)
        return ok, items
    if value_type is dict:
        ok = True
        mapping: dict[str, JsonValue] = {}
        for key, item in cast("dict[object, object]", value).items():
            if type(key) is not str:
                ok = False
                continue
            item_ok, item_value = _to_json_safe(item)
            ok = ok and item_ok
            mapping[key] = item_value
        return ok, mapping
    return False, None


def _canonicalize_numbers(value: JsonValue) -> JsonValue:
    """Recursively fold whole-valued floats into ints so `2` and `2.0`
    canonicalize identically. Must stay byte-for-byte in sync with
    `app.execution.base.canonical`'s `_canonicalize_numbers` -- see
    `tests/execution/test_verification.py`'s harness/host parity test."""
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        if value == value and value not in (float("inf"), float("-inf")) and value.is_integer():
            return int(value)
        return value
    if isinstance(value, list):
        return [_canonicalize_numbers(item) for item in value]
    return {key: _canonicalize_numbers(item) for key, item in value.items()}


def _canonical(value: JsonValue) -> str:
    """Canonical JSON text for a JSON value -- see `app.execution.base.
    canonical`'s docstring; this is the harness's own copy (it cannot import
    `app`) and must produce byte-identical output for the same value."""
    return json.dumps(
        _canonicalize_numbers(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=True,
    )


# --------------------------------------------------------------------------
# Payload decoding + validation
# --------------------------------------------------------------------------


@dataclass(slots=True)
class _Case:
    name: str
    args: list[JsonValue]
    kwargs: dict[str, JsonValue]
    expected: JsonValue


@dataclass(slots=True)
class _Tests:
    entrypoint: str
    cases: list[_Case]


@dataclass(slots=True)
class _Payload:
    code: str
    tests: _Tests | None


def _decode_payload(raw_b64: str) -> tuple[dict[str, JsonValue] | None, str | None]:
    """Base64-decode + UTF-8-decode + JSON-parse the payload envelope."""
    try:
        raw_bytes = base64.b64decode(raw_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        return None, f"invalid base64 payload: {exc}"
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, f"payload is not valid utf-8: {exc}"
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"payload is not valid json: {exc}"
    if not isinstance(parsed, dict):
        return None, "payload must be a json object"
    return cast(dict[str, JsonValue], parsed), None


def _validate_case(item: JsonValue) -> tuple[_Case | None, str | None]:
    if not isinstance(item, dict):
        return None, "each test case must be an object"
    name = item.get("name")
    if not isinstance(name, str):
        return None, "case 'name' must be a string"
    args = item.get("args")
    if not isinstance(args, list):
        return None, "case 'args' must be a list"
    kwargs = item.get("kwargs")
    if not isinstance(kwargs, dict):
        return None, "case 'kwargs' must be an object"
    if "expected" not in item:
        return None, "case must include 'expected'"
    expected = item["expected"]
    return (
        _Case(
            name=name,
            args=cast("list[JsonValue]", args),
            kwargs=cast("dict[str, JsonValue]", kwargs),
            expected=expected,
        ),
        None,
    )


def _validate_tests(tests_raw: JsonValue) -> tuple[_Tests | None, str | None]:
    if not isinstance(tests_raw, dict):
        return None, "'tests' must be an object or null"
    entrypoint = tests_raw.get("entrypoint")
    if not isinstance(entrypoint, str):
        return None, "'tests.entrypoint' must be a string"
    cases_raw = tests_raw.get("cases")
    if not isinstance(cases_raw, list):
        return None, "'tests.cases' must be a list"
    if len(cases_raw) > _MAX_CASES:
        return None, f"too many test cases (max {_MAX_CASES})"
    cases: list[_Case] = []
    for item in cases_raw:
        case, case_err = _validate_case(item)
        if case_err is not None or case is None:
            return None, case_err or "invalid test case"
        cases.append(case)
    return _Tests(entrypoint=entrypoint, cases=cases), None


def _validate_payload(payload: dict[str, JsonValue]) -> tuple[_Payload | None, str | None]:
    if payload.get("version") != 1:
        return None, "unsupported or missing 'version' (must be 1)"
    if payload.get("language") != "python":
        return None, "unsupported or missing 'language' (must be 'python')"
    code = payload.get("code")
    if not isinstance(code, str):
        return None, "'code' must be a string"
    tests_raw = payload.get("tests")
    if tests_raw is None:
        return _Payload(code=code, tests=None), None
    tests, tests_err = _validate_tests(tests_raw)
    if tests_err is not None or tests is None:
        return None, tests_err or "invalid 'tests'"
    return _Payload(code=code, tests=tests), None


# --------------------------------------------------------------------------
# Compile + load
# --------------------------------------------------------------------------


def _compile_code(code: str) -> tuple[types.CodeType | None, dict[str, JsonValue] | None]:
    try:
        compiled = compile(code, _SOLUTION_FILENAME, "exec")
    except BaseException as exc:  # noqa: BLE001 - must report, never crash the harness
        lineno = exc.lineno if isinstance(exc, SyntaxError) else _traceback_lineno(exc)
        return None, _make_error(type(exc).__name__, _safe_str(exc), lineno)
    return compiled, None


@dataclass(slots=True)
class _LoadResult:
    module: types.ModuleType | None
    stdout: str
    stderr: str
    error: dict[str, JsonValue] | None


def _load_module(compiled: types.CodeType, *, script_mode: bool) -> _LoadResult:
    module_name = "__main__" if script_mode else "solution"
    module = types.ModuleType(module_name)
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
            exec(compiled, module.__dict__)  # noqa: S102 - the harness's sole purpose
    except BaseException as exc:  # noqa: BLE001 - must report, never crash the harness
        return _LoadResult(
            module=None,
            stdout=_truncate(out_buf.getvalue()),
            stderr=_truncate(err_buf.getvalue()),
            error=_make_error(type(exc).__name__, _safe_str(exc), _traceback_lineno(exc)),
        )
    return _LoadResult(
        module=module,
        stdout=_truncate(out_buf.getvalue()),
        stderr=_truncate(err_buf.getvalue()),
        error=None,
    )


# --------------------------------------------------------------------------
# Test case execution
# --------------------------------------------------------------------------


def _empty_case_result(
    *, name: str, stdout: str, duration_ms: float, error: dict[str, JsonValue]
) -> dict[str, JsonValue]:
    return {
        "name": name,
        "passed": False,
        "actual": None,
        "actual_repr": "",
        "actual_sha256": None,
        "actual_truncated": False,
        "error": error,
        "stdout": stdout,
        "duration_ms": duration_ms,
    }


def _run_case(fn: Callable[..., object], case: _Case) -> dict[str, JsonValue]:
    args = copy.deepcopy(case.args)
    kwargs = copy.deepcopy(case.kwargs)
    out_buf = io.StringIO()
    err_buf = io.StringIO()

    error: dict[str, JsonValue] | None = None
    actual_raw: object = None
    start = time.perf_counter()
    try:
        with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
            actual_raw = fn(*args, **kwargs)
    except BaseException as exc:  # noqa: BLE001 - must report, never crash the harness
        error = _make_error(type(exc).__name__, _safe_str(exc), _traceback_lineno(exc))
    duration_ms = (time.perf_counter() - start) * 1000

    stdout_captured = _truncate(out_buf.getvalue(), _MAX_CASE_STDOUT_LEN)

    if error is not None:
        return _empty_case_result(
            name=case.name, stdout=stdout_captured, duration_ms=duration_ms, error=error
        )

    # Everything below (normalizing, canonicalizing, comparing, repr'ing an
    # arbitrary user-returned object) can raise on adversarial values --
    # deeply nested/self-referencing structures, malicious __eq__/__repr__ --
    # so it's wrapped as a whole: any exception here marks just this one case
    # failed rather than ever risking the harness itself, or the final
    # `json.dumps(report)`.
    try:
        ok, normalized = _to_json_safe(actual_raw)
        actual_repr = _truncate(_safe_repr(actual_raw), _MAX_ACTUAL_REPR_LEN)
        if not ok:
            return {
                "name": case.name,
                "passed": False,
                "actual": None,
                "actual_repr": actual_repr,
                "actual_sha256": None,
                "actual_truncated": False,
                "error": None,
                "stdout": stdout_captured,
                "duration_ms": duration_ms,
            }
        canonical_text = _canonical(normalized)
        actual_sha256 = hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()
        actual_truncated = len(canonical_text) > _MAX_ACTUAL_CANONICAL_LEN
        actual: JsonValue = None if actual_truncated else normalized
        passed = _json_equal(normalized, case.expected)
    except BaseException as exc:  # noqa: BLE001 - must report, never crash the harness
        error = _make_error(type(exc).__name__, _safe_str(exc), None)
        return _empty_case_result(
            name=case.name, stdout=stdout_captured, duration_ms=duration_ms, error=error
        )

    return {
        "name": case.name,
        "passed": passed,
        "actual": actual,
        "actual_repr": actual_repr,
        "actual_sha256": actual_sha256,
        "actual_truncated": actual_truncated,
        "error": None,
        "stdout": stdout_captured,
        "duration_ms": duration_ms,
    }


# --------------------------------------------------------------------------
# Report assembly
# --------------------------------------------------------------------------


def _base_report(
    *,
    phase: str,
    error: dict[str, JsonValue] | None,
    stdout: str = "",
    stderr: str = "",
    cases: list[dict[str, JsonValue]] | None = None,
) -> dict[str, JsonValue]:
    return {
        "version": 1,
        "phase": phase,
        "error": error,
        "stdout": stdout,
        "stderr": stderr,
        "cases": cast("list[JsonValue]", cases) if cases is not None else [],
    }


def _protocol_report(message: str) -> dict[str, JsonValue]:
    return _base_report(phase="protocol", error=_make_error("ProtocolError", message, None))


def _run(payload_b64: str) -> dict[str, JsonValue]:
    payload, decode_err = _decode_payload(payload_b64)
    if decode_err is not None or payload is None:
        return _protocol_report(decode_err or "unable to decode payload")

    parsed, validate_err = _validate_payload(payload)
    if validate_err is not None or parsed is None:
        return _protocol_report(validate_err or "invalid payload")

    compiled, compile_error = _compile_code(parsed.code)
    if compile_error is not None:
        return _base_report(phase="compile", error=compile_error)
    assert compiled is not None  # noqa: S101 - invariant of _compile_code's return contract

    load_result = _load_module(compiled, script_mode=parsed.tests is None)
    if load_result.error is not None:
        return _base_report(
            phase="load",
            error=load_result.error,
            stdout=load_result.stdout,
            stderr=load_result.stderr,
        )

    if parsed.tests is None:
        return _base_report(
            phase="script",
            error=None,
            stdout=load_result.stdout,
            stderr=load_result.stderr,
        )

    module = load_result.module
    candidate: object | None = module.__dict__.get(parsed.tests.entrypoint) if module else None
    if not callable(candidate):
        error = _make_error(
            "MissingEntrypoint",
            f"entrypoint {parsed.tests.entrypoint!r} not found or not callable",
            None,
        )
        return _base_report(
            phase="entrypoint",
            error=error,
            stdout=load_result.stdout,
            stderr=load_result.stderr,
        )

    cases_report = [_run_case(candidate, case) for case in parsed.tests.cases]
    return _base_report(
        phase="tests",
        error=None,
        stdout=load_result.stdout,
        stderr=load_result.stderr,
        cases=cases_report,
    )


# --------------------------------------------------------------------------
# Entrypoint
# --------------------------------------------------------------------------


def main() -> int:
    marker = os.environ.get("ACA_MARKER")
    payload_b64 = os.environ.get("ACA_PAYLOAD")
    if marker is None or payload_b64 is None:
        return 2

    # Scrub the protocol env vars before any user code can possibly run.
    del os.environ["ACA_MARKER"]
    del os.environ["ACA_PAYLOAD"]

    # Duplicate the real stdout fd now, so the final report can always be
    # written even if user code closes or redirects sys.stdout.
    real_out = os.fdopen(os.dup(1), "w", encoding="utf-8")

    try:
        report = _run(payload_b64)
    except BaseException as exc:  # noqa: BLE001 - the harness must never crash silently
        report = _protocol_report(f"internal harness error: {type(exc).__name__}: {_safe_str(exc)}")

    line = f"{marker}{json.dumps(report, ensure_ascii=True)}{marker}\n"
    real_out.write(line)
    real_out.flush()

    # From here, no user code (atexit handlers, lingering threads) must be
    # allowed to write to stdout/stderr after the report line. Flush both
    # best-effort, then terminate immediately without running interpreter
    # shutdown (which would run atexit handlers and non-daemon threads).
    with contextlib.suppress(Exception):
        sys.stdout.flush()
    with contextlib.suppress(Exception):
        sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    exit_code = main()
    if exit_code != 0:
        raise SystemExit(exit_code)
