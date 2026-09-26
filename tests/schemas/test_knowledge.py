"""Tests for the knowledge-chunk / retrieval-hit Pydantic schemas."""

import pytest
from pydantic import ValidationError

from app.schemas import KnowledgeChunk, RetrievalHit
from app.schemas.knowledge import CorpusDocument


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


def _make_corpus_document(**overrides: object) -> CorpusDocument:
    params: dict[str, object] = {
        "source": "corpus/sliding_window.md",
        "title": "Sliding Window",
        "pattern": "sliding_window",
        "topic": "arrays",
        "aliases": ("window", "subarray"),
        "body": "# Sliding Window\n\nBody text.",
    }
    params.update(overrides)
    return CorpusDocument(**params)  # type: ignore[arg-type]


def test_corpus_document_optional_fields_default_empty() -> None:
    doc = _make_corpus_document()
    assert doc.pattern_family == ""
    assert doc.difficulty == ""
    assert doc.representative_problems == ()
    assert doc.identification_signals == ()


def test_corpus_document_pattern_family_is_slug_normalized() -> None:
    doc = _make_corpus_document(pattern_family="Array Scanning")
    assert doc.pattern_family == "array_scanning"


def test_corpus_document_pattern_family_allows_empty() -> None:
    doc = _make_corpus_document(pattern_family="")
    assert doc.pattern_family == ""


def test_corpus_document_representative_problems_and_identification_signals_roundtrip() -> None:
    doc = _make_corpus_document(
        difficulty="E:5 M:8 H:1",
        representative_problems=("Two Sum | Easy | url1", "3Sum | Medium | url2"),
        identification_signals=("sorted array", "two indices"),
    )
    assert doc.difficulty == "E:5 M:8 H:1"
    assert doc.representative_problems == ("Two Sum | Easy | url1", "3Sum | Medium | url2")
    assert doc.identification_signals == ("sorted array", "two indices")
