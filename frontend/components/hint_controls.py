"""The hint-ladder control: "Show me the next hint" / ladder-exhausted caption.

Renders purely from `generated.more_help_available` -- it never decides on
its own whether more help exists, and never re-sends anything itself; it
only reports whether the caller (the button) was clicked so `app.py` can
resend the stored last problem text with the same `conversation_id`/`topic`.
"""

from __future__ import annotations

import streamlit as st

from frontend.api_client import GeneratedResponseDict

__all__ = ["render_hint_control"]

NEXT_HINT_LABEL = "Show me the next hint"


def render_hint_control(generated: GeneratedResponseDict, key: str) -> bool:
    """Render the hint-ladder control for one turn's `generated` response.

    Returns `True` iff the user just clicked "Show me the next hint" this
    run. Renders nothing if `hint_level` is `None` (not a hint-ladder turn).
    """
    if generated["hint_level"] is None:
        return False

    if generated["more_help_available"]:
        return st.button(NEXT_HINT_LABEL, key=key)

    st.caption(
        "That's as far as the hint ladder goes at this assistance level -- "
        "ask for more help or share your attempt for the next level."
    )
    return False
