"""Tests for `app.knowledge.retrieve`: RRF fusion and the fail-soft hybrid
`KnowledgeRetriever`."""

import asyncio
import hashlib
from collections.abc import AsyncGenerator, Sequence

import pytest
import pytest_asyncio
from qdrant_client import AsyncQdrantClient

from app.knowledge.bm25 import BM25Index, tokenize
from app.knowledge.ingest import build_bm25, chunk_corpus, ingest_corpus, load_corpus
from app.knowledge.retrieve import (
    MAX_QUERY_CHARS,
    KnowledgeRetriever,
    rrf_fuse,
)
from app.llm.client import Tracer
from app.llm.embeddings import FastEmbedEmbedder, FastEmbedReranker
from app.schemas.knowledge import KnowledgeChunk

DIM = 8
COLLECTION = "test_retrieve"


class FakeEmbedder:
    """Deterministic `Embedder`: a vector derived from the sha256 of the
    input text, no network/model involved."""

    def __init__(self, dim: int = DIM) -> None:
        self._dim = dim
        self.last_query: str | None = None

    @property
    def dim(self) -> int:
        return self._dim

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [digest[i % len(digest)] / 255.0 for i in range(self._dim)]

    async def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        self.last_query = text
        return self._vector(text)


class RaisingEmbedder:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    @property
    def dim(self) -> int:
        return DIM

    async def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        return [[0.0] * DIM for _ in texts]

    async def embed_query(self, text: str) -> list[float]:
        raise self._exc


class SlowEmbedder:
    def __init__(self, delay: float) -> None:
        self._delay = delay

    @property
    def dim(self) -> int:
        return DIM

    async def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        return [[0.0] * DIM for _ in texts]

    async def embed_query(self, text: str) -> list[float]:
        await asyncio.sleep(self._delay)
        return [0.0] * DIM


class OverlapReranker:
    """Fake reranker: score = number of shared tokens between query and doc."""

    async def rerank(self, query: str, documents: Sequence[str]) -> list[float]:
        query_tokens = set(tokenize(query))
        return [float(len(query_tokens & set(tokenize(doc)))) for doc in documents]


class RaisingReranker:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def rerank(self, query: str, documents: Sequence[str]) -> list[float]:
        raise self._exc


class SlowReranker:
    def __init__(self, delay: float) -> None:
        self._delay = delay

    async def rerank(self, query: str, documents: Sequence[str]) -> list[float]:
        await asyncio.sleep(self._delay)
        return [1.0] * len(documents)


class WrongCountReranker:
    async def rerank(self, query: str, documents: Sequence[str]) -> list[float]:
        return [1.0]


# --------------------------------------------------------------------------
# rrf_fuse
# --------------------------------------------------------------------------


def test_rrf_fuse_empty_input_returns_empty_list() -> None:
    assert rrf_fuse({}) == []


def test_rrf_fuse_hand_computed_scores() -> None:
    fused = rrf_fuse({"dense": ["a", "b", "c"], "bm25": ["b", "a"]})
    k = 60
    expected = {
        "a": 1 / (k + 1) + 1 / (k + 2),
        "b": 1 / (k + 2) + 1 / (k + 1),
        "c": 1 / (k + 3),
    }
    by_id = {f.id: f for f in fused}
    for doc_id, score in expected.items():
        assert by_id[doc_id].score == pytest.approx(score)

    # a and b tie in score -> broken by id.
    assert fused[0].id == "a"
    assert fused[1].id == "b"
    assert fused[2].id == "c"


def test_rrf_fuse_id_in_both_rankings_outranks_single_ranking_ids() -> None:
    fused = rrf_fuse({"dense": ["x", "y"], "bm25": ["x", "z"]})
    assert fused[0].id == "x"
    by_id = {f.id: f for f in fused}
    assert by_id["x"].retrievers == ("dense", "bm25")
    assert by_id["y"].retrievers == ("dense",)
    assert by_id["z"].retrievers == ("bm25",)


def test_rrf_fuse_ties_deterministic_by_id() -> None:
    fused = rrf_fuse({"bm25": ["b", "a", "c"]})
    # single ranking: order preserved (no ties here), but running twice is
    # deterministic.
    fused2 = rrf_fuse({"bm25": ["b", "a", "c"]})
    assert fused == fused2


# --------------------------------------------------------------------------
# KnowledgeRetriever fixtures
# --------------------------------------------------------------------------


@pytest_asyncio.fixture
async def memory_client() -> AsyncGenerator[AsyncQdrantClient]:
    client = AsyncQdrantClient(location=":memory:")
    try:
        yield client
    finally:
        await client.close()


@pytest.fixture
def all_chunks() -> list[KnowledgeChunk]:
    return chunk_corpus(load_corpus())


@pytest.fixture
def bm25(all_chunks: list[KnowledgeChunk]) -> BM25Index:
    return build_bm25(all_chunks)


@pytest_asyncio.fixture
async def ingested_client(
    memory_client: AsyncQdrantClient, all_chunks: list[KnowledgeChunk]
) -> AsyncQdrantClient:
    embedder = FakeEmbedder()
    await ingest_corpus(
        memory_client,
        embedder,
        collection=COLLECTION,
        chunks=all_chunks,
        documents=13,
    )
    return memory_client


# --------------------------------------------------------------------------
# End-to-end with fakes
# --------------------------------------------------------------------------


async def test_retrieve_returns_at_most_top_k_hits_with_expected_fields(
    ingested_client: AsyncQdrantClient, all_chunks: list[KnowledgeChunk], bm25: BM25Index
) -> None:
    retriever = KnowledgeRetriever(
        client=ingested_client,
        collection=COLLECTION,
        embedder=FakeEmbedder(),
        reranker=OverlapReranker(),
        chunks=all_chunks,
        bm25=bm25,
        timeout_s=5.0,
    )
    hits = await retriever.retrieve("sliding window shrink variable size", top_k=3)
    assert 0 < len(hits) <= 3
    for hit in hits:
        assert hit.chunk.source
        assert hit.chunk.topic
        assert hit.chunk.pattern
        assert hit.reranked is True
        assert set(hit.retrievers) <= {"dense", "bm25"}


async def test_reranked_order_follows_reranker_scores(
    ingested_client: AsyncQdrantClient, all_chunks: list[KnowledgeChunk], bm25: BM25Index
) -> None:
    retriever = KnowledgeRetriever(
        client=ingested_client,
        collection=COLLECTION,
        embedder=FakeEmbedder(),
        reranker=OverlapReranker(),
        chunks=all_chunks,
        bm25=bm25,
        timeout_s=5.0,
    )
    hits = await retriever.retrieve("sliding window technique for arrays", top_k=5)
    assert all(hit.reranked for hit in hits)
    scores = [hit.score for hit in hits]
    assert scores == sorted(scores, reverse=True)


async def test_retrievers_field_reflects_source_lists(
    ingested_client: AsyncQdrantClient, all_chunks: list[KnowledgeChunk], bm25: BM25Index
) -> None:
    retriever = KnowledgeRetriever(
        client=ingested_client,
        collection=COLLECTION,
        embedder=FakeEmbedder(),
        reranker=None,
        chunks=all_chunks,
        bm25=bm25,
        timeout_s=5.0,
    )
    hits = await retriever.retrieve("binary search sorted array", top_k=10)
    assert hits
    for hit in hits:
        assert hit.retrievers
        assert set(hit.retrievers) <= {"dense", "bm25"}


# --------------------------------------------------------------------------
# Soft-fail behaviour
# --------------------------------------------------------------------------


async def test_dead_server_client_falls_back_to_bm25_only(
    all_chunks: list[KnowledgeChunk], bm25: BM25Index
) -> None:
    dead_client = AsyncQdrantClient(url="http://127.0.0.1:1", timeout=1, check_compatibility=False)
    try:
        retriever = KnowledgeRetriever(
            client=dead_client,
            collection=COLLECTION,
            embedder=FakeEmbedder(),
            reranker=None,
            chunks=all_chunks,
            bm25=bm25,
            timeout_s=1.0,
        )
        hits = await retriever.retrieve("binary search halves the array", top_k=5)
        assert hits
        for hit in hits:
            assert hit.retrievers == ("bm25",)
    finally:
        await dead_client.close()


async def test_client_and_embedder_none_is_bm25_only(
    all_chunks: list[KnowledgeChunk], bm25: BM25Index
) -> None:
    retriever = KnowledgeRetriever(
        client=None,
        collection=COLLECTION,
        embedder=None,
        reranker=None,
        chunks=all_chunks,
        bm25=bm25,
        timeout_s=1.0,
    )
    hits = await retriever.retrieve("two pointers technique", top_k=5)
    assert hits
    for hit in hits:
        assert hit.retrievers == ("bm25",)


async def test_embedder_raising_embedding_error_falls_back_to_bm25(
    ingested_client: AsyncQdrantClient, all_chunks: list[KnowledgeChunk], bm25: BM25Index
) -> None:
    from app.llm.embeddings import EmbeddingError

    retriever = KnowledgeRetriever(
        client=ingested_client,
        collection=COLLECTION,
        embedder=RaisingEmbedder(EmbeddingError("boom")),
        reranker=None,
        chunks=all_chunks,
        bm25=bm25,
        timeout_s=1.0,
    )
    hits = await retriever.retrieve("depth first search on a graph", top_k=5)
    assert hits
    for hit in hits:
        assert hit.retrievers == ("bm25",)


async def test_reranker_raising_falls_back_to_fused_order(
    ingested_client: AsyncQdrantClient, all_chunks: list[KnowledgeChunk], bm25: BM25Index
) -> None:
    retriever = KnowledgeRetriever(
        client=ingested_client,
        collection=COLLECTION,
        embedder=FakeEmbedder(),
        reranker=RaisingReranker(RuntimeError("boom")),
        chunks=all_chunks,
        bm25=bm25,
        timeout_s=5.0,
    )
    hits = await retriever.retrieve("breadth first search queue", top_k=5)
    assert hits
    for hit in hits:
        assert hit.reranked is False


async def test_reranker_wrong_score_count_falls_back_to_fused_order(
    ingested_client: AsyncQdrantClient, all_chunks: list[KnowledgeChunk], bm25: BM25Index
) -> None:
    retriever = KnowledgeRetriever(
        client=ingested_client,
        collection=COLLECTION,
        embedder=FakeEmbedder(),
        reranker=WrongCountReranker(),
        chunks=all_chunks,
        bm25=bm25,
        timeout_s=5.0,
    )
    hits = await retriever.retrieve("greedy algorithm interval scheduling", top_k=5)
    assert hits
    for hit in hits:
        assert hit.reranked is False


async def test_slow_embedder_times_out_and_falls_back_to_bm25(
    ingested_client: AsyncQdrantClient, all_chunks: list[KnowledgeChunk], bm25: BM25Index
) -> None:
    retriever = KnowledgeRetriever(
        client=ingested_client,
        collection=COLLECTION,
        embedder=SlowEmbedder(delay=1.0),
        reranker=None,
        chunks=all_chunks,
        bm25=bm25,
        timeout_s=0.05,
    )
    loop = asyncio.get_event_loop()
    start = loop.time()
    hits = await retriever.retrieve("dynamic programming subproblems", top_k=5)
    elapsed = loop.time() - start
    assert elapsed < 0.9
    assert hits
    for hit in hits:
        assert hit.retrievers == ("bm25",)


async def test_timeout_bounds_the_whole_call_not_each_stage(
    ingested_client: AsyncQdrantClient, all_chunks: list[KnowledgeChunk], bm25: BM25Index
) -> None:
    """A slow embedder (dense stage) and a slow reranker (rerank stage) each
    individually finish under `timeout_s`, but their combined time doesn't --
    `retrieve` must still return within ~`timeout_s`, with the rerank stage
    skipped (`reranked=False`, fused order) rather than exceeding the budget."""
    retriever = KnowledgeRetriever(
        client=ingested_client,
        collection=COLLECTION,
        embedder=SlowEmbedder(delay=0.2),
        reranker=SlowReranker(delay=0.2),
        chunks=all_chunks,
        bm25=bm25,
        timeout_s=0.3,
    )
    loop = asyncio.get_event_loop()
    start = loop.time()
    hits = await retriever.retrieve("dynamic programming subproblems", top_k=5)
    elapsed = loop.time() - start

    assert elapsed < 0.5  # well under 0.2 + 0.2 = 0.4s of unbounded work
    assert hits
    for hit in hits:
        assert hit.reranked is False


async def test_everything_broken_returns_empty_list(
    ingested_client: AsyncQdrantClient,
    all_chunks: list[KnowledgeChunk],
    bm25: BM25Index,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(self: BM25Index, query: str, k: int) -> list[tuple[str, float]]:
        raise RuntimeError("boom")

    monkeypatch.setattr(BM25Index, "search", _boom)

    retriever = KnowledgeRetriever(
        client=ingested_client,
        collection=COLLECTION,
        embedder=RaisingEmbedder(RuntimeError("boom")),
        reranker=RaisingReranker(RuntimeError("boom")),
        chunks=all_chunks,
        bm25=bm25,
        timeout_s=1.0,
    )
    hits = await retriever.retrieve("anything at all", top_k=5)
    assert hits == []


async def test_empty_and_whitespace_query_returns_empty_list(
    ingested_client: AsyncQdrantClient, all_chunks: list[KnowledgeChunk], bm25: BM25Index
) -> None:
    retriever = KnowledgeRetriever(
        client=ingested_client,
        collection=COLLECTION,
        embedder=FakeEmbedder(),
        reranker=None,
        chunks=all_chunks,
        bm25=bm25,
        timeout_s=1.0,
    )
    assert await retriever.retrieve("", top_k=5) == []
    assert await retriever.retrieve("   ", top_k=5) == []


async def test_query_truncated_to_max_query_chars(
    ingested_client: AsyncQdrantClient, all_chunks: list[KnowledgeChunk], bm25: BM25Index
) -> None:
    embedder = FakeEmbedder()
    retriever = KnowledgeRetriever(
        client=ingested_client,
        collection=COLLECTION,
        embedder=embedder,
        reranker=None,
        chunks=all_chunks,
        bm25=bm25,
        timeout_s=5.0,
    )
    long_query = "binary search " * 200
    assert len(long_query) > MAX_QUERY_CHARS
    await retriever.retrieve(long_query, top_k=5)
    assert embedder.last_query == long_query.strip()[:MAX_QUERY_CHARS]


async def test_cancelled_error_propagates(
    ingested_client: AsyncQdrantClient, all_chunks: list[KnowledgeChunk], bm25: BM25Index
) -> None:
    retriever = KnowledgeRetriever(
        client=ingested_client,
        collection=COLLECTION,
        embedder=RaisingEmbedder(asyncio.CancelledError()),
        reranker=None,
        chunks=all_chunks,
        bm25=bm25,
        timeout_s=5.0,
    )
    with pytest.raises(asyncio.CancelledError):
        await retriever.retrieve("depth first search traversal", top_k=5)


# --------------------------------------------------------------------------
# Real models (no marker -- models are cached locally)
# --------------------------------------------------------------------------


async def test_real_models_sliding_window_query_ranks_pattern_first(
    memory_client: AsyncQdrantClient, all_chunks: list[KnowledgeChunk], bm25: BM25Index
) -> None:
    embedder = FastEmbedEmbedder(
        "BAAI/bge-small-en-v1.5", cache_dir=None, tracer=Tracer.disabled(), expected_dim=384
    )
    reranker = FastEmbedReranker(
        "Xenova/ms-marco-MiniLM-L-6-v2", cache_dir=None, tracer=Tracer.disabled()
    )
    await ingest_corpus(
        memory_client,
        embedder,
        collection="test_retrieve_real",
        chunks=all_chunks,
        documents=13,
    )
    retriever = KnowledgeRetriever(
        client=memory_client,
        collection="test_retrieve_real",
        embedder=embedder,
        reranker=reranker,
        chunks=all_chunks,
        bm25=bm25,
        timeout_s=10.0,
    )
    hits = await retriever.retrieve("how to shrink a variable-size window", top_k=5)
    assert hits
    top = hits[0]
    assert top.chunk.pattern == "sliding_window"
    assert top.reranked is True
    assert top.chunk.source.endswith("sliding_window.md")
    assert top.chunk.topic == "arrays"

    print("\ntop-5 for 'how to shrink a variable-size window':")
    for hit in hits:
        print(
            f"  pattern={hit.chunk.pattern!r} heading={hit.chunk.heading!r} "
            f"score={hit.score:.4f} retrievers={hit.retrievers}"
        )
