"""Qdrant collection lifecycle for the knowledge-corpus index.

Ingestion and retrieval (later in Phase 5) both depend on the knowledge
collection existing with the expected vector configuration; `ensure_collection`
is the single place that creates or validates it, so every entry point agrees
on what "the knowledge collection" means.
"""

from typing import Final

from qdrant_client import AsyncQdrantClient, models

__all__ = ["DISTANCE", "IndexConfigError", "ensure_collection"]

#: Distance metric the knowledge collection is created with; fastembed's dense
#: models (e.g. bge-small-en-v1.5) are trained/evaluated for cosine similarity.
DISTANCE: Final = models.Distance.COSINE


class IndexConfigError(RuntimeError):
    """Raised when an existing Qdrant collection's vector config doesn't match
    what the knowledge index expects (wrong size, distance, or a named/
    multi-vector config instead of a single unnamed vector)."""


def _describe_vectors_config(
    dim: int,
    distance: "models.Distance",
) -> str:
    return f"size={dim}, distance={distance}"


async def ensure_collection(
    client: AsyncQdrantClient,
    name: str,
    dim: int,
    *,
    recreate: bool = False,
) -> bool:
    """Ensure Qdrant collection `name` exists with a single unnamed vector of
    dimensionality `dim` and `DISTANCE`.

    Returns True if the collection was (re)created, False if it already
    existed with a matching configuration. If `recreate` is True and the
    collection already exists, it is deleted and recreated unconditionally.
    If it exists but its vector configuration doesn't match, raises
    `IndexConfigError` rather than silently reusing a mismatched collection.
    """
    exists = await client.collection_exists(name)

    if exists and recreate:
        await client.delete_collection(name)
        exists = False

    if not exists:
        await client.create_collection(
            collection_name=name,
            vectors_config=models.VectorParams(size=dim, distance=DISTANCE),
        )
        return True

    info = await client.get_collection(name)
    vectors = info.config.params.vectors
    if not isinstance(vectors, models.VectorParams):
        raise IndexConfigError(
            f"collection {name!r} has an incompatible vectors config "
            f"(expected a single unnamed vector: {_describe_vectors_config(dim, DISTANCE)}, "
            f"got: {vectors!r})"
        )
    if vectors.size != dim or vectors.distance != DISTANCE:
        raise IndexConfigError(
            f"collection {name!r} vector config mismatch: "
            f"expected {_describe_vectors_config(dim, DISTANCE)}, "
            f"got {_describe_vectors_config(vectors.size, vectors.distance)}"
        )

    return False
