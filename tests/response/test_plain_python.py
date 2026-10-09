"""Generated code is plain Python: no type hints (owner decision, 2026-10-09).

Reported: the code-writing agent returned `def expand(left: int, right: int)
-> (int, int):`; wanted `def expand(left, right):`.
"""

import ast

from app.response.plain_python import has_type_hints, without_type_hints

_HINTED = '''from typing import List, Optional


def expand(s: str, left: int, right: int) -> (int, int):
    """Grow the window while it is a palindrome."""
    # compare the two ends
    while left >= 0 and right < len(s) and s[left] == s[right]:
        left -= 1  # move outwards
        right += 1
    best: int = right - left - 1
    return left + 1, best


def longest(words: List[str], limit: Optional[int] = None, *rest: int, **more: str) -> str:
    seen: dict[str, int] = {}
    return max(words, key=len)
'''


def test_the_reported_signature_becomes_the_wanted_one() -> None:
    plain = without_type_hints(_HINTED)
    assert "def expand(s, left, right):" in plain
    assert "def longest(words, limit=None, *rest, **more):" in plain
    assert "->" not in plain
    assert "best = right - left - 1" in plain
    assert "seen = {}" in plain
    assert not has_type_hints(plain)
    ast.parse(plain)


def test_comments_docstrings_and_layout_survive() -> None:
    plain = without_type_hints(_HINTED)
    assert '"""Grow the window while it is a palindrome."""' in plain
    assert "# compare the two ends" in plain
    assert "left -= 1  # move outwards" in plain
    assert plain.count("\n") == _HINTED.count("\n") - 1  # only the typing import went


def test_a_typing_import_goes_only_when_nothing_uses_it() -> None:
    assert "from typing import" not in without_type_hints(_HINTED)
    used = (
        "from typing import NamedTuple\n\n\nclass P(NamedTuple):\n    x: int\n\n\n"
        "def f(a: int):\n    return P(a)\n"
    )
    plain = without_type_hints(used)
    assert "from typing import NamedTuple" in plain
    assert "def f(a):" in plain


def test_class_fields_keep_their_annotations() -> None:
    """A dataclass or NamedTuple field needs its annotation to exist at all."""
    code = (
        "from dataclasses import dataclass\n\n\n@dataclass\nclass Node:\n"
        "    val: int = 0\n    left: 'Node' = None\n\n\n"
        "def depth(node: Node) -> int:\n    return 0\n"
    )
    plain = without_type_hints(code)
    assert "    val: int = 0" in plain
    assert "def depth(node):" in plain


def test_code_without_hints_or_that_does_not_parse_is_returned_as_is() -> None:
    clean = "def expand(left, right):\n    return left, right\n"
    assert without_type_hints(clean) == clean
    assert not has_type_hints(clean)
    broken = "def f(a: int:\n    return a\n"
    assert without_type_hints(broken) == broken


def test_non_ascii_text_before_a_hint_does_not_shift_the_cut() -> None:
    code = 'def f(a: int, b: str = "é→") -> int:\n    return a  # naïve\n'
    assert without_type_hints(code) == 'def f(a, b="é→"):\n    return a  # naïve\n'
