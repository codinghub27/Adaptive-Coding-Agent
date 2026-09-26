"""Phase 5 manual test cases, run against the live Qdrant + real cached
embedding/reranker models.

Mirrors `tests/graph/test_phase4_manual.py`'s style: each "manual" test drives
the real thing end-to-end (a real `KnowledgeRetriever.retrieve` call, or the
whole teaching graph via `run_graph`) and prints an `ACTUAL:` line (visible
with `pytest -s`) for pasting into the phase doc. Never creates, deletes, or
modifies the `dsa_knowledge` collection -- every test here only reads it.
"""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from qdrant_client import AsyncQdrantClient

from app.config import Settings, get_settings
from app.graph.build import run_graph
from app.graph.state import RawInput
from app.knowledge.retrieve import Retriever, create_retriever
from app.llm.client import Tracer
from tests.input.fakes import FakeLLMClient

pytestmark = pytest.mark.integration


def _live_settings() -> Settings:
    return get_settings()


async def _open_client(settings: Settings) -> AsyncQdrantClient:
    return AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=(
            settings.qdrant_api_key.get_secret_value()
            if settings.qdrant_api_key is not None
            else None
        ),
        timeout=settings.qdrant_timeout,
        check_compatibility=False,
    )


@pytest_asyncio.fixture
async def live_retriever() -> AsyncGenerator[Retriever]:
    """The real `KnowledgeRetriever`: live Qdrant + cached fastembed models.

    Read-only against the shared `dsa_knowledge` collection; only ever
    queries it, never creates/deletes/modifies it.
    """
    settings = _live_settings()
    client = await _open_client(settings)
    try:
        retriever = await create_retriever(settings, client, Tracer.disabled())
        yield retriever
    finally:
        await client.close()


@pytest_asyncio.fixture
async def dead_qdrant_retriever() -> AsyncGenerator[Retriever]:
    """The same real `KnowledgeRetriever`, but wired to an unreachable Qdrant
    (dense search must fail-soft to BM25-only)."""
    settings = _live_settings()
    dead_client = AsyncQdrantClient(url="http://127.0.0.1:1", timeout=1, check_compatibility=False)
    try:
        retriever = await create_retriever(settings, dead_client, Tracer.disabled())
        yield retriever
    finally:
        await dead_client.close()


# ---------------------------------------------------------------------------
# Test 1: retriever.retrieve against the live Qdrant collection
# ---------------------------------------------------------------------------


async def test_manual_1_sliding_window_query_hits_live_qdrant(
    live_retriever: Retriever,
) -> None:
    hits = await live_retriever.retrieve("how to shrink a variable-size window", top_k=4)

    print(
        "\nACTUAL: "
        + str(
            [
                (
                    hit.chunk.pattern,
                    hit.chunk.heading,
                    round(hit.score, 2),
                    hit.retrievers,
                    hit.reranked,
                )
                for hit in hits
            ]
        )
    )

    assert hits
    top = hits[0]
    assert top.chunk.pattern == "sliding_window"
    assert top.chunk.topic == "arrays"
    assert top.chunk.source.endswith("sliding_window.md")
    assert top.reranked is True
    assert "dense" in top.retrievers

    for hit in hits:
        assert hit.chunk.source
        assert hit.chunk.topic
        assert hit.chunk.pattern


# ---------------------------------------------------------------------------
# Test 2: run_graph end-to-end with the live retriever + Phase 4 fake LLM
# ---------------------------------------------------------------------------

_DSA_SLIDING_WINDOW_TEXT = (
    "Given a string s, find the length of the longest substring without "
    "repeating characters.\n"
    "\n"
    "Example 1:\n"
    'Input: s = "abcabcbb"\n'
    "Output: 3\n"
    "\n"
    "Constraints:\n"
    "- 0 <= s.length <= 5 * 10^4\n"
)

#: `FakeLLMClient` returns one canned string for *every* `chat()` call, and it
#: asserts when given none. A `dsa` route reaches the DSA solver, which makes a
#: real LLM call, so the DSA tests below must supply one -- otherwise the solver
#: raises inside `dsa_agent`, `safe_node` degrades it to a `NodeError`, and the
#: turn silently reports a fallback instead of the behaviour under test. The
#: payload only has to be parseable; these tests assert on retrieval, not on
#: solver output.
_FAKE_CHAT_CONTENT = '{"intent": "DSA_SOLVE", "confidence": 0.95, "rationale": "clear"}'

_DEBUG_RULE_TEXT = (
    "```python\n"
    "def get_item(items, idx):\n"
    "    return items[idx]\n"
    "```\n"
    "\n"
    "IndexError: list index out of range\n"
)


async def test_manual_2a_dsa_solve_retrieves_sliding_window_context(
    live_retriever: Retriever,
) -> None:
    fake = FakeLLMClient(chat_content=_FAKE_CHAT_CONTENT)
    raw = RawInput(text=_DSA_SLIDING_WINDOW_TEXT, topic_hint="sliding_window")

    result = await run_graph(raw, llm=fake, retriever=live_retriever, knowledge_top_k=4)
    state = result.state

    print(
        "\nACTUAL: "
        f"route={state.route!r} "
        f"intent={state.intent.intent if state.intent else None} "
        f"n_hits={len(state.retrieved_context)} "
        f"patterns={[hit.chunk.pattern for hit in state.retrieved_context]}"
    )

    assert state.route == "dsa"
    # Guards the gap that let a degraded DSA turn pass unnoticed here while
    # test 2c (which does assert on errors) failed.
    assert state.errors == []
    assert state.retrieved_context
    assert state.retrieved_context[0].chunk.pattern == "sliding_window"
    for hit in state.retrieved_context:
        assert hit.chunk.source
        assert hit.chunk.pattern


async def test_manual_2b_runtime_error_debug_skips_retrieval(
    live_retriever: Retriever,
) -> None:
    fake = FakeLLMClient()
    raw = RawInput(text=_DEBUG_RULE_TEXT)

    result = await run_graph(raw, llm=fake, retriever=live_retriever, knowledge_top_k=4)
    state = result.state

    print(
        "\nACTUAL: "
        f"route={state.route!r} "
        f"intent={state.intent.intent if state.intent else None} "
        f"retrieved_context={state.retrieved_context!r}"
    )

    assert state.route == "debug"
    assert state.retrieved_context == []


async def test_manual_2c_dead_qdrant_degrades_to_bm25_only(
    dead_qdrant_retriever: Retriever,
) -> None:
    fake = FakeLLMClient(chat_content=_FAKE_CHAT_CONTENT)
    raw = RawInput(text=_DSA_SLIDING_WINDOW_TEXT, topic_hint="sliding_window")

    result = await run_graph(raw, llm=fake, retriever=dead_qdrant_retriever, knowledge_top_k=4)
    state = result.state

    print(
        "\nACTUAL: "
        f"route={state.route!r} "
        f"errors={state.errors!r} "
        f"n_hits={len(state.retrieved_context)} "
        f"retrievers={[hit.retrievers for hit in state.retrieved_context]}"
    )

    assert state.errors == []
    assert state.retrieved_context
    for hit in state.retrieved_context:
        assert hit.retrievers == ("bm25",)
