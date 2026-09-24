"""Tests for `app.knowledge.ingest.ingest_corpus`/`build_index` against an
in-memory Qdrant instance (`AsyncQdrantClient(location=":memory:")`)."""

import hashlib
from collections.abc import AsyncGenerator, Sequence

import pytest
import pytest_asyncio
from qdrant_client import AsyncQdrantClient

from app.knowledge.ingest import (
    IngestReport,
    chunk_corpus,
    chunk_from_payload,
    ingest_corpus,
    load_corpus,
)
from app.schemas.knowledge import KnowledgeChunk

DIM = 8
COLLECTION = "test_knowledge"


class FakeEmbedder:
    """Deterministic `Embedder` for tests: a vector derived from the sha256
    of the input text, no network/model involved."""

    def __init__(self, dim: int = DIM, *, bad_dim: bool = False) -> None:
        self._dim = dim
        self._bad_dim = bad_dim

    @property
    def dim(self) -> int:
        return self._dim

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        length = self._dim + 1 if self._bad_dim else self._dim
        return [digest[i % len(digest)] / 255.0 for i in range(length)]

    async def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncQdrantClient]:
    qdrant = AsyncQdrantClient(location=":memory:")
    try:
        yield qdrant
    finally:
        await qdrant.close()


@pytest.fixture
def all_chunks() -> list[KnowledgeChunk]:
    return chunk_corpus(load_corpus())


async def test_first_ingest_creates_collection_and_upserts_all(
    client: AsyncQdrantClient, all_chunks: list[KnowledgeChunk]
) -> None:
    report = await ingest_corpus(
        client,
        FakeEmbedder(),
        collection=COLLECTION,
        chunks=all_chunks,
        documents=13,
    )
    assert isinstance(report, IngestReport)
    assert report.created is True
    assert report.chunks == len(all_chunks)
    assert report.upserted == len(all_chunks)
    assert report.deleted_stale == 0
    assert report.points_after == len(all_chunks)


async def test_second_ingest_is_idempotent(
    client: AsyncQdrantClient, all_chunks: list[KnowledgeChunk]
) -> None:
    await ingest_corpus(
        client, FakeEmbedder(), collection=COLLECTION, chunks=all_chunks, documents=13
    )
    report2 = await ingest_corpus(
        client, FakeEmbedder(), collection=COLLECTION, chunks=all_chunks, documents=13
    )
    assert report2.created is False
    assert report2.upserted == len(all_chunks)
    assert report2.deleted_stale == 0
    assert report2.points_after == len(all_chunks)


async def test_ingest_subset_deletes_stale_points(
    client: AsyncQdrantClient, all_chunks: list[KnowledgeChunk]
) -> None:
    await ingest_corpus(
        client, FakeEmbedder(), collection=COLLECTION, chunks=all_chunks, documents=13
    )

    dropped_source = all_chunks[0].source
    subset = [c for c in all_chunks if c.source != dropped_source]
    dropped_count = len(all_chunks) - len(subset)
    assert dropped_count > 0

    report = await ingest_corpus(
        client, FakeEmbedder(), collection=COLLECTION, chunks=subset, documents=12
    )
    assert report.deleted_stale == dropped_count
    assert report.points_after == len(subset)


async def test_stored_payloads_round_trip_to_knowledge_chunks(
    client: AsyncQdrantClient, all_chunks: list[KnowledgeChunk]
) -> None:
    await ingest_corpus(
        client, FakeEmbedder(), collection=COLLECTION, chunks=all_chunks, documents=13
    )
    records, _next_offset = await client.scroll(
        collection_name=COLLECTION, limit=len(all_chunks) + 10, with_payload=True
    )
    assert len(records) == len(all_chunks)
    for record in records:
        assert record.payload is not None
        chunk = chunk_from_payload(record.payload)
        assert chunk.source
        assert chunk.topic
        assert chunk.pattern


async def test_wrong_length_vector_raises_value_error(
    client: AsyncQdrantClient, all_chunks: list[KnowledgeChunk]
) -> None:
    with pytest.raises(ValueError):
        await ingest_corpus(
            client,
            FakeEmbedder(bad_dim=True),
            collection=COLLECTION,
            chunks=all_chunks,
            documents=13,
        )


async def test_recreate_forces_created_true(
    client: AsyncQdrantClient, all_chunks: list[KnowledgeChunk]
) -> None:
    await ingest_corpus(
        client, FakeEmbedder(), collection=COLLECTION, chunks=all_chunks, documents=13
    )
    report = await ingest_corpus(
        client,
        FakeEmbedder(),
        collection=COLLECTION,
        chunks=all_chunks,
        documents=13,
        recreate=True,
    )
    assert report.created is True
    assert report.points_after == len(all_chunks)
