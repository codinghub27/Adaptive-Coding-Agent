"""Shared `FakeLLMClient` test double for `app.input` tests.

Implements the `LLMClient` protocol with canned `chat`/`vision` responses,
optional per-method `LLMError` raising, and call recording -- no real
network/model calls.
"""

from collections.abc import Sequence

from app.llm.base import ChatMessage, ChatResult, LLMError

__all__ = ["FakeLLMClient"]


class FakeLLMClient:
    """A minimal `LLMClient` stand-in for `chat()` and `vision()`."""

    def __init__(
        self,
        *,
        chat_content: str | None = None,
        vision_content: str | None = None,
        raise_chat: bool = False,
        raise_vision: bool = False,
    ) -> None:
        self.chat_content = chat_content
        self.vision_content = vision_content
        self.raise_chat = raise_chat
        self.raise_vision = raise_vision
        self.chat_calls: list[Sequence[ChatMessage]] = []
        self.chat_kwargs: list[dict[str, object]] = []
        self.vision_calls: list[dict[str, object]] = []

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        self.chat_calls.append(messages)
        self.chat_kwargs.append({"temperature": temperature, "max_tokens": max_tokens})
        if self.raise_chat:
            raise LLMError("fake provider chat call failed: RuntimeError")
        assert self.chat_content is not None
        return ChatResult(content=self.chat_content, provider="fake", model="fake-model")

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise NotImplementedError

    async def vision(
        self,
        image: bytes,
        prompt: str,
        *,
        mime_type: str = "image/png",
    ) -> ChatResult:
        self.vision_calls.append({"image": image, "prompt": prompt, "mime_type": mime_type})
        if self.raise_vision:
            raise LLMError("fake provider vision call failed: RuntimeError")
        assert self.vision_content is not None
        return ChatResult(content=self.vision_content, provider="fake", model="fake-vision")
