"""Tests for `app.llm.embeddings`.

These exercise the real fastembed models rather than mocks: `bge-small-en-v1.5`
(already present in the local fastembed cache) and the
`Xenova/ms-marco-MiniLM-L-6-v2` cross-encoder (downloaded once from Hugging
Face on first use in this test session, if not already cached). The tracing
tests use a `unittest.mock.MagicMock(spec=Client)` in place of a real
`langsmith.Client`, so no network I/O happens there either.
"""

import threading
from collections.abc import Iterable
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from fastembed import TextEmbedding
from langsmith import Client as LangSmithClient

from app.llm.client import Tracer
from app.llm.embeddings import (
    EmbeddingConfigError,
    EmbeddingError,
    FastEmbedEmbedder,
    FastEmbedReranker,
)

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIM = 384
RERANKER_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"


@pytest.fixture(scope="module")
def embedder() -> FastEmbedEmbedder:
    return FastEmbedEmbedder(
        EMBEDDING_MODEL, cache_dir=None, tracer=Tracer.disabled(), expected_dim=EMBEDDING_DIM
    )


@pytest.fixture(scope="module")
def reranker() -> FastEmbedReranker:
    return FastEmbedReranker(RERANKER_MODEL, cache_dir=None, tracer=Tracer.disabled())


async def test_embed_passages_returns_expected_dim_vectors(embedder: FastEmbedEmbedder) -> None:
    vectors = await embedder.embed_passages(
        ["binary search halves the search range", "a cat sat on the mat"]
    )
    assert len(vectors) == 2
    for vector in vectors:
        assert len(vector) == EMBEDDING_DIM
        assert all(isinstance(component, float) for component in vector)


async def test_embed_passages_empty_returns_empty_list(embedder: FastEmbedEmbedder) -> None:
    assert await embedder.embed_passages([]) == []


async def test_embed_query_returns_expected_dim_vector(embedder: FastEmbedEmbedder) -> None:
    vector = await embedder.embed_query("two pointers technique")
    assert len(vector) == EMBEDDING_DIM


def test_embedder_dim_property(embedder: FastEmbedEmbedder) -> None:
    assert embedder.dim == EMBEDDING_DIM


def test_mismatched_expected_dim_raises_config_error() -> None:
    with pytest.raises(EmbeddingConfigError):
        FastEmbedEmbedder(
            EMBEDDING_MODEL, cache_dir=None, tracer=Tracer.disabled(), expected_dim=999
        )


def test_unknown_model_raises_config_error() -> None:
    with pytest.raises(EmbeddingConfigError):
        FastEmbedEmbedder(
            "not/a-real-model",
            cache_dir=None,
            tracer=Tracer.disabled(),
            expected_dim=EMBEDDING_DIM,
        )


async def test_inner_failure_wrapped_without_leaking_message(
    embedder: FastEmbedEmbedder, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(self: TextEmbedding, texts: Iterable[str], **kwargs: Any) -> Iterable[Any]:
        raise RuntimeError("secret-marker")

    monkeypatch.setattr(TextEmbedding, "passage_embed", _boom)
    with pytest.raises(EmbeddingError) as exc_info:
        await embedder.embed_passages(["hello"])
    message = str(exc_info.value)
    assert "secret-marker" not in message
    assert "RuntimeError" in message


async def test_rerank_ranks_relevant_document_higher(reranker: FastEmbedReranker) -> None:
    query = "how does binary search work"
    docs = [
        "The recipe calls for two cups of flour and a pinch of salt.",
        "Binary search repeatedly halves a sorted array to find a target value.",
    ]
    scores = await reranker.rerank(query, docs)
    assert len(scores) == 2
    assert scores[1] > scores[0]


async def test_rerank_empty_documents_returns_empty_list(reranker: FastEmbedReranker) -> None:
    assert await reranker.rerank("query", []) == []


# --------------------------------------------------------------------------
# Dedicated executor
# --------------------------------------------------------------------------


async def test_embed_passages_runs_on_dedicated_fastembed_thread(
    embedder: FastEmbedEmbedder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inference must run on the dedicated `_EXECUTOR` (`thread_name_prefix=
    "fastembed"`), never `asyncio.to_thread`'s shared default executor."""
    thread_names: list[str] = []
    original = embedder._embed_passages_sync  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]

    def _spy(texts: list[str]) -> list[list[float]]:
        thread_names.append(threading.current_thread().name)
        return original(texts)

    monkeypatch.setattr(embedder, "_embed_passages_sync", _spy)

    await embedder.embed_passages(["hello"])

    assert thread_names
    assert thread_names[0].startswith("fastembed")


async def test_rerank_runs_on_dedicated_fastembed_thread(
    reranker: FastEmbedReranker, monkeypatch: pytest.MonkeyPatch
) -> None:
    thread_names: list[str] = []
    original = reranker._rerank_sync  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]

    def _spy(query: str, documents: list[str]) -> list[float]:
        thread_names.append(threading.current_thread().name)
        return original(query, documents)

    monkeypatch.setattr(reranker, "_rerank_sync", _spy)

    await reranker.rerank("query", ["a document"])

    assert thread_names
    assert thread_names[0].startswith("fastembed")


# --------------------------------------------------------------------------
# Tracing
# --------------------------------------------------------------------------


def _traced_embedder() -> tuple[FastEmbedEmbedder, LangSmithClient]:
    mock_client = cast(LangSmithClient, MagicMock(spec=LangSmithClient))
    tracer = Tracer(enabled=True, client=mock_client, project_name="test-project")
    embedder = FastEmbedEmbedder(
        EMBEDDING_MODEL, cache_dir=None, tracer=tracer, expected_dim=EMBEDDING_DIM
    )
    return embedder, mock_client


async def test_embed_query_creates_traced_run_without_raw_text() -> None:
    embedder, mock_client = _traced_embedder()

    await embedder.embed_query("a secret problem statement the user typed")

    create_run = cast(MagicMock, mock_client.create_run)  # pyright: ignore[reportAttributeAccessIssue]
    assert create_run.called
    kwargs = create_run.call_args.kwargs
    assert kwargs["name"] == "embedding.embed_query"
    assert kwargs["inputs"] == {"model": EMBEDDING_MODEL, "n_texts": 1}
    assert "secret problem statement" not in str(kwargs["inputs"])


async def test_embed_passages_disabled_tracer_makes_no_client_calls(
    embedder: FastEmbedEmbedder,
) -> None:
    """`embedder` (the module-scope fixture) is built with `Tracer.disabled()`
    and has no LangSmith client configured at all -- a disabled tracer's
    `Tracer.run` is a complete no-op passthrough, so this must succeed without
    ever touching a LangSmith client."""
    vectors = await embedder.embed_passages(["hello"])
    assert len(vectors) == 1
