"""Tests for the knowledge-chunk / retrieval-hit Pydantic schemas."""

import pytest
from pydantic import ValidationError

from app.schemas import KnowledgeChunk, RetrievalHit


def _make_chunk(**overrides: object) -> KnowledgeChunk:
    params: dict[str, object] = {
        "id": "11111111-1111-1111-1111-111111111111",
        "text": "A sliding window keeps a contiguous range of indices.",
        "source": "corpus/sliding_window.md",
        "title": "Sliding Window",
        "heading": "Overview",
        "topic": "Arrays",
        "pattern": "Sliding Window",
    }
    params.update(overrides)
    return KnowledgeChunk(**params)  # type: ignore[arg-type]


def test_valid_chunk_round_trips() -> None:
    chunk = _make_chunk()
    assert chunk.text.startswith("A sliding window")
    assert chunk.metadata == {}


def test_topic_and_pattern_are_slug_normalized() -> None:
    chunk = _make_chunk(topic="Two Pointers", pattern="Sliding Window")
    assert chunk.topic == "two_pointers"
    assert chunk.pattern == "sliding_window"


def test_blank_topic_rejected() -> None:
    with pytest.raises(ValidationError):
        _make_chunk(topic="   ")


def test_blank_pattern_rejected() -> None:
    with pytest.raises(ValidationError):
        _make_chunk(pattern="   ")


def test_blank_text_rejected() -> None:
    with pytest.raises(ValidationError):
        _make_chunk(text="")


def test_chunk_is_frozen() -> None:
    chunk = _make_chunk()
    with pytest.raises(ValidationError):
        chunk.title = "New Title"  # type: ignore[misc]


def test_chunk_rejects_extra_field() -> None:
    with pytest.raises(ValidationError):
        _make_chunk(bogus="nope")


def test_retrieval_hit_valid() -> None:
    chunk = _make_chunk()
    hit = RetrievalHit(chunk=chunk, score=0.87, retrievers=("dense", "bm25"), reranked=True)
    assert hit.retrievers == ("dense", "bm25")
    assert hit.reranked is True


def test_retrieval_hit_is_frozen() -> None:
    chunk = _make_chunk()
    hit = RetrievalHit(chunk=chunk, score=0.5, retrievers=("dense",), reranked=False)
    with pytest.raises(ValidationError):
        hit.score = 0.9  # type: ignore[misc]


def test_retrieval_hit_rejects_extra_field() -> None:
    chunk = _make_chunk()
    with pytest.raises(ValidationError):
        RetrievalHit(
            chunk=chunk,
            score=0.5,
            retrievers=("dense",),
            reranked=False,
            bogus="nope",  # type: ignore[call-arg]
        )
