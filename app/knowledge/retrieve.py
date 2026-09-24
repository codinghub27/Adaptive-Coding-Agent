"""Hybrid knowledge retrieval: dense (Qdrant) + BM25 -> RRF fusion -> rerank.

`KnowledgeRetriever` is the fail-soft entry point the Phase 5 graph node
depends on (through the `Retriever` protocol): dense search and reranking are
best-effort -- any failure (timeout, connection error, embedding/reranker
error, malformed payload) degrades to BM25-only results rather than raising,
so a broken vector store or model never takes down retrieval. The only inputs
here are the curated corpus and the learner's query text; nothing here writes
to the knowledge index.
"""

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from qdrant_client import AsyncQdrantClient

from app.config import Settings
from app.knowledge.base import Retriever
from app.knowledge.bm25 import BM25Index
from app.knowledge.ingest import build_bm25, chunk_corpus, chunk_from_payload, load_corpus
from app.llm.client import Tracer
from app.llm.embeddings import Embedder, Reranker, load_embedder, load_reranker
from app.schemas.knowledge import KnowledgeChunk, RetrievalHit, RetrieverName

__all__ = [
    "BM25_LIMIT",
    "DENSE_LIMIT",
    "MAX_QUERY_CHARS",
    "RERANK_CANDIDATES",
    "RRF_K",
    "FusedId",
    "KnowledgeRetriever",
    "Retriever",
    "create_retriever",
    "rrf_fuse",
]

logger = logging.getLogger(__name__)

#: RRF fusion constant (a larger k flattens the contribution of rank
#: differences; 60 is the standard value from the original RRF paper).
RRF_K: Final = 60
#: Candidate pool sizes pulled from each retriever before fusion.
DENSE_LIMIT: Final = 20
BM25_LIMIT: Final = 20
#: How many fused candidates are sent to the (comparatively expensive)
#: cross-encoder reranker.
RERANK_CANDIDATES: Final = 10
#: Hard cap on query length handed to the embedder/reranker/BM25 index.
MAX_QUERY_CHARS: Final = 1000

#: Fixed retriever order used to build a fused id's `retrievers` tuple.
_RETRIEVER_ORDER: Final[tuple[RetrieverName, ...]] = ("dense", "bm25")


@dataclass(frozen=True)
class FusedId:
    """One id's Reciprocal Rank Fusion result."""

    id: str
    score: float
    retrievers: tuple[RetrieverName, ...]


def rrf_fuse(rankings: Mapping[RetrieverName, Sequence[str]], *, k: int = RRF_K) -> list[FusedId]:
    """Fuse per-retriever rankings (best first) via Reciprocal Rank Fusion.

    Pure and deterministic: for each id, score = sum of 1/(k + rank) over
    every ranking it appears in (rank starting at 1). Sorted by score desc,
    ties broken by id. An id's `retrievers` tuple lists which rankings it
    appeared in, always in `dense, bm25` order regardless of `rankings`'
    iteration order.
    """
    scores: dict[str, float] = {}
    retrievers_by_id: dict[str, list[RetrieverName]] = {}

    for name in _RETRIEVER_ORDER:
        ranking = rankings.get(name)
        if not ranking:
            continue
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
            retrievers_by_id.setdefault(doc_id, []).append(name)

    fused = [
        FusedId(id=doc_id, score=scores[doc_id], retrievers=tuple(retrievers_by_id[doc_id]))
        for doc_id in scores
    ]
    fused.sort(key=lambda item: (-item.score, item.id))
    return fused


class KnowledgeRetriever:
    """Fail-soft hybrid retriever: dense (Qdrant) + BM25 -> RRF -> rerank.

    `client`/`embedder`/`reranker` may each be `None` (or fail at call time);
    in every such case retrieval degrades gracefully rather than raising.
    """

    def __init__(
        self,
        *,
        client: AsyncQdrantClient | None,
        collection: str,
        embedder: Embedder | None,
        reranker: Reranker | None,
        chunks: Sequence[KnowledgeChunk],
        bm25: BM25Index,
        timeout_s: float,
    ) -> None:
        self._client = client
        self._collection = collection
        self._embedder = embedder
        self._reranker = reranker
        self._chunk_map: dict[str, KnowledgeChunk] = {chunk.id: chunk for chunk in chunks}
        self._bm25 = bm25
        self._timeout_s = timeout_s

    async def _dense_ids(
        self, query: str, extra_chunks: dict[str, KnowledgeChunk], deadline: float
    ) -> list[str]:
        """Best-effort dense search; returns [] (never raises, except
        CancelledError) on any failure or if `deadline` has already passed."""
        client = self._client
        embedder = self._embedder
        if client is None or embedder is None:
            return []

        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return []

        async def _search() -> list[str]:
            vector = await embedder.embed_query(query)
            resp = await client.query_points(
                collection_name=self._collection,
                query=vector,
                limit=DENSE_LIMIT,
                with_payload=True,
            )
            ids: list[str] = []
            for point in resp.points:
                point_id = str(point.id)
                if point_id in self._chunk_map:
                    ids.append(point_id)
                    continue
                if point.payload is None:
                    continue
                try:
                    chunk = chunk_from_payload(point.payload)
                except Exception:
                    continue
                extra_chunks[chunk.id] = chunk
                ids.append(chunk.id)
            return ids

        try:
            return await asyncio.wait_for(_search(), timeout=remaining)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("knowledge dense retrieval failed: %s", type(exc).__name__)
            return []

    async def _rerank(
        self,
        query: str,
        candidates: list[tuple[FusedId, KnowledgeChunk]],
        deadline: float,
    ) -> tuple[list[float] | None, bool]:
        """Best-effort rerank of `candidates`. Returns (scores, reranked);
        (None, False) on any failure, if there's no reranker configured, or if
        `deadline` has already passed (the fused order is used instead)."""
        reranker = self._reranker
        if reranker is None or not candidates:
            return None, False

        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return None, False

        async def _run() -> list[float]:
            documents = [chunk.text for _fused, chunk in candidates]
            scores = await reranker.rerank(query, documents)
            if len(scores) != len(candidates):
                raise ValueError("reranker returned a different number of scores than documents")
            return scores

        try:
            scores = await asyncio.wait_for(_run(), timeout=remaining)
            return scores, True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("knowledge rerank failed: %s", type(exc).__name__)
            return None, False

    async def _retrieve_inner(self, query: str, top_k: int, deadline: float) -> list[RetrievalHit]:
        extra_chunks: dict[str, KnowledgeChunk] = {}
        bm25_ids = [doc_id for doc_id, _score in self._bm25.search(query, BM25_LIMIT)]
        dense_ids = await self._dense_ids(query, extra_chunks, deadline)

        rankings: dict[RetrieverName, Sequence[str]] = {}
        if dense_ids:
            rankings["dense"] = dense_ids
        if bm25_ids:
            rankings["bm25"] = bm25_ids

        fused = rrf_fuse(rankings)
        if not fused:
            return []

        chunk_lookup = {**extra_chunks, **self._chunk_map}
        candidates = [
            (fused_id, chunk_lookup[fused_id.id])
            for fused_id in fused[:RERANK_CANDIDATES]
            if fused_id.id in chunk_lookup
        ]

        rerank_scores, reranked = await self._rerank(query, candidates, deadline)

        if reranked and rerank_scores is not None:
            paired = sorted(
                zip(candidates, rerank_scores, strict=True),
                key=lambda item: -item[1],
            )
            ordered: list[tuple[FusedId, KnowledgeChunk, float]] = [
                (fused_id, chunk, score) for (fused_id, chunk), score in paired
            ]
        else:
            ordered = [(fused_id, chunk, fused_id.score) for fused_id, chunk in candidates]

        return [
            RetrievalHit(
                chunk=chunk, score=score, retrievers=fused_id.retrievers, reranked=reranked
            )
            for fused_id, chunk, score in ordered[:top_k]
        ]

    async def retrieve(self, query: str, top_k: int) -> list[RetrievalHit]:
        """Retrieve up to `top_k` hits for `query`. Never raises (except
        `asyncio.CancelledError`): any unexpected failure degrades to `[]`.

        `timeout_s` bounds this whole call, not each stage individually: a
        deadline is computed once, up front, and both the dense-search and
        rerank stages are given whatever budget remains when they start (the
        rerank stage is skipped -- falling back to fused order, `reranked=
        False` -- if the dense stage already used up the whole budget).
        """
        query = query.strip()[:MAX_QUERY_CHARS]
        if not query or top_k < 1:
            return []
        deadline = asyncio.get_running_loop().time() + self._timeout_s
        try:
            return await self._retrieve_inner(query, top_k, deadline)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("knowledge retrieval failed: %s", type(exc).__name__)
            return []


async def create_retriever(
    settings: Settings, client: AsyncQdrantClient | None, tracer: Tracer
) -> KnowledgeRetriever:
    """Build the app's `KnowledgeRetriever` from settings.

    Corpus/BM25 errors propagate (a malformed corpus is a code bug, not a
    runtime condition to degrade gracefully around). The embedder and
    reranker are each loaded best-effort: if either fails to load, the
    retriever still comes up BM25-only rather than the whole app failing.
    """
    docs = load_corpus()
    chunks = chunk_corpus(docs)
    bm25 = build_bm25(chunks)

    embedder: Embedder | None
    try:
        embedder = await load_embedder(settings, tracer)
    except Exception as exc:
        logger.warning("knowledge embedder load failed: %s", type(exc).__name__)
        embedder = None

    reranker: Reranker | None
    try:
        reranker = await load_reranker(settings, tracer)
    except Exception as exc:
        logger.warning("knowledge reranker load failed: %s", type(exc).__name__)
        reranker = None

    return KnowledgeRetriever(
        client=client,
        collection=settings.knowledge_collection,
        embedder=embedder,
        reranker=reranker,
        chunks=chunks,
        bm25=bm25,
        timeout_s=settings.knowledge_timeout_s,
    )
