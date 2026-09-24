"""Lightweight contracts shared by the knowledge RAG pipeline and the graph.

Deliberately depends only on `app.schemas.knowledge` and `typing` -- never on
`qdrant_client`, `fastembed`, or anything else that pulls in a heavy model
SDK. `app.graph.state` (and everything importing it) needs the `Retriever`
shape without paying for those imports at process start, so this module is
the one place that shape lives; `app.knowledge.retrieve` re-exports it for
backward compatibility.
"""

from typing import Final, Protocol, runtime_checkable

from app.schemas.knowledge import RetrievalHit

__all__ = ["DEFAULT_KNOWLEDGE_TOP_K", "Retriever"]

#: Default number of chunks returned by knowledge retrieval per query. The
#: single source of truth for this default -- `Settings.knowledge_top_k`,
#: `GraphContext.knowledge_top_k`, `run_graph`'s `knowledge_top_k` parameter,
#: and the `/chat` route's fallback all use this constant so the default
#: can't drift out of sync between them.
DEFAULT_KNOWLEDGE_TOP_K: Final = 4


@runtime_checkable
class Retriever(Protocol):
    """What the retrieval graph node depends on -- nothing more."""

    async def retrieve(self, query: str, top_k: int) -> list[RetrievalHit]: ...
