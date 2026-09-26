"""Knowledge-base chunk and retrieval-hit schemas.

`KnowledgeChunk` is the unit stored/retrieved by the knowledge RAG pipeline
(corpus text ingested by Phase 5's ingestion step, not yet implemented here).
`topic`/`pattern` are normalized the same way as `LearningEventCreate` so a
chunk's tags line up with the learner-profile tags used elsewhere.
"""

from typing import Literal

from pydantic import Field, field_validator

from app.schemas.base import APIModel
from app.schemas.event import slug_tag

__all__ = ["CorpusDocument", "KnowledgeChunk", "RetrievalHit", "RetrieverName"]


class CorpusDocument(APIModel):
    """A single parsed corpus markdown file (`app/knowledge/corpus/*.md`),
    before chunking/embedding. `topic`/`pattern` are normalized the same way
    as `KnowledgeChunk` so the two line up."""

    source: str = Field(min_length=1)
    title: str = Field(min_length=1)
    pattern: str
    topic: str
    aliases: tuple[str, ...]
    body: str = Field(min_length=1)
    pattern_family: str = ""
    difficulty: str = ""
    representative_problems: tuple[str, ...] = ()
    identification_signals: tuple[str, ...] = ()

    @field_validator("topic", "pattern", mode="before")
    @classmethod
    def _normalize_tag(cls, value: object) -> object:
        if isinstance(value, str):
            return slug_tag(value)
        return value

    @field_validator("topic", "pattern")
    @classmethod
    def _require_nonempty_tag(cls, value: str) -> str:
        if not value:
            raise ValueError("must be non-empty after normalization")
        return value

    @field_validator("pattern_family", mode="before")
    @classmethod
    def _normalize_pattern_family(cls, value: object) -> object:
        if isinstance(value, str):
            return slug_tag(value)
        return value


class KnowledgeChunk(APIModel):
    """A single retrievable unit of the knowledge corpus."""

    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source: str = Field(min_length=1)
    title: str
    heading: str
    topic: str
    pattern: str
    metadata: dict[str, str] = Field(default_factory=dict[str, str])

    @field_validator("topic", "pattern", mode="before")
    @classmethod
    def _normalize_tag(cls, value: object) -> object:
        if isinstance(value, str):
            return slug_tag(value)
        return value

    @field_validator("topic", "pattern")
    @classmethod
    def _require_nonempty_tag(cls, value: str) -> str:
        if not value:
            raise ValueError("must be non-empty after normalization")
        return value


RetrieverName = Literal["dense", "bm25"]


class RetrievalHit(APIModel):
    """A single scored, retrieved chunk, tagged with provenance."""

    chunk: KnowledgeChunk
    score: float
    retrievers: tuple[RetrieverName, ...]
    reranked: bool
