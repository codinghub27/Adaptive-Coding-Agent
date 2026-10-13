"""Evaluation only: answer the Groq chat-completions route from a local Ollama model.

When every cloud credential is rate-limited, a behaviour replay cannot finish.
This bridge lets a SECOND copy of the server run the same code against a
local model, so the routing and the prompts can still be read live. It is not
a provider: nothing in `app/` knows about it, `.env` is not touched, and the
server on the usual port keeps its cloud settings.

    # 1. the bridge
    .\\venv\\Scripts\\python.exe -m uvicorn eval.local_model_bridge:app --port 11500
    # 2. a second server that talks to it (PowerShell)
    $env:GROQ_API_BASE="http://127.0.0.1:11500"; $env:LLM_PROVIDER="groq"
    $env:LLM_MODEL="qwen-direct"
    .\\venv\\Scripts\\python.exe -m uvicorn app.main:app --port 8010
    # 3. the replay, against that server
    .\\venv\\Scripts\\python.exe -m eval.behavior.target_replay `
        --base-url http://127.0.0.1:8010 --timeout 900 --out replay_local.md

Thinking is switched off (`think: false`), as `ollama run qwen-direct
--think=false` does. Set `LOCAL_MODEL` to use another Ollama model.

A small local model is not the model the product runs on: what such a replay
shows is which agent answered and whether the reply kept its limits, not how
good the cloud model's reply would be. Image turns are not supported (the text
parts of a message are forwarded, the picture is not).
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any, Final

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

OLLAMA_CHAT: Final = os.environ.get("OLLAMA_CHAT_URL", "http://127.0.0.1:11434/api/chat")
MODEL: Final = os.environ.get("LOCAL_MODEL", "qwen-direct")
_CONTEXT_TOKENS: Final = 8192
_TIMEOUT_S: Final = 600.0


def _text(content: Any) -> str:
    """A message's text: a string as it is, or the text parts of a multimodal list."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:  # pyright: ignore[reportUnknownVariableType]
            if isinstance(part, dict):
                parts.append(str(part.get("text", "")))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        return "\n".join(parts)
    return ""


async def chat(request: Request) -> JSONResponse:
    body: dict[str, Any] = await request.json()
    messages = [
        {"role": str(message.get("role", "user")), "content": _text(message.get("content"))}
        for message in body.get("messages", [])
    ]
    options: dict[str, Any] = {"num_ctx": _CONTEXT_TOKENS}
    if body.get("temperature") is not None:
        options["temperature"] = body["temperature"]
    limit = body.get("max_tokens") or body.get("max_completion_tokens")
    if limit:
        options["num_predict"] = int(limit)
    payload = {
        "model": MODEL,
        "think": False,
        "stream": False,
        "options": options,
        "messages": messages,
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        reply: dict[str, Any] = (await client.post(OLLAMA_CHAT, json=payload)).json()
    answered: dict[str, Any] = reply.get("message") or {}
    prompt_tokens = int(reply.get("prompt_eval_count") or 0)
    completion_tokens = int(reply.get("eval_count") or 0)
    return JSONResponse(
        {
            "id": "chatcmpl-" + uuid.uuid4().hex,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": MODEL,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": str(answered.get("content", "")),
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }
    )


app = Starlette(routes=[Route("/openai/v1/chat/completions", chat, methods=["POST"])])
