"""Adaptive Coding Tutor -- Streamlit frontend.

Talks to the backend over HTTP only via `frontend.api_client.ApiClient`.
This module never imports `app.*`, never touches a database, and never
executes code or reaches the sandbox directly -- see the Phase 08 frontend
packet's architectural rule. All rendering of server content is delegated to
`frontend.components.*`, which display exactly what the API returned and
never synthesize hints, code, or teaching decisions on their own.
"""

from __future__ import annotations

import contextlib
from typing import Any, Literal, TypedDict, cast

import streamlit as st

from frontend.api_client import (
    ApiClient,
    ApiError,
    ChatResponseDict,
    DoneEvent,
    ErrorEvent,
    ProfileDict,
    StageEvent,
)
from frontend.components.chat_view import render_turn
from frontend.components.hint_controls import render_hint_control
from frontend.components.profile_panel import render_profile_panel

__all__ = ["main"]

LANGUAGE_OPTIONS: tuple[str, ...] = (
    "",
    "python",
    "javascript",
    "typescript",
    "java",
    "c",
    "cpp",
    "go",
    "rust",
)


class UserHistoryItem(TypedDict):
    role: Literal["user"]
    text: str


class AssistantHistoryItem(TypedDict):
    role: Literal["assistant"]
    chat: ChatResponseDict


HistoryItem = UserHistoryItem | AssistantHistoryItem


def _history() -> list[HistoryItem]:
    return cast("list[HistoryItem]", st.session_state.setdefault("history", []))


def _refresh_profile(api: ApiClient, token: str) -> None:
    """Best-effort profile refresh; a failure here must not break the chat flow."""
    with contextlib.suppress(ApiError):
        st.session_state["profile"] = api.get_profile(token)


def _render_auth_gate(api: ApiClient) -> None:
    st.title("Adaptive Coding Tutor")
    st.caption("Sign in or create an account to start a session.")

    login_tab, register_tab = st.tabs(["Log in", "Register"])

    with login_tab:
        with st.form("login_form", clear_on_submit=False):
            username = st.text_input("Username", key="login_username")
            password = st.text_input("Password", type="password", key="login_password")
            submitted = st.form_submit_button("Log in")
        if submitted:
            try:
                tokens = api.login(username, password)
            except ApiError as exc:
                st.error(exc.detail)
            else:
                st.session_state["access_token"] = tokens["access_token"]
                st.session_state["username"] = username
                st.rerun()

    with register_tab:
        with st.form("register_form", clear_on_submit=True):
            new_username = st.text_input("Username", key="register_username")
            new_password = st.text_input("Password", type="password", key="register_password")
            register_submitted = st.form_submit_button("Create account")
        if register_submitted:
            try:
                api.register(new_username, new_password)
            except ApiError as exc:
                st.error(exc.detail)
            else:
                st.success("Account created. You can log in now.")


def _start_new_conversation(api: ApiClient, token: str) -> None:
    try:
        conversation = api.create_conversation(token)
    except ApiError as exc:
        st.error(f"Could not start a conversation: {exc.detail}")
        return
    st.session_state["conversation_id"] = conversation["conversation_id"]
    st.session_state["history"] = []
    st.session_state["last_problem_text"] = None
    st.session_state["last_language"] = None


def _ensure_conversation(api: ApiClient, token: str) -> str | None:
    existing = st.session_state.get("conversation_id")
    if existing:
        return cast(str, existing)
    try:
        conversation = api.create_conversation(token)
    except ApiError as exc:
        st.error(f"Could not start a conversation: {exc.detail}")
        return None
    conversation_id = conversation["conversation_id"]
    st.session_state["conversation_id"] = conversation_id
    return conversation_id


def _render_sidebar(api: ApiClient, token: str) -> None:
    with st.sidebar:
        render_profile_panel(cast("ProfileDict | None", st.session_state.get("profile")))
        st.divider()
        st.text_input("Topic (optional)", key="topic_input")
        if st.button("New conversation", key="new_conversation_button"):
            _start_new_conversation(api, token)
            st.rerun()
        st.caption(f"Signed in as {st.session_state.get('username', '')}")
        if st.button("Log out", key="logout_button"):
            st.session_state.clear()
            st.rerun()


def _run_turn(
    api: ApiClient,
    token: str,
    *,
    text: str | None,
    language: str | None,
    image: bytes | None,
    image_filename: str,
    image_content_type: str,
    conversation_id: str,
    topic: str | None,
) -> None:
    """Stream one turn, showing live stage labels, then the rendered result.

    Appends the finished turn to history and queues a topic auto-fill update
    (applied at the top of the *next* run, never mid-run -- see
    `pending_topic_update` in `main`) rather than mutating the `topic_input`
    widget's own session-state key after it has already been rendered.
    """
    final_event: DoneEvent | ErrorEvent | None = None

    with st.chat_message("assistant"):
        with st.status("Working on it...", expanded=True) as status:
            try:
                for event in api.stream_chat(
                    token,
                    text=text,
                    language=language,
                    image=image,
                    image_filename=image_filename,
                    image_content_type=image_content_type,
                    conversation_id=conversation_id,
                    topic=topic,
                ):
                    if isinstance(event, StageEvent):
                        status.update(label=event.label)
                    else:
                        final_event = event
            except Exception:  # never let a raw traceback reach the UI
                final_event = ErrorEvent(detail="the request could not be completed")

            if isinstance(final_event, DoneEvent):
                status.update(label="Done", state="complete")
            else:
                status.update(label="Something went wrong", state="error")

        if isinstance(final_event, DoneEvent):
            render_turn(final_event.data, key_prefix=f"turn_{len(_history())}")
        else:
            detail = final_event.detail if final_event is not None else (
                "the request could not be completed"
            )
            st.error(detail)

    if isinstance(final_event, DoneEvent):
        chat = final_event.data
        _history().append({"role": "assistant", "chat": chat})
        _refresh_profile(api, token)

        plan = chat["plan"]
        if plan is not None:
            new_topic = plan.get("topic")
            if new_topic and not (st.session_state.get("topic_input") or "").strip():
                st.session_state["pending_topic_update"] = new_topic


def _handle_new_message(api: ApiClient, token: str, prompt: Any) -> None:  # noqa: ANN401
    """`prompt` is Streamlit's `ChatInputValue`, which is only partially typed
    upstream; `Any` is used deliberately here rather than guessing at a
    stub, with every field access narrowed immediately below.
    """
    prompt_text: str = prompt.text or ""
    files: list[Any] = list(prompt["files"] or [])

    paste_text = cast(str, st.session_state.get("paste_area", "") or "")
    language_choice = cast(str, st.session_state.get("language_select", "") or "") or None

    combined_parts = [part for part in (prompt_text.strip(), paste_text.strip()) if part]
    final_text = "\n\n".join(combined_parts) if combined_parts else None

    image_bytes: bytes | None = None
    image_filename = "image.png"
    image_content_type = "image/png"
    if files:
        uploaded = files[0]
        image_bytes = bytes(uploaded.getvalue())
        image_filename = str(uploaded.name or image_filename)
        image_content_type = str(uploaded.type or image_content_type)

    if final_text is None and image_bytes is None:
        return

    conversation_id = _ensure_conversation(api, token)
    if conversation_id is None:
        return

    topic = (st.session_state.get("topic_input") or "").strip() or None

    _history().append({"role": "user", "text": final_text or "(attached image)"})
    st.session_state["last_problem_text"] = final_text
    st.session_state["last_language"] = language_choice

    _run_turn(
        api,
        token,
        text=final_text,
        language=language_choice,
        image=image_bytes,
        image_filename=image_filename,
        image_content_type=image_content_type,
        conversation_id=conversation_id,
        topic=topic,
    )
    st.rerun()


def _resend_for_next_hint(api: ApiClient, token: str) -> None:
    conversation_id = st.session_state.get("conversation_id")
    text = st.session_state.get("last_problem_text")
    if conversation_id is None or text is None:
        st.error("Nothing to resend for the next hint.")
        return

    language = st.session_state.get("last_language")
    topic = (st.session_state.get("topic_input") or "").strip() or None

    _history().append({"role": "user", "text": text})
    _run_turn(
        api,
        token,
        text=text,
        language=language,
        image=None,
        image_filename="image.png",
        image_content_type="image/png",
        conversation_id=cast(str, conversation_id),
        topic=topic,
    )
    st.rerun()


def _render_hint_control_for_last_turn(api: ApiClient, token: str) -> None:
    history = _history()
    if not history:
        return
    last = history[-1]
    if last["role"] != "assistant":
        return
    generated = last["chat"]["generated"]
    if generated is None:
        return
    clicked = render_hint_control(generated, key=f"next_hint_{len(history) - 1}")
    if clicked:
        _resend_for_next_hint(api, token)


def _render_chat_area(api: ApiClient, token: str) -> None:
    st.title("Adaptive Coding Tutor")

    for i, item in enumerate(_history()):
        if item["role"] == "user":
            with st.chat_message("user"):
                st.markdown(item["text"])
        else:
            with st.chat_message("assistant"):
                render_turn(item["chat"], key_prefix=f"turn_{i}")

    _render_hint_control_for_last_turn(api, token)

    with st.expander("Paste code or an error message"):
        st.text_area(
            "Paste code or an error message",
            key="paste_area",
            label_visibility="collapsed",
        )
        st.selectbox(
            "Language",
            LANGUAGE_OPTIONS,
            key="language_select",
            format_func=lambda value: value or "(auto-detect)",
        )

    prompt = st.chat_input(
        "Ask a question, paste an error, or attach a screenshot",
        accept_file=True,
        file_type=["png", "jpg", "jpeg"],
        key="chat_input_box",
    )
    if prompt:
        _handle_new_message(api, token, prompt)


def main() -> None:
    st.set_page_config(page_title="Adaptive Coding Tutor")

    # Apply any topic auto-fill queued by the previous run's completed turn
    # *before* the sidebar's `topic_input` widget is instantiated -- Streamlit
    # forbids mutating a widget's session-state key after that widget has
    # already been rendered in the current run.
    pending_topic = st.session_state.pop("pending_topic_update", None)
    if pending_topic is not None and not (st.session_state.get("topic_input") or "").strip():
        st.session_state["topic_input"] = pending_topic

    if not st.session_state.get("access_token"):
        api = ApiClient()
        _render_auth_gate(api)
        return

    api = ApiClient()
    token = cast(str, st.session_state["access_token"])
    if "profile" not in st.session_state:
        _refresh_profile(api, token)

    _render_sidebar(api, token)
    _render_chat_area(api, token)


main()
