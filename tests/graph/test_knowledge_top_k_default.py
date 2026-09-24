"""`DEFAULT_KNOWLEDGE_TOP_K` (`app.knowledge.base`) must be the single source
of truth for the "4 chunks per query" default, everywhere it's duplicated:
`Settings.knowledge_top_k`, `GraphContext.knowledge_top_k`, `run_graph`'s
`knowledge_top_k` parameter, and `app.graph.api`'s `_get_knowledge_top_k`
fallback."""

import inspect

from fastapi import Request
from starlette.datastructures import State

from app.config import Settings
from app.graph.api import _get_knowledge_top_k  # pyright: ignore[reportPrivateUsage]
from app.graph.build import run_graph
from app.graph.state import GraphContext
from app.knowledge.base import DEFAULT_KNOWLEDGE_TOP_K
from tests.input.fakes import FakeLLMClient


def test_settings_knowledge_top_k_default_matches() -> None:
    assert Settings.model_fields["knowledge_top_k"].default == DEFAULT_KNOWLEDGE_TOP_K


def test_graph_context_knowledge_top_k_default_matches() -> None:
    context = GraphContext(llm=FakeLLMClient())
    assert context.knowledge_top_k == DEFAULT_KNOWLEDGE_TOP_K


def test_run_graph_knowledge_top_k_default_matches() -> None:
    default = inspect.signature(run_graph).parameters["knowledge_top_k"].default
    assert default == DEFAULT_KNOWLEDGE_TOP_K


def test_get_knowledge_top_k_fallback_matches() -> None:
    request = Request(scope={"type": "http", "app": _AppWithEmptyState(), "headers": []})
    assert _get_knowledge_top_k(request) == DEFAULT_KNOWLEDGE_TOP_K


class _AppWithEmptyState:
    """A minimal stand-in for `FastAPI` with no `settings` on `.state`."""

    def __init__(self) -> None:
        self.state = State()
