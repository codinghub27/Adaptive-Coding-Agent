"""Per-graph-run LLM call budget enforcement.

Wraps an `LLMClient` so a graph run can never make more than a fixed number
of chat/embed/vision calls, regardless of how many nodes end up calling the
LLM. Counting happens *before* delegating to the wrapped client: a call that
would exceed the budget raises immediately and the inner client is never
invoked, so a runaway node cannot burn additional cost/latency once the
budget is spent.

Because `LLMBudgetExceededError` subclasses `LLMError`, every existing
LLM-calling call site that already degrades gracefully on `LLMError` (e.g.
`classify_intent` falling back to its keyword heuristic, or
`understand_input` keeping a successful text result when the image path
fails) also degrades gracefully when the budget is exhausted, with no
special-casing required.
"""

from collections.abc import Sequence
from typing import Final

from app.llm.base import ChatMessage, ChatResult, LLMClient, LLMError

__all__ = ["DEFAULT_MAX_LLM_CALLS", "BudgetedLLMClient", "LLMBudgetExceededError"]

# Per graph run. The Phase 07 specialized agents made the old budget of 3 too
# tight: the debugger alone needs `classify_intent` (1) + `infer_approach` (2)
# + `explain` (3) + `patch` (4), plus a second `patch` on its retry (5), and an
# image turn spends one more on vision (6). At 3 the patch call always raised
# `LLMBudgetExceededError`, which `patch_code` swallowed as an `LLMError` --
# so the debugger silently never produced a fix. 8 covers the worst real path
# (image + debug + retry) with headroom, while still bounding a runaway run.
DEFAULT_MAX_LLM_CALLS: Final = 8

_BUDGET_EXCEEDED_MESSAGE: Final = "LLM call budget exceeded for this graph run"


class LLMBudgetExceededError(LLMError):
    """Raised when a graph run's LLM call budget has been exhausted."""

    def __init__(self) -> None:
        super().__init__(_BUDGET_EXCEEDED_MESSAGE)


class BudgetedLLMClient:
    """`LLMClient` wrapper capping the total number of calls made through it.

    Satisfies the `LLMClient` protocol (structurally, via `isinstance`
    against the `runtime_checkable` protocol) so it can be substituted
    anywhere an `LLMClient` is expected -- e.g. installed as `GraphContext.llm`
    for the duration of a single graph run.
    """

    def __init__(self, inner: LLMClient, max_calls: int = DEFAULT_MAX_LLM_CALLS) -> None:
        if max_calls < 0:
            raise ValueError("max_calls must be >= 0")
        self._inner = inner
        self._max_calls = max_calls
        self._calls = 0

    @property
    def calls(self) -> int:
        """The number of calls made through this client so far."""
        return self._calls

    @property
    def max_calls(self) -> int:
        """The maximum number of calls this client will allow."""
        return self._max_calls

    def _consume(self) -> None:
        if self._calls >= self._max_calls:
            raise LLMBudgetExceededError
        self._calls += 1

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        self._consume()
        return await self._inner.chat(messages, temperature=temperature, max_tokens=max_tokens)

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self._consume()
        return await self._inner.embed(texts)

    async def vision(
        self,
        image: bytes,
        prompt: str,
        *,
        mime_type: str = "image/png",
    ) -> ChatResult:
        self._consume()
        return await self._inner.vision(image, prompt, mime_type=mime_type)
