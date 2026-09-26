"""Renders one finished chat turn.

Purely a renderer over `frontend.api_client.ChatResponseDict` /
`GeneratedResponseDict`: it displays exactly the sections, citations, and
next steps the server produced, and derives the hint-ladder indicator text
from `hint_level`/`hint_ceiling` alone. It never hides, reorders, or
synthesizes content the server didn't send -- the server already guarantees
no code leaks below the top assistance rung.
"""

from __future__ import annotations

import streamlit as st

from frontend.api_client import CODE_SECTION_KINDS, ChatResponseDict, ResponseSectionDict

__all__ = ["render_hint_ladder_indicator", "render_turn"]


def _render_section(section: ResponseSectionDict, key_prefix: str) -> None:
    is_code = section["kind"] in CODE_SECTION_KINDS or section["language"] is not None
    st.markdown(f"**{section['title']}**")
    if is_code:
        st.code(section["body"], language=section["language"] or "text")
    else:
        st.markdown(section["body"])


def render_hint_ladder_indicator(hint_level: int, hint_ceiling: int) -> None:
    """Render the compact `Hint N of M at this assistance level` indicator.

    `hint_level`/`hint_ceiling` are 0-based `HintLevel` ints; both are
    displayed 1-based.
    """
    st.caption(f"Hint {hint_level + 1} of {hint_ceiling + 1} at this assistance level")


def render_turn(turn: ChatResponseDict, key_prefix: str) -> None:
    """Render one completed turn's response, sections, citations, and next steps.

    `key_prefix` scopes any widget keys created for this turn (currently
    none are needed, but kept for forward compatibility / uniqueness).
    """
    generated = turn["generated"]
    if generated is None:
        # No structured render available (a degraded turn): the assembled
        # markdown is all there is.
        st.markdown(turn["response"])
        return

    # `generated["text"]` is exactly the assembly of `generated["sections"]`,
    # so rendering both would show every hint, next step and citation twice.
    # The structured sections are the richer form (code gets `st.code`), so
    # they win and the flat markdown is skipped.

    if generated["hint_level"] is not None and generated["hint_ceiling"] is not None:
        render_hint_ladder_indicator(generated["hint_level"], generated["hint_ceiling"])

    for i, section in enumerate(generated["sections"]):
        if section["kind"] in ("next_steps", "citations"):
            # Rendered separately below from the structured fields, not the
            # (possibly absent) matching section.
            continue
        _render_section(section, key_prefix=f"{key_prefix}_section_{i}")

    if generated["next_steps"]:
        st.markdown("**Next steps**")
        for step in generated["next_steps"]:
            st.markdown(f"- {step}")

    if generated["citations"]:
        st.markdown("**Citations**")
        for citation in generated["citations"]:
            st.markdown(f"- {citation}")
