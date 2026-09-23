"""Deterministic, LLM-free normalization of raw user text into `StructuredInput`.

Everything in this module is pure text processing: regexes and string
manipulation only. It never executes, evaluates, or otherwise treats the
supplied text as instructions — code, error messages, and problem statements
here are **untrusted user data**, handled the same way regardless of what
they claim or ask for.
"""

import re
from typing import Final

from app.input._text import looks_like_python_error, none_if_blank
from app.schemas.input import CodeBlock, StructuredInput

__all__ = ["MAX_TEXT_CHARS", "normalize_text", "guess_language", "merge_inputs"]

MAX_TEXT_CHARS: Final = 50_000

# --------------------------------------------------------------------------
# Language aliases
# --------------------------------------------------------------------------

_LANGUAGE_ALIASES: Final[dict[str, str]] = {
    "py": "python",
    "js": "javascript",
    "ts": "typescript",
    "c++": "cpp",
    "cc": "cpp",
}

_NON_CODE_FENCE_LANGS: Final = {"text", "console", "bash", "shell", "log", "output"}


def _normalize_language(raw: str) -> str | None:
    """Lowercase and alias-normalize a language tag; `None` for blank input."""
    key = raw.strip().lower()
    if not key:
        return None
    return _LANGUAGE_ALIASES.get(key, key)


# --------------------------------------------------------------------------
# guess_language
# --------------------------------------------------------------------------

_LANGUAGE_SIGNATURES: Final[dict[str, tuple[re.Pattern[str], ...]]] = {
    "python": (
        re.compile(r"\bdef \w+\("),
        re.compile(r"\bself\b"),
        re.compile(r"^\s*import \w+", re.MULTILINE),
        re.compile(r":\s*$", re.MULTILINE),
        re.compile(r"\bprint\("),
        re.compile(r"\belif\b"),
    ),
    "typescript": (
        re.compile(r"\binterface \w+"),
        re.compile(r":\s*(string|number|boolean|any|void)\b"),
        re.compile(r"\bexport (default )?(function|class|const)\b"),
    ),
    "javascript": (
        re.compile(r"\bfunction\s*\w*\("),
        re.compile(r"\bconst \w+\s*="),
        re.compile(r"\blet \w+\s*="),
        re.compile(r"=>"),
        re.compile(r"\bconsole\.log\("),
    ),
    "java": (
        re.compile(r"\bpublic (static )?(class|void|int|String)\b"),
        re.compile(r"\bSystem\.out\.println\("),
        re.compile(r";\s*$", re.MULTILINE),
    ),
    "cpp": (
        re.compile(r"#include\s*<\w+>"),
        re.compile(r"\bstd::"),
        re.compile(r"\bcout\s*<<"),
        re.compile(r"\bint main\s*\("),
    ),
    "c": (
        re.compile(r"#include\s*<\w+\.h>"),
        re.compile(r"\bprintf\("),
        re.compile(r"\bint main\s*\("),
    ),
    "go": (
        re.compile(r"\bfunc \w+\("),
        re.compile(r"\bpackage \w+"),
        re.compile(r":=\s"),
    ),
    "rust": (
        re.compile(r"\bfn \w+\("),
        re.compile(r"\blet mut\b"),
        re.compile(r"->\s*\w"),
        re.compile(r"::<"),
    ),
}


def guess_language(code: str) -> str | None:
    """Best-effort language guess from simple regex scoring; `None` if unclear."""
    best_lang: str | None = None
    best_score = 0
    for lang, patterns in _LANGUAGE_SIGNATURES.items():
        score = sum(1 for pattern in patterns if pattern.search(code))
        if score > best_score:
            best_score = score
            best_lang = lang
    return best_lang


# --------------------------------------------------------------------------
# Span removal helpers
# --------------------------------------------------------------------------


def _remove_spans(text: str, matches: list[re.Match[str]]) -> str:
    """Return `text` with each matched span replaced by a paragraph break."""
    if not matches:
        return text
    parts: list[str] = []
    last = 0
    for match in matches:
        parts.append(text[last : match.start()])
        parts.append("\n")
        last = match.end()
    parts.append(text[last:])
    return "".join(parts)


_BLANK_RUN_RE = re.compile(r"\n{3,}")


def _collapse_blank_lines(text: str) -> str:
    return _BLANK_RUN_RE.sub("\n\n", text).strip()


# --------------------------------------------------------------------------
# Fenced code blocks
#
# This is a linear, line-based scanner rather than a single monolithic regex.
# A single regex with a non-greedy `.*?` body and a backreferenced closing
# fence is quadratic on adversarial input: many opening-fence-looking lines
# that never close force the engine to rescan all the way to the end of the
# text from every candidate start position. Scanning line-by-line with an
# explicit open/closed state visits each line once, so it stays O(n) even
# for pathological input (e.g. thousands of unclosed fences).
#
# An unclosed fence (no matching closing marker before end of text) is left
# as plain text: everything from the opening marker line through the end of
# the text is passed through untouched rather than guessed to be code.
# --------------------------------------------------------------------------

_FENCE_OPEN_RE = re.compile(r"^(`{3,}|~{3,})[ \t]*([A-Za-z0-9_+#.\-]*)[ \t]*$")


def _is_fence_close(line: str, fence_char: str, min_len: int) -> bool:
    stripped = line.strip(" \t")
    if len(stripped) < min_len:
        return False
    return all(c == fence_char for c in stripped)


def _extract_fenced_blocks(text: str) -> tuple[list[CodeBlock], list[str], str]:
    lines = text.split("\n")
    code_blocks: list[CodeBlock] = []
    error_texts: list[str] = []
    output_lines: list[str] = []
    n = len(lines)
    i = 0
    while i < n:
        open_match = _FENCE_OPEN_RE.match(lines[i])
        if open_match is None:
            output_lines.append(lines[i])
            i += 1
            continue

        fence_marker = open_match.group(1)
        fence_char = fence_marker[0]
        fence_len = len(fence_marker)
        raw_lang = open_match.group(2).strip()

        body_lines: list[str] = []
        j = i + 1
        closed = False
        while j < n:
            if _is_fence_close(lines[j], fence_char, fence_len):
                closed = True
                break
            body_lines.append(lines[j])
            j += 1

        if not closed:
            # No closing marker anywhere in the rest of the text: leave the
            # opening line as plain text and keep scanning from there. Do
            # NOT re-scan the buffered body_lines again -- append them as-is
            # so this whole unclosed-fence tail is still only visited once.
            output_lines.append(lines[i])
            output_lines.extend(body_lines)
            i = j
            continue

        content = "\n".join(body_lines).strip("\n")
        if content.strip():
            lang_lower = raw_lang.lower()
            looks_error = _looks_like_error(content)
            if looks_error and (not raw_lang or lang_lower in _NON_CODE_FENCE_LANGS):
                error_texts.append(content.strip())
            else:
                language = _normalize_language(raw_lang) if raw_lang else guess_language(content)
                code_blocks.append(CodeBlock(content=content, language=language))
        output_lines.append("")  # paragraph break where the fenced block was
        i = j + 1

    remaining = "\n".join(output_lines)
    return code_blocks, error_texts, remaining


# --------------------------------------------------------------------------
# Error extraction
# --------------------------------------------------------------------------

_ERROR_PATTERNS: Final[list[re.Pattern[str]]] = [
    # Python traceback: header through the final "SomeError: msg" line. The
    # inner `(?:(?!Traceback \(most recent call last\):).*\n)*?` lookahead
    # stops the non-greedy body from being able to run past a *later*
    # "Traceback ..." header. Without it, many repeated (never-terminated)
    # traceback headers back-to-back force the engine to rescan all the way
    # to the end of the text from every header position -- O(n^2) on
    # adversarial input. With the lookahead, each header position only ever
    # scans up to the next header (or end of text), which is linear overall.
    re.compile(
        r"Traceback \(most recent call last\):\n"
        r"(?:(?!Traceback \(most recent call last\):).*\n)*?"
        r"[A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt|Warning)\b(?::.*)?(?=\n|$)"
    ),
    # Java: `Exception in thread "main" ...` followed by `\tat ...` frames.
    re.compile(r'Exception in thread "[^"\n]*"[^\n]*\n(?:[ \t]*at [^\n]*\n?)+'),
    # JS-style stack: `SomeError: message` followed by `  at ...` frames.
    re.compile(
        r"^[A-Za-z_][\w.]*(?:Error|Exception)\b:[^\n]*\n(?:[ \t]+at [^\n]*\n?)+",
        re.MULTILINE,
    ),
    # Rust: `error[E0382]: ...`
    re.compile(r"^error\[E\d+\]:.*$", re.MULTILINE),
    # gcc/clang: `file.cpp:12:5: error: ...`
    re.compile(r"^\S+:\d+:\d+: error:.*$", re.MULTILINE),
    # Bare exception line on its own, e.g. `IndexError: list index out of range`.
    re.compile(
        r"^[A-Za-z_][\w.]*(Error|Exception|Exit|Interrupt|Warning)\b(:.*)?$",
        re.MULTILINE,
    ),
]


def _looks_like_error(content: str) -> bool:
    return any(pattern.search(content) for pattern in _ERROR_PATTERNS)


def _extract_errors(text: str) -> tuple[list[str], str]:
    errors: list[str] = []
    remaining = text
    for pattern in _ERROR_PATTERNS:
        matches = list(pattern.finditer(remaining))
        if not matches:
            continue
        errors.extend(match.group(0).strip() for match in matches)
        remaining = _remove_spans(remaining, matches)
    return errors, remaining


# --------------------------------------------------------------------------
# Unfenced code
# --------------------------------------------------------------------------

_CODE_SIMPLE_PREFIXES: Final = (
    "def ",
    "class ",
    "import ",
    "return",
    "elif",
    "else:",
    "try:",
    "except",
    "@",
)
_CODE_BLOCK_PREFIXES: Final = ("if ", "for ", "while ", "with ")
_CODE_MARKER_SUBSTRINGS: Final = (
    "function ",
    "const ",
    "let ",
    "public ",
    "private ",
    "#include",
    "int main(",
    "=>",
)
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\[[^\]\n]*\])?\s*=\s*[^=\s].*$")
_INDENT_RE = re.compile(r"^( {2,}|\t)")
# A standalone call/expression statement: an identifier/attribute chain with
# no space before its opening `(`, e.g. `print(get_item(items, 5))` or
# `console.log(add(1, 2));` or `nums.sort()` or `obj.x.y(1)`. Anchored at
# both ends so prose like "call foo(x) to check" (extra words after the
# closing paren) or "I tried print(x) but it failed?" (leading words before
# the identifier) never matches.
_CALL_STATEMENT_RE = re.compile(r"^[A-Za-z_][\w.]*(\[[^\]\n]*\])*\(.*\)\s*;?$")


def _is_code_start_line(stripped: str) -> bool:
    if stripped.startswith("from ") and " import" in stripped:
        return True
    if any(stripped.startswith(prefix) for prefix in _CODE_SIMPLE_PREFIXES):
        return True
    return any(
        stripped.startswith(prefix) for prefix in _CODE_BLOCK_PREFIXES
    ) and stripped.endswith(":")


def _has_code_markers(stripped: str) -> bool:
    if "{" in stripped or "}" in stripped:
        return True
    if stripped.endswith(";"):
        return True
    if any(marker in stripped for marker in _CODE_MARKER_SUBSTRINGS):
        return True
    if _ASSIGNMENT_RE.match(stripped):
        return True
    return bool(_CALL_STATEMENT_RE.match(stripped))


def _classify_lines(lines: list[str]) -> list[bool]:
    flags = [False] * len(lines)
    prev_code = False
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped:
            continue
        is_indented_continuation = bool(_INDENT_RE.match(raw)) and prev_code
        if _is_code_start_line(stripped) or _has_code_markers(stripped) or is_indented_continuation:
            flags[i] = True
            prev_code = True
        else:
            prev_code = False
    return flags


def _extract_unfenced_code(text: str) -> tuple[list[CodeBlock], str]:
    lines = text.split("\n")
    code_like = _classify_lines(lines)
    blocks: list[CodeBlock] = []
    removed: set[int] = set()
    n = len(lines)
    i = 0
    while i < n:
        if not lines[i].strip() or not code_like[i]:
            i += 1
            continue
        start = i
        j = i
        while j < n and (code_like[j] or not lines[j].strip()):
            j += 1
        end = j
        while end > start and not lines[end - 1].strip():
            end -= 1
        code_count = sum(1 for k in range(start, end) if code_like[k])
        if code_count >= 2:
            content = "\n".join(lines[start:end]).strip("\n")
            blocks.append(CodeBlock(content=content, language=guess_language(content)))
            removed.update(range(start, end))
        i = end if end > i else i + 1
    remaining = "\n".join("" if idx in removed else lines[idx] for idx in range(n))
    return blocks, remaining


# --------------------------------------------------------------------------
# Constraints
# --------------------------------------------------------------------------

_CONSTRAINTS_HEADING_RE = re.compile(r"(?i)^constraints:\s*$")
_CONSTRAINTS_STOP_RE = re.compile(r"(?i)^(example\b|follow-up\b)")
_BOUND_TOKEN = r"[A-Za-z0-9_.\[\]\^\-]+"
_BOUND_OP = r"(?:<=|>=|<|>|≤|≥)"
_BOUND_RE = re.compile(
    rf"^\s*{_BOUND_TOKEN}\s*{_BOUND_OP}\s*{_BOUND_TOKEN}(?:\s*{_BOUND_OP}\s*{_BOUND_TOKEN})*\s*$"
)


def _normalize_ineq(value: str) -> str:
    return value.replace("≤", "<=").replace("≥", ">=")


def _strip_bullet(line: str) -> str:
    # Only strip a bullet marker followed by whitespace, never a leading `-`
    # that is directly followed by a digit/char (e.g. a negative bound like
    # `-10^9 <= nums[i] <= 10^9` must not have its minus sign eaten).
    return _normalize_ineq(re.sub(r"^[\-*•]\s+", "", line).strip())


def _extract_constraints(text: str) -> tuple[list[str], str]:
    lines = text.split("\n")
    n = len(lines)
    consumed = [False] * n
    constraints: list[str] = []

    i = 0
    while i < n:
        if _CONSTRAINTS_HEADING_RE.match(lines[i].strip()):
            consumed[i] = True
            j = i + 1
            # Skip blank lines between the heading and the first item.
            while j < n and not lines[j].strip():
                j += 1
            found_item = False
            while j < n:
                stripped = lines[j].strip()
                if not stripped:
                    # A blank line only ends the list once we have at least
                    # one item; blank lines before the first item were
                    # already skipped above.
                    if found_item:
                        break
                    j += 1
                    continue
                if _CONSTRAINTS_STOP_RE.match(stripped):
                    break
                constraints.append(_strip_bullet(stripped))
                consumed[j] = True
                found_item = True
                j += 1
            i = j
            continue
        i += 1

    for idx, line in enumerate(lines):
        if consumed[idx]:
            continue
        # Accept an optional leading bullet (`- `, `* `, `• `) on a
        # standalone bound line too, e.g. a bulleted bound outside any
        # "Constraints:" heading. `_strip_bullet` never eats a leading `-`
        # that is directly followed by a digit (a negative bound).
        candidate = _strip_bullet(line)
        if candidate and _BOUND_RE.match(candidate) and any(c.isdigit() for c in candidate):
            constraints.append(candidate)
            consumed[idx] = True

    remaining = "\n".join("" if consumed[idx] else lines[idx] for idx in range(n))
    return constraints, remaining


# --------------------------------------------------------------------------
# Problem vs. question
# --------------------------------------------------------------------------

_PROBLEM_MARKERS_RE = re.compile(
    r"Example\s+\d|Input:|Output:|Given (an|a|the|two)|Return |Follow-up"
)
_EXAMPLE_OR_INPUT_RE = re.compile(r"Example\s+\d|Input:")
_DIRECT_ASK_STARTS: Final = ("can you", "how", "why", "what", "help", "give me", "explain")
_DIRECT_ASK_MAX_CHARS: Final = 200
# A problem-statement scaffolding line (Example N/Input/Output/Explanation/
# Constraints) is never itself the user's direct ask, even if it happens to
# contain a marker word below (e.g. "Explanation: ... please note ...").
_NON_ASK_LINE_RE = re.compile(r"(?i)^(example\s+\d|input:|output:|explanation:|constraints:)")
# First-person / request markers: a hint/help ask embedded as a leading or
# trailing line of an otherwise-pasted problem statement (e.g. "I only want
# a hint please.") must still be pulled out as the question, not silently
# absorbed into the problem text.
_ASK_MARKER_RE = re.compile(r"(?i)\bi\b|\bi'm\b|\bme\b|\bmy\b|please|hint|stuck|help")


def _looks_like_problem(prose: str, has_constraints: bool) -> bool:
    has_marker = bool(_PROBLEM_MARKERS_RE.search(prose)) or has_constraints
    if not has_marker:
        return False
    long_enough = len(prose) >= 150
    has_example_or_input = bool(_EXAMPLE_OR_INPUT_RE.search(prose))
    return long_enough or has_example_or_input


def _is_direct_ask_line(line: str) -> bool:
    if not line or len(line) > _DIRECT_ASK_MAX_CHARS:
        return False
    if _NON_ASK_LINE_RE.match(line):
        return False
    if line.endswith("?"):
        return True
    if line.lower().startswith(_DIRECT_ASK_STARTS):
        return True
    return bool(_ASK_MARKER_RE.search(line))


def _extract_direct_ask(prose: str) -> tuple[str, str | None]:
    lines = prose.split("\n")
    non_blank = [i for i, line in enumerate(lines) if line.strip()]
    if len(non_blank) <= 1:
        return prose, None
    last_i, first_i = non_blank[-1], non_blank[0]
    if _is_direct_ask_line(lines[last_i].strip()):
        target_i = last_i
    elif _is_direct_ask_line(lines[first_i].strip()):
        target_i = first_i
    else:
        return prose, None
    question = lines[target_i].strip()
    del lines[target_i]
    return "\n".join(lines), question


# --------------------------------------------------------------------------
# Language resolution
# --------------------------------------------------------------------------


def _resolve_language(
    language_hint: str | None,
    code_blocks: list[CodeBlock],
    error_text: str | None,
) -> str | None:
    if language_hint:
        normalized = _normalize_language(language_hint)
        if normalized:
            return normalized
    for block in code_blocks:
        if block.language:
            return block.language
    if error_text and looks_like_python_error(error_text):
        return "python"
    return None


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def normalize_text(text: str, *, language_hint: str | None = None) -> StructuredInput:
    """Parse raw user text into a `StructuredInput`. Pure, deterministic, no LLM."""
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError(f"text exceeds MAX_TEXT_CHARS ({MAX_TEXT_CHARS})")

    code_blocks, fenced_errors, remaining = _extract_fenced_blocks(text)
    plain_errors, remaining = _extract_errors(remaining)
    error_texts = fenced_errors + plain_errors

    unfenced_blocks, remaining = _extract_unfenced_code(remaining)
    code_blocks = code_blocks + unfenced_blocks

    constraints, remaining = _extract_constraints(remaining)

    prose = _collapse_blank_lines(remaining)

    error_text = none_if_blank("\n".join(t for t in error_texts if t))

    problem: str | None = None
    question: str | None = None
    if prose:
        if _looks_like_problem(prose, bool(constraints)):
            remaining_prose, direct_question = _extract_direct_ask(prose)
            problem = none_if_blank(_collapse_blank_lines(remaining_prose))
            question = none_if_blank(direct_question)
        else:
            question = none_if_blank(prose)

    language = _resolve_language(language_hint, code_blocks, error_text)

    return StructuredInput(
        source="text",
        question=question,
        code=code_blocks,
        error=error_text,
        problem=problem,
        constraints=constraints,
        language=language,
    )


def merge_inputs(image_input: StructuredInput, text_input: StructuredInput) -> StructuredInput:
    """Merge a text-derived `StructuredInput` into an image-derived one.

    Text fields take precedence for scalar fields (question/error/problem/
    language), code blocks are concatenated (text first), and constraints are
    a de-duplicated union preserving order (text first).
    """
    constraints: list[str] = []
    for constraint in [*text_input.constraints, *image_input.constraints]:
        if constraint not in constraints:
            constraints.append(constraint)

    return StructuredInput(
        source="image",
        question=text_input.question or image_input.question,
        code=[*text_input.code, *image_input.code],
        error=text_input.error or image_input.error,
        problem=text_input.problem or image_input.problem,
        constraints=constraints,
        language=text_input.language or image_input.language,
    )
