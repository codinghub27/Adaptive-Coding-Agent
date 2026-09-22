"""Provider-agnostic types and interface for LLM/vision/embedding clients.

This module has no dependency on any model SDK or LangChain integration.
Business logic (graph nodes, agents, etc.) should depend only on the
`LLMClient` protocol and the Pydantic types defined here so providers stay
swappable.
"""

from collections.abc import Sequence
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel


class ChatMessage(BaseModel):
    """A single message in a chat conversation."""

    role: Literal["system", "user", "assistant"]
    content: str


class TokenUsage(BaseModel):
    """Token accounting for a single chat/vision call."""

    input_tokens: int
    output_tokens: int
    total_tokens: int


class ChatResult(BaseModel):
    """The normalized result of a chat/vision call, regardless of provider."""

    content: str
    provider: str
    model: str
    usage: TokenUsage | None = None


class LLMError(Exception):
    """Wraps a provider/SDK failure without leaking secrets.

    The message must only ever contain the provider name and the underlying
    exception's class name -- never API keys, headers, or raw exception
    payloads that might include them.
    """


@runtime_checkable
class LLMClient(Protocol):
    """Provider-agnostic interface for chat, embedding, and vision calls."""

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult: ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def vision(
        self,
        image: bytes,
        prompt: str,
        *,
        mime_type: str = "image/png",
    ) -> ChatResult: ...
