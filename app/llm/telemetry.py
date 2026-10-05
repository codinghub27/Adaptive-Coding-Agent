"""Per-request telemetry for the LLM provider's HTTP calls.

The provider SDK retries a rate-limited request itself, sleeping for the
server's `retry-after` before it tries again, and none of that is visible
from `LLMClient.chat`: a call that "took 17 s" may be 1 s of model time and
16 s of waiting. These `httpx` event hooks record every HTTP attempt -- its
status, how long it took, and the provider's rate-limit headers -- so the two
can be told apart.

Only metadata is recorded: status, timings, the URL path, a credential INDEX
(never the key) and the rate-limit headers. Never a request or response body,
never an `Authorization` header.

A 429 is always logged at WARNING. Everything is additionally appended as
JSON lines to `Settings.llm_http_log_path` when that is set (off by default).
"""

import json
import logging
import time
from pathlib import Path
from typing import Final, cast

import httpx

__all__ = ["RATE_LIMIT_HEADERS", "HttpTelemetry"]

logger = logging.getLogger(__name__)

#: Groq's documented rate-limit headers, plus the standard `retry-after`.
RATE_LIMIT_HEADERS: Final = (
    "retry-after",
    "x-ratelimit-limit-requests",
    "x-ratelimit-limit-tokens",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-remaining-tokens",
    "x-ratelimit-reset-requests",
    "x-ratelimit-reset-tokens",
)

_STARTED: Final = "aca_started"


class HttpTelemetry:
    """`httpx` event hooks for one credential's client."""

    def __init__(self, *, provider: str, credential_index: int, log_path: Path | None) -> None:
        self._provider = provider
        self._credential_index = credential_index
        self._log_path = log_path

    async def on_request(self, request: httpx.Request) -> None:
        request.extensions[_STARTED] = (time.time(), time.perf_counter())

    async def on_response(self, response: httpx.Response) -> None:
        wall, seconds = self._timing(response.request)
        record: dict[str, object] = {
            "provider": self._provider,
            "credential": self._credential_index,
            "path": response.request.url.path,
            "status": response.status_code,
            "started": wall,
            # Time to response HEADERS; the body of a chat completion follows
            # within milliseconds for a non-streamed call.
            "seconds": seconds,
        }
        for name in RATE_LIMIT_HEADERS:
            value = response.headers.get(name)
            if value is not None:
                record[name] = value
        if response.status_code == 429:
            logger.warning(
                "llm rate limited: provider=%s credential=%d retry-after=%s "
                "remaining-requests=%s remaining-tokens=%s",
                self._provider,
                self._credential_index,
                record.get("retry-after"),
                record.get("x-ratelimit-remaining-requests"),
                record.get("x-ratelimit-remaining-tokens"),
            )
        if self._log_path is not None:
            try:
                with self._log_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record) + "\n")
            except OSError:
                logger.warning("llm http telemetry: could not write the log file")

    @staticmethod
    def _timing(request: httpx.Request) -> tuple[float, float | None]:
        """When this attempt started (wall clock) and how long it took."""
        started: object = request.extensions.get(_STARTED)
        if (
            isinstance(started, tuple)
            and len(cast("tuple[object, ...]", started)) == 2
            and all(isinstance(part, float) for part in cast("tuple[object, ...]", started))
        ):
            wall, monotonic = cast("tuple[float, float]", started)
            return round(wall, 3), round(time.perf_counter() - monotonic, 3)
        return round(time.time(), 3), None

    def client(self) -> httpx.AsyncClient:
        """An `httpx.AsyncClient` with these hooks, for the provider SDK."""
        return httpx.AsyncClient(
            event_hooks={"request": [self.on_request], "response": [self.on_response]}
        )
