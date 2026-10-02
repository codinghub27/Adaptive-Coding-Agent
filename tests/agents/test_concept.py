"""ADAPTIVE-upgrade P3: answers grounded in the corpus, cited only for what they used."""

import json

from app.agents.concept import (
    Reference,
    answer_concept,
    cited,
    concept_references,
    curriculum_references,
    pattern_chunks,
    references_block,
    turn_references,
)
from app.agents.debugger import explain_bug
from app.knowledge.ingest import chunk_corpus, load_corpus
from app.response.corpus_sections import corpus_sections, requested_sections
from app.schemas.input import CodeBlock, StructuredInput
from app.schemas.knowledge import RetrievalHit
from tests.input.fakes import FakeLLMClient

_CHUNKS = chunk_corpus(load_corpus())
_REFS = [
    Reference(label="Trie - Overview", text="A trie..."),
    Reference(label="Trie - X", text="y"),
]
_QUESTION = StructuredInput(source="text", question="what is a trie?")


def _hit(section: str, score: float) -> RetrievalHit:
    chunk = next(c for c in _CHUNKS if c.pattern == "trie" and c.metadata.get("section") == section)
    return RetrievalHit(chunk=chunk, score=score, retrievers=("bm25",), reranked=True)


async def test_citations_are_exactly_the_references_the_model_used() -> None:
    fake = FakeLLMClient(chat_content=json.dumps({"answer": "A trie is...", "used": [2, 9, 2]}))
    answer = await answer_concept(_QUESTION, _REFS, fake)
    assert answer.answer == "A trie is..."
    assert answer.citations == ["Trie - X"]  # 9 was never shown; 2 deduplicated
    assert answer.grounded


async def test_no_references_means_no_model_call_and_no_citations() -> None:
    fake = FakeLLMClient(chat_content="unused")
    answer = await answer_concept(_QUESTION, [], fake)
    assert answer.answer == ""
    assert answer.citations == []
    assert fake.chat_calls == []


async def test_unparseable_output_falls_back_to_the_references_themselves() -> None:
    fake = FakeLLMClient(chat_content="not json")
    answer = await answer_concept(_QUESTION, _REFS, fake)
    assert "A trie..." in answer.answer
    assert answer.citations == ["Trie - Overview", "Trie - X"]


async def test_learner_text_is_delimited_as_untrusted_data() -> None:
    fake = FakeLLMClient(chat_content=json.dumps({"answer": "ok", "used": [1]}))
    await answer_concept(_QUESTION, _REFS, fake)
    user = fake.chat_calls[0][-1].content
    assert "<user_input>\nwhat is a trie?\n</user_input>" in user


def test_noise_and_solution_templates_are_never_references() -> None:
    hits = [_hit("general_template", 5.0), _hit("overview", -9.0), _hit("core_intuition", 1.0)]
    labels = [ref.label for ref in concept_references(hits)]
    assert labels == ["Trie - Core Intuition"]


def test_turn_references_put_the_topic_sections_first_without_duplicates() -> None:
    refs = turn_references([_hit("overview", 2.0)], pattern_chunks("trie"), ("overview",))
    assert [r.label for r in refs] == ["Trie - Overview"]


def test_curriculum_covers_every_pattern_family() -> None:
    families = {doc.pattern_family or doc.pattern for doc in load_corpus()}
    assert len(curriculum_references()) == len(families)


def test_cited_ignores_numbers_that_were_not_shown() -> None:
    assert cited(_REFS, [0, 1, 3]) == ["Trie - Overview"]
    assert references_block([]) == ""


async def test_debug_explanation_cites_only_used_reference_notes() -> None:
    fake = FakeLLMClient(chat_content=json.dumps({"bug_explanation": "off by one", "used": [1]}))
    problem = StructuredInput(
        source="text", question="fix", code=[CodeBlock(content="def f(n): return n")]
    )
    explanation, citations = await explain_bug(
        problem,
        static_findings=[],
        failing_case=None,
        bug_location=None,
        inferred_approach=None,
        llm=fake,
        references=_REFS,
    )
    assert explanation == "off by one"
    assert citations == ["Trie - Overview"]
    assert "<reference_notes>" in fake.chat_calls[0][-1].content


# --- corpus teaching sections --------------------------------------------


def test_requested_sections_are_detected_from_a_fixed_vocabulary() -> None:
    text = "Explain the intuition first, how to recognize it, the complexity, then run tests."
    assert requested_sections(text) == {"intuition", "recognition", "complexity", "tests"}
    assert requested_sections("give full answer") == frozenset()


def test_sections_respect_the_assistance_level_and_are_never_truncated() -> None:
    chunks = pattern_chunks("trees")
    hint_level = corpus_sections(chunks, "hint", frozenset({"intuition", "complexity"}))
    assert [s.kind for s in hint_level] == ["recognition"]
    concept = corpus_sections(chunks, "concept", frozenset({"complexity"}))
    assert [s.kind for s in concept] == ["recognition", "intuition", "complexity"]
    assert all(not s.body.endswith("...") for s in concept)
    assert concept[0].citation == "Trees - When to Recognize It"


def test_problem_complexity_from_the_solver_wins_over_the_pattern_one() -> None:
    sections = corpus_sections(
        pattern_chunks("trees"), "partial", frozenset({"complexity"}), has_problem_complexity=True
    )
    assert "complexity" not in [s.kind for s in sections]


def test_pattern_naming_sections_wait_for_a_confident_topic_or_a_request() -> None:
    """Code review P3: at L0 on a mere retrieval guess, don't name the pattern."""
    chunks = pattern_chunks("trees")
    quiet = corpus_sections(chunks, "concept", frozenset(), reveal_pattern=False)
    assert quiet == []
    asked = corpus_sections(chunks, "concept", frozenset({"recognition"}), reveal_pattern=False)
    assert [s.kind for s in asked] == ["recognition"]


def test_a_section_split_across_chunks_is_never_quoted_in_part() -> None:
    chunks = list(pattern_chunks("trees"))
    first = next(c for c in chunks if c.metadata.get("section") == "when_to_recognize_it")
    second = first.model_copy(update={"id": "x", "metadata": {**first.metadata, "part": "1"}})
    sections = corpus_sections([*chunks, second], "concept", frozenset())
    assert "recognition" not in [s.kind for s in sections]
