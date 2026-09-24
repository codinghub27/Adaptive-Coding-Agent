"""Tests for `app.knowledge.bm25`."""

import math

from app.knowledge.bm25 import BM25Index, tokenize
from app.knowledge.ingest import bm25_text, chunk_corpus, load_corpus


def test_tokenize_lowercases_and_splits_snake_case_and_hyphen() -> None:
    assert tokenize("Sliding_Window shrink-left") == ["sliding", "window", "shrink", "left"]


def test_tokenize_drops_stopwords_and_single_chars() -> None:
    tokens = tokenize("How do I use a window for this")
    assert "how" not in tokens
    assert "do" not in tokens
    assert "i" not in tokens
    assert "a" not in tokens
    assert "for" not in tokens
    assert "this" not in tokens
    assert "use" in tokens
    assert "window" in tokens


def test_tokenize_case_insensitive() -> None:
    assert tokenize("Window") == tokenize("WINDOW") == tokenize("window")


def test_bm25_hand_computed_single_term_score() -> None:
    docs = [
        ("a", "window window window"),
        ("b", "window array"),
        ("c", "array array array"),
    ]
    index = BM25Index.build(docs, k1=1.5, b=0.75)

    n = 3
    df = 2  # "window" appears in docs a, b
    idf = math.log((n - df + 0.5) / (df + 0.5) + 1)
    avgdl = (3 + 2 + 3) / 3

    def bm25_score(freq: int, doc_len: int) -> float:
        denom = freq + 1.5 * (1 - 0.75 + 0.75 * doc_len / avgdl)
        return idf * (freq * (1.5 + 1)) / denom

    results = dict(index.search("window", k=10))
    assert math.isclose(results["a"], bm25_score(3, 3), rel_tol=1e-9)
    assert math.isclose(results["b"], bm25_score(1, 2), rel_tol=1e-9)
    assert "c" not in results


def test_bm25_ranking_over_real_corpus_favors_sliding_window() -> None:
    chunks = chunk_corpus(load_corpus())
    index = BM25Index.build([(c.id, bm25_text(c)) for c in chunks])
    chunk_by_id = {c.id: c for c in chunks}

    results = index.search("sliding window shrink", k=5)
    assert results
    top_chunk = chunk_by_id[results[0][0]]
    assert top_chunk.pattern == "sliding_window"


def test_bm25_empty_query_returns_empty() -> None:
    index = BM25Index.build([("a", "window array")])
    assert index.search("", k=5) == []
    assert index.search("the of and", k=5) == []


def test_bm25_empty_index_returns_empty() -> None:
    index = BM25Index.build([])
    assert index.search("window", k=5) == []
    assert len(index) == 0


def test_bm25_len() -> None:
    index = BM25Index.build([("a", "x"), ("b", "y")])
    assert len(index) == 2


def test_bm25_tie_break_is_deterministic_by_doc_id() -> None:
    docs = [("z", "window"), ("a", "window"), ("m", "window")]
    index = BM25Index.build(docs)
    results = index.search("window", k=10)
    ids = [doc_id for doc_id, _score in results]
    assert ids == sorted(ids)


def test_bm25_repeated_query_token_does_not_over_weight_score() -> None:
    """A query token repeated (e.g. a topic word echoed in both the plan's
    topic and the question) must not count multiple times -- `search` dedupes
    query tokens before scoring, order-preserving."""
    docs = [("a", "window array"), ("b", "array array array")]
    index = BM25Index.build(docs)

    once = dict(index.search("window", k=10))
    repeated = dict(index.search("window window window", k=10))

    assert once == repeated
