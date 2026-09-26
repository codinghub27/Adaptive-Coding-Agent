"""Sidebar learner-profile panel.

Purely a renderer over `frontend.api_client.ProfileDict` -- it never fetches
data itself and never decides what the profile *means*, only how to display
the fields the API already returned.
"""

from __future__ import annotations

import streamlit as st

from frontend.api_client import ProfileDict

__all__ = ["render_profile_panel"]


def render_profile_panel(profile: ProfileDict | None) -> None:
    """Render the sidebar learner-profile panel.

    `profile` is `None` before the first successful `GET /profile` call (or
    on a fetch failure); an empty-but-present profile (no skills, no
    preferences, no error tags) is treated the same way -- both render an
    explicit "no data yet" state rather than a blank panel.
    """
    st.subheader("Your learning profile")

    has_data = profile is not None and (
        bool(profile["skill_levels"])
        or bool(profile["learning_preferences"])
        or bool(profile["common_errors"])
        or bool(profile["language"])
    )
    if not has_data:
        st.caption("No profile data yet")
        return

    assert profile is not None  # narrowed by has_data above

    if profile["language"]:
        st.caption(f"Preferred language: {profile['language']}")

    if profile["skill_levels"]:
        st.markdown("**Skill levels**")
        for skill in sorted(profile["skill_levels"]):
            level = profile["skill_levels"][skill]
            st.progress(level, text=f"{skill}: {level:.0%}")

    if profile["learning_preferences"]:
        st.markdown("**Learning preferences**")
        for name in sorted(profile["learning_preferences"]):
            enabled = profile["learning_preferences"][name]
            st.checkbox(
                name,
                value=enabled,
                disabled=True,
                key=f"profile_pref_{name}",
            )

    if profile["common_errors"]:
        st.markdown("**Common errors**")
        for error_tag in profile["common_errors"]:
            st.caption(f"- {error_tag}")
