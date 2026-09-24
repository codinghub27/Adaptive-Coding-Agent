"""RRF fusion evaluation: does fusing dense + BM25 beat either alone?

Deterministic, in-memory, no network: an in-memory Qdrant instance
(`AsyncQdrantClient(location=":memory:")`) ingested with the real corpus
using the real cached `bge-small` embedder, plus the real in-house BM25
index. No reranker here -- the whole point is to isolate `rrf_fuse`'s effect
from reranking.
"""

from qdrant_client import AsyncQdrantClient

from app.knowledge.ingest import build_bm25, chunk_corpus, ingest_corpus, load_corpus
from app.knowledge.retrieve import rrf_fuse
from app.llm.client import Tracer
from app.llm.embeddings import FastEmbedEmbedder
from app.schemas.knowledge import KnowledgeChunk

COLLECTION = "test_fusion_eval"
DENSE_LIMIT = 20
BM25_LIMIT = 20
TOP_K_MRR = 5
TOP_K_HIT = 3

#: (query, expected pattern). Mixes keyword-heavy queries that share exact
#: corpus/alias vocabulary (favour BM25) with paraphrases that share almost
#: no vocabulary with the corpus text (favour dense embeddings).
_LABELED_QUERIES: list[tuple[str, str]] = [
    # -- keyword-style (favour BM25) --
    ("heapq kth largest", "heaps"),
    ("union find disjoint set", "graphs"),
    ("memoization overlapping subproblems", "dynamic_programming"),
    ("monotonic deque maximum", "sliding_window"),
    ("hash map frequency dictionary lookup", "hashing"),
    ("binary search on answer bisect", "binary_search"),
    # -- paraphrase / semantic (favour dense) --
    (
        "I keep getting the wrong middle index and loop forever when searching a sorted list",
        "binary_search",
    ),
    ("explore all subsets and undo choices", "backtracking"),
    ("cheapest path when edges have costs", "graphs"),
    ("count subarrays whose total equals a target quickly", "prefix_sum"),
    ("find the closest shared ancestor node between two leaves of a hierarchy", "trees"),
    (
        "two people starting from opposite ends of a sorted list and moving toward the middle",
        "two_pointers",
    ),
]


def _dedup_patterns(ranked_ids: list[str], chunk_map: dict[str, KnowledgeChunk]) -> list[str]:
    """Ranked ids -> ranked, order-preserving, deduplicated pattern list."""
    seen: list[str] = []
    for doc_id in ranked_ids:
        pattern = chunk_map[doc_id].pattern
        if pattern not in seen:
            seen.append(pattern)
    return seen


def _reciprocal_rank(ranked_patterns: list[str], label: str, *, top_k: int) -> float:
    top = ranked_patterns[:top_k]
    if label not in top:
        return 0.0
    return 1.0 / (top.index(label) + 1)


def _hit(ranked_patterns: list[str], label: str, *, top_k: int) -> bool:
    return label in ranked_patterns[:top_k]


async def test_rrf_fusion_beats_or_matches_either_single_retriever_alone() -> None:
    docs = load_corpus()
    chunks = chunk_corpus(docs)
    chunk_map = {chunk.id: chunk for chunk in chunks}
    bm25 = build_bm25(chunks)

    embedder = FastEmbedEmbedder(
        "BAAI/bge-small-en-v1.5", cache_dir=None, tracer=Tracer.disabled(), expected_dim=384
    )

    client = AsyncQdrantClient(location=":memory:")
    try:
        await ingest_corpus(
            client, embedder, collection=COLLECTION, chunks=chunks, documents=len(docs)
        )

        dense_mrr = 0.0
        bm25_mrr = 0.0
        fused_mrr = 0.0
        dense_hits = 0
        bm25_hits = 0
        fused_hits = 0

        rows: list[tuple[str, str, list[str], list[str], list[str]]] = []

        for query, label in _LABELED_QUERIES:
            vector = await embedder.embed_query(query)
            resp = await client.query_points(
                collection_name=COLLECTION, query=vector, limit=DENSE_LIMIT, with_payload=False
            )
            dense_ids = [str(point.id) for point in resp.points]
            bm25_ids = [doc_id for doc_id, _score in bm25.search(query, BM25_LIMIT)]
            fused_ids = [fused.id for fused in rrf_fuse({"dense": dense_ids, "bm25": bm25_ids})]

            dense_patterns = _dedup_patterns(dense_ids, chunk_map)
            bm25_patterns = _dedup_patterns(bm25_ids, chunk_map)
            fused_patterns = _dedup_patterns(fused_ids, chunk_map)

            dense_mrr += _reciprocal_rank(dense_patterns, label, top_k=TOP_K_MRR)
            bm25_mrr += _reciprocal_rank(bm25_patterns, label, top_k=TOP_K_MRR)
            fused_mrr += _reciprocal_rank(fused_patterns, label, top_k=TOP_K_MRR)

            dense_hits += int(_hit(dense_patterns, label, top_k=TOP_K_HIT))
            bm25_hits += int(_hit(bm25_patterns, label, top_k=TOP_K_HIT))
            fused_hits += int(_hit(fused_patterns, label, top_k=TOP_K_HIT))

            rows.append(
                (
                    query,
                    label,
                    dense_patterns[:TOP_K_MRR],
                    bm25_patterns[:TOP_K_MRR],
                    fused_patterns[:TOP_K_MRR],
                )
            )
    finally:
        await client.close()

    n = len(_LABELED_QUERIES)
    mrr_dense = dense_mrr / n
    mrr_bm25 = bm25_mrr / n
    mrr_fused = fused_mrr / n
    hit3_dense = dense_hits / n
    hit3_bm25 = bm25_hits / n
    hit3_fused = fused_hits / n

    print("\nACTUAL: per-query top-5 patterns (dense | bm25 | fused):")
    for query, label, dense_p, bm25_p, fused_p in rows:
        print(f"  {query!r} (label={label!r}): dense={dense_p} bm25={bm25_p} fused={fused_p}")
    print(
        "ACTUAL: "
        f"mrr_dense={mrr_dense:.3f} mrr_bm25={mrr_bm25:.3f} mrr_fused={mrr_fused:.3f} "
        f"hit3_dense={hit3_dense:.3f} hit3_bm25={hit3_bm25:.3f} hit3_fused={hit3_fused:.3f}"
    )

    assert mrr_fused >= max(mrr_dense, mrr_bm25)
    assert mrr_fused > min(mrr_dense, mrr_bm25)
    assert hit3_fused >= hit3_dense
    assert hit3_fused >= hit3_bm25
