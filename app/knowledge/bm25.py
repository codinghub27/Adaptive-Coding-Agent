"""In-house BM25 sparse-retrieval index.

A small, dependency-free BM25 implementation (Okapi BM25) used as the sparse
half of the knowledge RAG hybrid retriever (dense embeddings + BM25 -> RRF ->
rerank, wired up later in Phase 5). Kept in-house rather than pulling in a
BM25 package since the corpus is small (a few dozen documents) and an
in-house index avoids an extra dependency for something this simple.
"""

import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

__all__ = ["BM25Index", "tokenize"]

_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(r"[a-z0-9]+")

_STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "a",
        "an",
        "the",
        "of",
        "to",
        "in",
        "is",
        "and",
        "or",
        "for",
        "on",
        "with",
        "by",
        "it",
        "as",
        "at",
        "be",
        "this",
        "that",
        "how",
        "what",
        "when",
        "do",
        "i",
        "my",
    }
)


def tokenize(text: str) -> list[str]:
    """Lowercase, split on runs of `[a-z0-9]`, drop stopwords and single chars."""
    candidates = _TOKEN_PATTERN.findall(text.lower())
    return [token for token in candidates if len(token) >= 2 and token not in _STOPWORDS]


@dataclass(frozen=True)
class BM25Index:
    """An Okapi BM25 index over a fixed corpus of (doc_id, text) pairs."""

    k1: float
    b: float
    _doc_term_freqs: dict[str, Counter[str]] = field(repr=False)
    _doc_lengths: dict[str, int] = field(repr=False)
    _doc_freq: dict[str, int] = field(repr=False)
    _avgdl: float = field(repr=False)
    _doc_ids: tuple[str, ...] = field(repr=False)

    @classmethod
    def build(
        cls, docs: Sequence[tuple[str, str]], *, k1: float = 1.5, b: float = 0.75
    ) -> "BM25Index":
        doc_term_freqs: dict[str, Counter[str]] = {}
        doc_lengths: dict[str, int] = {}
        doc_freq: dict[str, int] = {}
        doc_ids: list[str] = []

        for doc_id, text in docs:
            tokens = tokenize(text)
            counts = Counter(tokens)
            doc_term_freqs[doc_id] = counts
            doc_lengths[doc_id] = len(tokens)
            doc_ids.append(doc_id)
            for term in counts:
                doc_freq[term] = doc_freq.get(term, 0) + 1

        avgdl = (sum(doc_lengths.values()) / len(doc_ids)) if doc_ids else 0.0

        return cls(
            k1=k1,
            b=b,
            _doc_term_freqs=doc_term_freqs,
            _doc_lengths=doc_lengths,
            _doc_freq=doc_freq,
            _avgdl=avgdl,
            _doc_ids=tuple(doc_ids),
        )

    def _idf(self, term: str) -> float:
        n = len(self._doc_ids)
        df = self._doc_freq.get(term, 0)
        return math.log((n - df + 0.5) / (df + 0.5) + 1)

    def search(self, query: str, k: int) -> list[tuple[str, float]]:
        if not self._doc_ids:
            return []
        # Deduped, order-preserving: a repeated query token (e.g. a topic
        # word echoed in both the plan's topic and the question) must not
        # count multiple times -- BM25's per-term contribution is already
        # frequency-aware on the *document* side, so repeating a *query*
        # token would just linearly over-weight that one term rather than
        # reflect anything about relevance.
        query_tokens = list(dict.fromkeys(tokenize(query)))
        if not query_tokens:
            return []

        scores: dict[str, float] = {}
        for doc_id in self._doc_ids:
            term_freqs = self._doc_term_freqs[doc_id]
            doc_length = self._doc_lengths[doc_id]
            score = 0.0
            for term in query_tokens:
                freq = term_freqs.get(term, 0)
                if freq == 0:
                    continue
                idf = self._idf(term)
                denom = freq + self.k1 * (1 - self.b + self.b * doc_length / self._avgdl)
                score += idf * (freq * (self.k1 + 1)) / denom
            if score > 0:
                scores[doc_id] = score

        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return ranked[:k]

    def __len__(self) -> int:
        return len(self._doc_ids)
