"""Just enough of a conversation table for the session stubs in the chat API tests.

ADAPTIVE-upgrade P1 made `/chat` create a conversation when the client sends
none (B7), so every API turn now reads and writes conversation memory. The
non-`db` API tests run against table-less session stubs; this gives those
stubs one in-memory conversation so those reads/writes behave like a fresh,
empty conversation instead of failing with `ConversationNotFoundError`.
Anything else still reads as an empty database.
"""

from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from app.db.models import Conversation


class StubResult:
    """A `Result` over at most one stored conversation; otherwise empty."""

    def __init__(self, conversation: Conversation | None = None, *, seq: int = 1) -> None:
        self._conversation = conversation
        self._seq = seq

    def scalar_one_or_none(self) -> Any:
        return self._conversation

    def scalar_one(self) -> int:
        return self._seq

    def one_or_none(self) -> None:
        return None

    def scalars(self) -> list[Any]:
        return []


class ConversationStub:
    """Mixin: `add`/`flush`/`execute` backed by the conversations added so far."""

    def __init__(self) -> None:
        self.conversations: list[Conversation] = []

    def add(self, obj: Any) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = uuid4()
        if hasattr(obj, "created_at") and getattr(obj, "created_at", None) is None:
            obj.created_at = datetime.now(UTC)
        if isinstance(obj, Conversation):
            self.conversations.append(obj)

    async def flush(self) -> None:
        return None

    async def refresh(self, obj: Any) -> None:
        del obj

    async def execute(self, statement: Any = None, *args: object, **kwargs: object) -> StubResult:
        del args, kwargs
        descriptions = cast("list[dict[str, Any]]", getattr(statement, "column_descriptions", []))
        first = descriptions[0] if descriptions else {}
        if first.get("entity") is Conversation and first.get("name") == "Conversation":
            return StubResult(self.conversations[-1] if self.conversations else None)
        return StubResult()
