"""Tests for the curated DSA corpus and its loader (`app.knowledge.ingest`)."""

import ast
import re
from pathlib import Path

import pytest

from app.knowledge.ingest import (
    CORPUS_DIR,
    EXPECTED_PATTERNS,
    CorpusError,
    load_corpus,
    parse_document,
)
from app.schemas.knowledge import CorpusDocument

EXPECTED_TOPIC_BY_PATTERN: dict[str, str] = {
    "two_pointers": "arrays",
    "sliding_window": "arrays",
    "prefix_sum": "arrays",
    "hashing": "hash_tables",
    "binary_search": "searching",
    "dfs": "graph_traversal",
    "bfs": "graph_traversal",
    "backtracking": "recursion",
    "greedy": "optimization",
    "dynamic_programming": "optimization",
    "graphs": "graphs",
    "trees": "trees",
    "heaps": "heaps",
    "fast_slow_pointers": "linked_list",
    "monotonic_stack": "stacks",
    "stack": "stacks",
    "binary_search_on_answer": "searching",
    "linked_list": "linked_list",
    "union_find": "graphs",
    "topological_sort": "graphs",
    "dijkstra": "graphs",
    "bellman_ford": "graphs",
    "dp_1d": "dynamic_programming",
    "dp_2d": "dynamic_programming",
    "intervals": "intervals",
    "bit_manipulation": "bit_manipulation",
    "trie": "tries",
    "divide_and_conquer": "divide_and_conquer",
    "math_geometry": "math",
    "segment_tree": "range_queries",
}

REQUIRED_SECTIONS = [
    "Overview",
    "When to Recognize It",
    "Core Intuition",
    "Identification Signals",
    "General Template",
    "Complexity",
    "Common Mistakes",
    "When NOT to Use",
    "Variations",
    "Representative Problems",
]

_MINIMAL_VALID = """\
---
title: Sample Pattern
pattern: sample_pattern
topic: sample_topic
aliases: alpha, beta
---

# Sample Pattern

Some body text.
"""


def _valid_front_matter_doc(**overrides: str) -> str:
    fields = {
        "title": "Sample Pattern",
        "pattern": "sample_pattern",
        "topic": "sample_topic",
        "aliases": "alpha, beta",
    }
    fields.update(overrides)
    front = "\n".join(f"{k}: {v}" for k, v in fields.items())
    return f"---\n{front}\n---\n\n# Sample Pattern\n\nSome body text.\n"


# ---------------------------------------------------------------------------
# load_corpus() over the real corpus directory
# ---------------------------------------------------------------------------


def test_load_corpus_returns_exactly_expected_patterns() -> None:
    docs = load_corpus()
    patterns = {doc.pattern for doc in docs}
    assert patterns == EXPECTED_PATTERNS


def test_load_corpus_topics_match_table() -> None:
    docs = {doc.pattern: doc for doc in load_corpus()}
    for pattern, expected_topic in EXPECTED_TOPIC_BY_PATTERN.items():
        assert docs[pattern].topic == expected_topic


def test_load_corpus_bodies_have_h1_and_ordered_sections() -> None:
    docs = load_corpus()
    heading_pattern = re.compile(r"^## (.+)$", flags=re.MULTILINE)
    for doc in docs:
        assert re.search(r"^# ", doc.body, flags=re.MULTILINE), f"{doc.pattern}: no H1"
        found_headings = heading_pattern.findall(doc.body)
        assert found_headings == REQUIRED_SECTIONS, f"{doc.pattern}: {found_headings}"


def test_load_corpus_docs_have_at_least_one_alias() -> None:
    docs = load_corpus()
    for doc in docs:
        assert len(doc.aliases) >= 1, f"{doc.pattern}: no aliases"


def test_sliding_window_covers_shrinking_variable_size_window() -> None:
    docs = {doc.pattern: doc for doc in load_corpus()}
    body_lower = docs["sliding_window"].body.lower()
    assert "variable-size window" in body_lower
    assert "shrink" in body_lower


def test_every_template_section_has_parseable_python_fence() -> None:
    docs = load_corpus()
    fence_pattern = re.compile(r"```python\n(.*?)```", flags=re.DOTALL)
    section_pattern = re.compile(
        r"^## General Template\n(.*?)(?=^## |\Z)", flags=re.MULTILINE | re.DOTALL
    )
    for doc in docs:
        section_match = section_pattern.search(doc.body)
        assert section_match is not None, f"{doc.pattern}: no General Template section"
        fence_match = fence_pattern.search(section_match.group(1))
        assert fence_match is not None, f"{doc.pattern}: no python fence in General Template"
        # Static parse only -- never exec/eval untrusted-shaped content.
        ast.parse(fence_match.group(1))


# ---------------------------------------------------------------------------
# parse_document() error cases
# ---------------------------------------------------------------------------


def test_parse_document_no_front_matter_raises() -> None:
    with pytest.raises(CorpusError):
        parse_document("# Title\n\nBody.\n", source="x.md")


def test_parse_document_unterminated_front_matter_raises() -> None:
    text = "---\ntitle: X\npattern: x\ntopic: y\naliases: a\n\n# Title\n\nBody.\n"
    with pytest.raises(CorpusError):
        parse_document(text, source="x.md")


def test_parse_document_unknown_key_raises() -> None:
    text = _valid_front_matter_doc().replace(
        "aliases: alpha, beta", "aliases: alpha, beta\nbogus: 1"
    )
    with pytest.raises(CorpusError):
        parse_document(text, source="x.md")


def test_parse_document_missing_pattern_raises() -> None:
    text = "---\ntitle: Sample\ntopic: sample_topic\naliases: alpha\n---\n\n# Sample\n\nBody.\n"
    with pytest.raises(CorpusError):
        parse_document(text, source="x.md")


def test_parse_document_empty_body_raises() -> None:
    text = "---\ntitle: X\npattern: x\ntopic: y\naliases: a\n---\n\n   \n"
    with pytest.raises(CorpusError):
        parse_document(text, source="x.md")


def test_parse_document_no_h1_raises() -> None:
    text = "---\ntitle: X\npattern: x\ntopic: y\naliases: a\n---\n\nJust a paragraph, no heading.\n"
    with pytest.raises(CorpusError):
        parse_document(text, source="x.md")


def test_parse_document_valid_minimal_doc_parses() -> None:
    doc = parse_document(_MINIMAL_VALID, source="x.md")
    assert isinstance(doc, CorpusDocument)
    assert doc.title == "Sample Pattern"
    assert doc.pattern == "sample_pattern"
    assert doc.topic == "sample_topic"
    assert doc.aliases == ("alpha", "beta")
    assert doc.body.startswith("# Sample Pattern")


# ---------------------------------------------------------------------------
# load_corpus() error cases against a tmp_path corpus dir
# ---------------------------------------------------------------------------


def test_load_corpus_pattern_stem_mismatch_raises(tmp_path: Path) -> None:
    (tmp_path / "wrong_name.md").write_text(_MINIMAL_VALID, encoding="utf-8")
    with pytest.raises(CorpusError):
        load_corpus(tmp_path)


def test_load_corpus_duplicate_pattern_raises(tmp_path: Path) -> None:
    # The first file matches its own stem; the second declares the same
    # `pattern` value as the first, which is invalid one way or another --
    # either as a stem mismatch on the second file, or (if that check were
    # relaxed) as a genuine duplicate-pattern collision. Either way
    # `load_corpus` must reject the corpus.
    (tmp_path / "dup_pattern.md").write_text(
        _valid_front_matter_doc(pattern="dup_pattern"), encoding="utf-8"
    )
    (tmp_path / "dup_pattern_other.md").write_text(
        _valid_front_matter_doc(pattern="dup_pattern", title="Sample Pattern Two"),
        encoding="utf-8",
    )
    with pytest.raises(CorpusError):
        load_corpus(tmp_path)


def test_load_corpus_uses_real_corpus_dir_by_default() -> None:
    assert CORPUS_DIR.is_dir()
    assert load_corpus() == load_corpus(CORPUS_DIR)


# ---------------------------------------------------------------------------
# optional front-matter keys (pattern_family, difficulty,
# representative_problems, identification_signals)
# ---------------------------------------------------------------------------


def test_doc_without_optional_keys_still_parses() -> None:
    """A doc declaring only the 4 required front-matter keys must still
    parse, with empty defaults for the 4 optional keys (backward
    compatibility with the pre-P3c corpus format)."""
    doc = parse_document(_MINIMAL_VALID, source="x.md")
    assert doc.pattern_family == ""
    assert doc.difficulty == ""
    assert doc.representative_problems == ()
    assert doc.identification_signals == ()


def test_every_corpus_doc_has_retrieval_metadata() -> None:
    """Every doc in the live corpus must populate pattern_family and
    identification_signals, so a future doc cannot silently ship without
    retrieval metadata."""
    for doc in load_corpus():
        assert doc.pattern_family, f"{doc.pattern}: missing pattern_family"
        assert doc.identification_signals, f"{doc.pattern}: missing identification_signals"


def test_parse_document_with_all_optional_keys_parses() -> None:
    text = _valid_front_matter_doc(
        pattern_family="Array Scanning",
        difficulty="E:5 M:8 H:1",
        representative_problems=(
            "Two Sum, Easy | Easy | https://example.com/two-sum;"
            "3Sum, Medium | Medium | https://example.com/3sum"
        ),
        identification_signals="sorted array, two indices, opposite ends",
    )
    doc = parse_document(text, source="x.md")
    assert doc.pattern_family == "array_scanning"
    assert doc.difficulty == "E:5 M:8 H:1"
    assert doc.representative_problems == (
        "Two Sum, Easy | Easy | https://example.com/two-sum",
        "3Sum, Medium | Medium | https://example.com/3sum",
    )
    assert doc.identification_signals == (
        "sorted array",
        "two indices",
        "opposite ends",
    )


def test_parse_document_unknown_key_still_raises_with_optional_keys_present() -> None:
    text = _valid_front_matter_doc(pattern_family="Foo", difficulty="E:1").replace(
        "aliases: alpha, beta", "aliases: alpha, beta\nbogus: 1"
    )
    with pytest.raises(CorpusError):
        parse_document(text, source="x.md")


def test_parse_document_missing_required_key_raises_even_with_optional_keys() -> None:
    text = (
        "---\ntitle: Sample\ntopic: sample_topic\naliases: alpha\n"
        "pattern_family: foo\ndifficulty: E:1\n---\n\n# Sample\n\nBody.\n"
    )
    with pytest.raises(CorpusError):
        parse_document(text, source="x.md")
