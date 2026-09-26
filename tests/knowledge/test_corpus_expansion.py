"""Retrieval tests for the expanded DSA corpus (30 docs / 300 chunks; the
original 13 patterns plus 17 new ones: union_find, topological_sort,
dijkstra, bellman_ford, binary_search_on_answer, monotonic_stack, stack,
fast_slow_pointers, linked_list, dp_1d, dp_2d, intervals, bit_manipulation,
trie, divide_and_conquer, math_geometry, segment_tree).

Deterministic, in-memory harness (like `test_fusion_eval.py`): an in-memory
Qdrant instance (`AsyncQdrantClient(location=":memory:")`) ingested with the
real corpus using the real cached `bge-small` embedder and the real BM25
index, so these tests run without the live `dsa_knowledge` container. The
fail-soft test points a client at an unreachable port (like
`test_phase5_manual.py`'s `dead_qdrant_retriever`) rather than needing
`integration`.

Per the work packet: queries are never adjusted and the corpus is never
touched to make a test pass -- a query that doesn't rank its expected
pattern first is a finding to report, not a bug to fix here.
"""

import asyncio
from collections.abc import Generator

import pytest
from qdrant_client import AsyncQdrantClient

from app.knowledge.bm25 import BM25Index
from app.knowledge.ingest import build_bm25, chunk_corpus, ingest_corpus, load_corpus
from app.knowledge.retrieve import KnowledgeRetriever
from app.llm.client import Tracer
from app.llm.embeddings import FastEmbedEmbedder, FastEmbedReranker
from app.schemas.knowledge import CorpusDocument, KnowledgeChunk

COLLECTION = "test_corpus_expansion"
RETRIEVE_TIMEOUT_S = 10.0
DEAD_TIMEOUT_S = 3.0

#: (query, expected pattern) for the 17 newly added patterns (a subset --
#: the headline set from the work packet).
_NEW_PATTERN_QUERIES: list[tuple[str, str]] = [
    ("shortest path with negative edges", "bellman_ford"),
    ("range queries with updates", "segment_tree"),
    ("dependency ordering with prerequisites", "topological_sort"),
    ("next greater element", "monotonic_stack"),
    ("minimize a value over a search space", "binary_search_on_answer"),
    ("detect a cycle in a linked list", "fast_slow_pointers"),
    ("merge overlapping ranges", "intervals"),
    ("prefix search autocomplete dictionary", "trie"),
    ("count the set bits using XOR tricks", "bit_manipulation"),
    ("connected components in an undirected graph", "union_find"),
    ("shortest path with weighted edges no negatives", "dijkstra"),
]

#: (query, expected pattern, expected topic) checked against front-matter
#: (`app/knowledge/corpus/<pattern>.md`) for a handful of the new patterns.
_METADATA_CHECK_QUERIES: list[tuple[str, str, str]] = [
    ("shortest path with negative edges", "bellman_ford", "graphs"),
    ("range queries with updates", "segment_tree", "range_queries"),
    ("dependency ordering with prerequisites", "topological_sort", "graphs"),
    ("next greater element", "monotonic_stack", "stacks"),
    ("connected components in an undirected graph", "union_find", "graphs"),
]

#: A subset of the new-pattern queries exercised with the reranker enabled.
_RERANK_QUERIES: list[tuple[str, str]] = [
    ("shortest path with negative edges", "bellman_ford"),
    ("next greater element", "monotonic_stack"),
    ("prefix search autocomplete dictionary", "trie"),
]

#: The original 13 patterns must still retrieve at rank 1 for their
#: canonical query.
_REGRESSION_QUERIES: list[tuple[str, str]] = [
    ("how to shrink a variable-size window", "sliding_window"),
    ("two pointers on a sorted array to find a pair", "two_pointers"),
    ("count frequencies with a hash map", "hashing"),
    ("backtracking to generate all subsets", "backtracking"),
    ("kth largest element with a heap", "heaps"),
]


# ---------------------------------------------------------------------------
# Fixtures: in-memory Qdrant + real embedder/reranker + real BM25.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def corpus() -> tuple[list[CorpusDocument], list[KnowledgeChunk], BM25Index]:
    docs = load_corpus()
    chunks = chunk_corpus(docs)
    bm25 = build_bm25(chunks)
    return docs, chunks, bm25


@pytest.fixture(scope="module")
def embedder() -> FastEmbedEmbedder:
    return FastEmbedEmbedder(
        "BAAI/bge-small-en-v1.5", cache_dir=None, tracer=Tracer.disabled(), expected_dim=384
    )


@pytest.fixture(scope="module")
def reranker() -> FastEmbedReranker | None:
    """The real cross-encoder reranker, or None if it can't be loaded
    offline (no network / model not cached) -- tests that need it skip."""
    try:
        return FastEmbedReranker(
            "Xenova/ms-marco-MiniLM-L-6-v2", cache_dir=None, tracer=Tracer.disabled()
        )
    except Exception as exc:
        print(f"\nreranker unavailable, tests needing it will skip: {exc!r}")
        return None


@pytest.fixture(scope="module")
def qdrant_client(
    corpus: tuple[list[CorpusDocument], list[KnowledgeChunk], BM25Index],
    embedder: FastEmbedEmbedder,
) -> Generator[AsyncQdrantClient]:
    """In-memory Qdrant, ingested once with the full 30-doc corpus.

    Built/closed via its own short-lived `asyncio.run` loops rather than an
    async fixture: local-mode `AsyncQdrantClient` holds no event-loop-bound
    state (no `asyncio.Lock`/`Task`, only synchronous in-process storage), so
    it's safe to ingest here and then `await` its query methods from each
    test's own (function-scoped) event loop.
    """
    docs, chunks, _bm25 = corpus
    client = AsyncQdrantClient(location=":memory:")
    asyncio.run(
        ingest_corpus(client, embedder, collection=COLLECTION, chunks=chunks, documents=len(docs))
    )
    yield client
    asyncio.run(client.close())


@pytest.fixture(scope="module")
def retriever(
    qdrant_client: AsyncQdrantClient,
    corpus: tuple[list[CorpusDocument], list[KnowledgeChunk], BM25Index],
    embedder: FastEmbedEmbedder,
) -> KnowledgeRetriever:
    """Dense + BM25 -> RRF fusion, no reranker (isolates fusion ranking)."""
    _docs, chunks, bm25 = corpus
    return KnowledgeRetriever(
        client=qdrant_client,
        collection=COLLECTION,
        embedder=embedder,
        reranker=None,
        chunks=chunks,
        bm25=bm25,
        timeout_s=RETRIEVE_TIMEOUT_S,
    )


@pytest.fixture(scope="module")
def reranked_retriever(
    qdrant_client: AsyncQdrantClient,
    corpus: tuple[list[CorpusDocument], list[KnowledgeChunk], BM25Index],
    embedder: FastEmbedEmbedder,
    reranker: FastEmbedReranker | None,
) -> KnowledgeRetriever | None:
    """Same ingested collection as `retriever`, with the reranker attached."""
    if reranker is None:
        return None
    _docs, chunks, bm25 = corpus
    return KnowledgeRetriever(
        client=qdrant_client,
        collection=COLLECTION,
        embedder=embedder,
        reranker=reranker,
        chunks=chunks,
        bm25=bm25,
        timeout_s=RETRIEVE_TIMEOUT_S,
    )


@pytest.fixture(scope="module")
def dead_qdrant_retriever(
    corpus: tuple[list[CorpusDocument], list[KnowledgeChunk], BM25Index],
    embedder: FastEmbedEmbedder,
) -> Generator[KnowledgeRetriever]:
    """Same real corpus/BM25/embedder, wired to an unreachable Qdrant so
    dense search must fail-soft to BM25-only."""
    _docs, chunks, bm25 = corpus
    dead_client = AsyncQdrantClient(url="http://127.0.0.1:1", timeout=1, check_compatibility=False)
    yield KnowledgeRetriever(
        client=dead_client,
        collection=COLLECTION,
        embedder=embedder,
        reranker=None,
        chunks=chunks,
        bm25=bm25,
        timeout_s=DEAD_TIMEOUT_S,
    )
    asyncio.run(dead_client.close())


# ---------------------------------------------------------------------------
# Test 1: new-pattern retrieval -- the headline test.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query,expected_pattern", _NEW_PATTERN_QUERIES, ids=[q for q, _ in _NEW_PATTERN_QUERIES]
)
async def test_new_pattern_ranks_first(
    retriever: KnowledgeRetriever, query: str, expected_pattern: str
) -> None:
    hits = await retriever.retrieve(query, top_k=3)
    top3 = [(hit.chunk.pattern, hit.chunk.heading, round(hit.score, 4)) for hit in hits[:3]]
    print(f"\nACTUAL: query={query!r} expected={expected_pattern!r} top3={top3}")

    assert hits, f"no hits at all for {query!r}"
    assert hits[0].chunk.pattern == expected_pattern


# ---------------------------------------------------------------------------
# Test 2: recognition-cue queries carry correct metadata.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query,expected_pattern,expected_topic",
    _METADATA_CHECK_QUERIES,
    ids=[q for q, _, _ in _METADATA_CHECK_QUERIES],
)
async def test_winning_hit_metadata(
    retriever: KnowledgeRetriever, query: str, expected_pattern: str, expected_topic: str
) -> None:
    hits = await retriever.retrieve(query, top_k=1)

    assert hits
    top = hits[0]
    print(
        "\nACTUAL: "
        f"query={query!r} pattern={top.chunk.pattern!r} topic={top.chunk.topic!r} "
        f"pattern_family={top.chunk.metadata.get('pattern_family')!r} "
        f"identification_signals={top.chunk.metadata.get('identification_signals')!r}"
    )

    assert top.chunk.pattern == expected_pattern
    assert top.chunk.topic == expected_topic
    assert top.chunk.metadata.get("pattern_family", "")
    assert top.chunk.metadata.get("identification_signals", "")


# ---------------------------------------------------------------------------
# Test 3: reranker ranks the intended pattern first.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query,expected_pattern", _RERANK_QUERIES, ids=[q for q, _ in _RERANK_QUERIES]
)
async def test_reranker_ranks_intended_pattern_first(
    reranked_retriever: KnowledgeRetriever | None, query: str, expected_pattern: str
) -> None:
    if reranked_retriever is None:
        pytest.skip("reranker model unavailable offline")

    hits = await reranked_retriever.retrieve(query, top_k=3)
    top3 = [(hit.chunk.pattern, hit.chunk.heading, round(hit.score, 4)) for hit in hits[:3]]
    print(f"\nACTUAL (reranked): query={query!r} expected={expected_pattern!r} top3={top3}")

    assert hits
    assert hits[0].reranked is True
    assert hits[0].chunk.pattern == expected_pattern


# ---------------------------------------------------------------------------
# Test 4: regression -- the original 13 patterns still retrieve.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query,expected_pattern", _REGRESSION_QUERIES, ids=[q for q, _ in _REGRESSION_QUERIES]
)
async def test_original_pattern_still_ranks_first(
    retriever: KnowledgeRetriever, query: str, expected_pattern: str
) -> None:
    hits = await retriever.retrieve(query, top_k=3)
    top3 = [(hit.chunk.pattern, hit.chunk.heading, round(hit.score, 4)) for hit in hits[:3]]
    print(f"\nACTUAL: query={query!r} expected={expected_pattern!r} top3={top3}")

    assert hits
    assert hits[0].chunk.pattern == expected_pattern


async def test_topological_sort_query_top3_within_graph_family(
    retriever: KnowledgeRetriever,
) -> None:
    """The umbrella `graphs`/`dynamic_programming` docs now compete against
    narrower new docs, so "rank 1" for this query is genuinely ambiguous;
    the honest assertion is that the top-3 patterns stay within the graph
    family rather than pinning one exact winner."""
    query = "topological sort of a directed acyclic graph"
    hits = await retriever.retrieve(query, top_k=3)
    patterns = [hit.chunk.pattern for hit in hits]
    print(f"\nACTUAL: query={query!r} top3_patterns={patterns}")

    assert hits
    assert set(patterns) <= {"graphs", "topological_sort", "dfs"}


# ---------------------------------------------------------------------------
# Test 5: fail-soft -- Qdrant unreachable degrades to BM25-only.
# ---------------------------------------------------------------------------


async def test_dead_qdrant_degrades_to_bm25_only_no_raise(
    dead_qdrant_retriever: KnowledgeRetriever,
) -> None:
    hits = await dead_qdrant_retriever.retrieve("shortest path with negative edges", top_k=5)

    print(
        "\nACTUAL: "
        f"n_hits={len(hits)} retrievers={[hit.retrievers for hit in hits]} "
        f"reranked={[hit.reranked for hit in hits]}"
    )

    assert hits
    for hit in hits:
        assert hit.retrievers == ("bm25",)
        assert hit.reranked is False
