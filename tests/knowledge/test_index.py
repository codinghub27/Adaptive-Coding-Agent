"""Integration tests for `app.knowledge.index` against the real Qdrant instance.

Only ever creates/deletes throwaway `test_ks_*` collections on the shared
Qdrant container -- never touches any other collection.
"""

import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from qdrant_client import AsyncQdrantClient, models

from app.config import get_settings
from app.knowledge.index import DISTANCE, IndexConfigError, ensure_collection

pytestmark = pytest.mark.integration

DIM = 384


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncQdrantClient]:
    settings = get_settings()
    qdrant = AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=(
            settings.qdrant_api_key.get_secret_value()
            if settings.qdrant_api_key is not None
            else None
        ),
        timeout=settings.qdrant_timeout,
        check_compatibility=False,
    )
    try:
        yield qdrant
    finally:
        await qdrant.close()


@pytest_asyncio.fixture
async def collection_name(client: AsyncQdrantClient) -> AsyncGenerator[str]:
    """A unique throwaway collection name, deleted after the test regardless
    of whether the test itself created it."""
    name = f"test_ks_{uuid.uuid4().hex}"
    try:
        yield name
    finally:
        if await client.collection_exists(name):
            await client.delete_collection(name)


async def test_ensure_collection_creates_when_missing(
    client: AsyncQdrantClient, collection_name: str
) -> None:
    created = await ensure_collection(client, collection_name, DIM)
    assert created is True
    assert await client.collection_exists(collection_name)


async def test_ensure_collection_second_call_is_noop(
    client: AsyncQdrantClient, collection_name: str
) -> None:
    assert await ensure_collection(client, collection_name, DIM) is True
    assert await ensure_collection(client, collection_name, DIM) is False


async def test_ensure_collection_dim_mismatch_raises(
    client: AsyncQdrantClient, collection_name: str
) -> None:
    assert await ensure_collection(client, collection_name, DIM) is True
    with pytest.raises(IndexConfigError):
        await ensure_collection(client, collection_name, DIM + 1)


async def test_ensure_collection_recreate_rebuilds_mismatched_collection(
    client: AsyncQdrantClient, collection_name: str
) -> None:
    assert await ensure_collection(client, collection_name, DIM) is True

    created = await ensure_collection(client, collection_name, DIM + 1, recreate=True)
    assert created is True

    info = await client.get_collection(collection_name)
    vectors = info.config.params.vectors
    assert isinstance(vectors, models.VectorParams)
    assert vectors.size == DIM + 1
    assert vectors.distance == DISTANCE
