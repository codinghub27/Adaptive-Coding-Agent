"""Memory package: learner profile, conversation history, and learning events.

Re-exports the public API of `app.memory.profile`, `app.memory.conversation`,
and `app.memory.events`.
"""

from app.memory.conversation import (
    DEFAULT_CONTEXT_WINDOW,
    ConversationNotFoundError,
    add_turn,
    get_recent_context,
    start_conversation,
)
from app.memory.events import (
    INTENT_TO_HELP,
    list_events,
    rebuild_profile,
    record_event,
)
from app.memory.profile import get_profile, set_language, set_learning_preferences

__all__ = [
    "DEFAULT_CONTEXT_WINDOW",
    "INTENT_TO_HELP",
    "ConversationNotFoundError",
    "add_turn",
    "get_profile",
    "get_recent_context",
    "list_events",
    "rebuild_profile",
    "record_event",
    "set_language",
    "set_learning_preferences",
    "start_conversation",
]
