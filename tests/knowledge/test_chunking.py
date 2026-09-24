"""Tests for `app.knowledge.ingest` chunking: `chunk_document`/`chunk_corpus`
and the payload round-trip helpers."""

import pytest

from app.knowledge.ingest import (
    MAX_CHUNK_CHARS,
    CorpusError,
    chunk_corpus,
    chunk_document,
    chunk_from_payload,
    chunk_to_payload,
    load_corpus,
)
from app.schemas.knowledge import CorpusDocument

# ---------------------------------------------------------------------------
# real corpus
# ---------------------------------------------------------------------------


def test_chunk_corpus_every_chunk_has_required_fields() -> None:
    docs = load_corpus()
    chunks = chunk_corpus(docs)
    assert chunks
    for chunk in chunks:
        assert chunk.source
        assert chunk.topic
        assert chunk.pattern
        assert chunk.title
        assert chunk.heading
        prefix = f"{chunk.title} — {chunk.heading}\n\n"
        if len(chunk.text) > MAX_CHUNK_CHARS:
            # Only a single oversize paragraph is allowed to exceed the
            # limit -- i.e. no blank-line paragraph separator in the body.
            assert "\n\n" not in chunk.text.removeprefix(prefix)
        assert chunk.text.startswith(f"{chunk.title} — {chunk.heading}")


def test_chunk_corpus_ids_stable_and_unique() -> None:
    docs = load_corpus()
    chunks_a = chunk_corpus(docs)
    chunks_b = chunk_corpus(docs)
    ids_a = [c.id for c in chunks_a]
    ids_b = [c.id for c in chunks_b]
    assert ids_a == ids_b
    assert len(ids_a) == len(set(ids_a))


def test_chunk_corpus_each_doc_yields_at_least_six_chunks() -> None:
    docs = load_corpus()
    by_source: dict[str, int] = {}
    for chunk in chunk_corpus(docs):
        by_source[chunk.source] = by_source.get(chunk.source, 0) + 1
    assert len(by_source) == len(docs)
    for source, count in by_source.items():
        assert count >= 6, f"{source}: only {count} chunks"


def test_chunk_corpus_no_duplicate_ids_raises_corpus_error() -> None:
    docs = load_corpus()
    doc = docs[0]
    with_dup = list(docs) + [doc]
    # Same doc chunked twice yields duplicate ids -> CorpusError.
    with pytest.raises(CorpusError):
        chunk_corpus(with_dup)


def test_chunk_corpus_doc_with_no_sections_raises_corpus_error() -> None:
    """A doc whose body has only a top-level `# ` heading (no `## ` sections)
    yields zero chunks from `chunk_document` -- `chunk_corpus` must raise
    rather than silently dropping the document from the corpus."""
    doc = CorpusDocument(
        source="h1_only.md",
        title="H1 Only",
        pattern="h1_only",
        topic="h1_only",
        aliases=(),
        body="# H1 Only\n\nJust a paragraph, no '## ' section heading anywhere.\n",
    )
    assert chunk_document(doc) == []
    with pytest.raises(CorpusError, match="h1_only.md"):
        chunk_corpus([doc])


def test_payload_round_trip_real_corpus() -> None:
    chunks = chunk_corpus(load_corpus())
    for chunk in chunks:
        assert chunk_from_payload(chunk_to_payload(chunk)) == chunk


# ---------------------------------------------------------------------------
# synthetic long-section document
# ---------------------------------------------------------------------------

_LONG_PARAGRAPH = " ".join(f"word{i}" for i in range(30))  # ~ 190 chars


def _synthetic_long_doc() -> CorpusDocument:
    paragraphs = [f"Paragraph {i}. {_LONG_PARAGRAPH}" for i in range(8)]
    section_body = "\n\n".join(paragraphs)
    body = f"# Synthetic\n\n## Long Section\n{section_body}\n\n## Short Section\nShort body.\n"
    return CorpusDocument(
        source="synthetic.md",
        title="Synthetic",
        pattern="synthetic_pattern",
        topic="synthetic_topic",
        aliases=("synth",),
        body=body,
    )


def test_long_section_splits_into_multiple_overlapping_parts() -> None:
    doc = _synthetic_long_doc()
    chunks = chunk_document(doc)

    long_chunks = [c for c in chunks if c.heading == "Long Section"]
    short_chunks = [c for c in chunks if c.heading == "Short Section"]

    assert len(long_chunks) > 1
    assert len(short_chunks) == 1

    for chunk in long_chunks:
        assert len(chunk.text) <= MAX_CHUNK_CHARS

    # One-paragraph overlap: the last paragraph body of part N appears in
    # part N+1 too.
    for prev_chunk, next_chunk in zip(long_chunks, long_chunks[1:], strict=False):
        prev_last_paragraph = prev_chunk.text.strip().split("\n\n")[-1]
        assert prev_last_paragraph in next_chunk.text


def test_fence_containing_heading_marker_is_not_split() -> None:
    body = (
        "# Fence Doc\n\n"
        "## Template\n"
        "Intro text.\n\n"
        "```python\n"
        "# comment\n"
        "## not a heading, just a comment inside a fence\n"
        "x = 1\n"
        "```\n\n"
        "## Complexity\n"
        "O(n).\n"
    )
    doc = CorpusDocument(
        source="fence.md",
        title="Fence Doc",
        pattern="fence_pattern",
        topic="fence_topic",
        aliases=("fence",),
        body=body,
    )
    chunks = chunk_document(doc)
    headings = [c.heading for c in chunks]
    assert headings == ["Template", "Complexity"]

    template_chunk = chunks[0]
    assert "## not a heading, just a comment inside a fence" in template_chunk.text
    # The fence itself was never split mid-block.
    assert template_chunk.text.count("```") == 2


def test_fence_never_split_mid_block_when_section_is_long() -> None:
    """A long section whose fenced code block alone would exceed
    MAX_CHUNK_CHARS becomes its own (oversize) part, never split mid-fence."""
    fence_lines = "\n".join(f"    line_{i} = {i}" for i in range(200))
    body = (
        "# Big Fence Doc\n\n"
        "## Template\n"
        f"```python\n{fence_lines}\n```\n\n"
        "Trailing paragraph after the fence.\n\n"
        "## Complexity\nO(n).\n"
    )
    doc = CorpusDocument(
        source="bigfence.md",
        title="Big Fence Doc",
        pattern="bigfence_pattern",
        topic="bigfence_topic",
        aliases=("bigfence",),
        body=body,
    )
    chunks = chunk_document(doc)
    template_chunks = [c for c in chunks if c.heading == "Template"]
    assert len(template_chunks) >= 1
    for chunk in template_chunks:
        # Each part has balanced fence markers -- never a half-open fence.
        assert chunk.text.count("```") % 2 == 0
