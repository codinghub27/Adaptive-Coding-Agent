"""Explicit section asks override the short-answer rule (owner decision, transcript T1)."""

from app.graph.nodes import requested_sections


def test_t1_style_ask_requests_intuition_recognition_and_complexity() -> None:
    text = (
        "Explain the intuition first, then how to recognize which approach to use, then "
        "the time and space complexity, then provide the Python solution and run tests."
    )
    kinds = requested_sections(text)
    assert {"intuition", "recognition", "complexity"} <= kinds


def test_plain_requests_ask_for_nothing_extra() -> None:
    assert requested_sections("How to solve this problem") == frozenset()
    assert requested_sections("7?") == frozenset()
    assert requested_sections(None) == frozenset()
