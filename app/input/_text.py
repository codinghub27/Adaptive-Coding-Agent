"""Shared low-level text helpers used across `app.input` submodules.

Pure string/regex utilities only -- no LLM calls, no I/O. Everything these
functions touch is **untrusted user data**: they only parse and reshape
text, never execute or evaluate it.
"""

import re
from typing import Final

__all__ = ["extract_json_object", "looks_like_python_error", "none_if_blank"]


def none_if_blank(value: str | None) -> str | None:
    """Return `None` for `None`/an all-whitespace string; else the stripped value."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


# Canonical Python-traceback/bare-exception detector (originally normalize.py's).
_PY_ERROR_LINE_RE: Final = re.compile(r"^[A-Za-z_][\w.]*(Error|Exception)\b(:.*)?$", re.MULTILINE)


def looks_like_python_error(error_text: str) -> bool:
    """True if `error_text` looks like a Python traceback or bare exception line."""
    return "Traceback (most recent call last):" in error_text or bool(
        _PY_ERROR_LINE_RE.search(error_text)
    )


_JSON_FENCE_RE: Final = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL | re.IGNORECASE)


def extract_json_object(raw: str) -> str | None:
    """Pull the outermost `{...}` JSON object out of `raw`.

    Strips a ```json ... ``` (or bare ``` ... ```) fence if present, and
    tolerates leading prose (e.g. model reasoning) before the fence or the
    object itself. Returns `None` if no `{...}` span can be found.
    """
    text = raw.strip()

    fence_match = _JSON_FENCE_RE.search(text)
    if fence_match is not None:
        text = fence_match.group(1).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    return text[start : end + 1]
