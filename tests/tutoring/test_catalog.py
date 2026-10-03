"""The misconception catalog and question bank stay anchored and closed (G3, AD-T2)."""

import re
from pathlib import Path

from app.agents.planner import HARD_SKILL
from app.knowledge.ingest import CORPUS_DIR
from app.memory.profile import ALPHA, CONCEPT_ALPHA, CONCEPT_CEILING
from app.tutoring.bank import get_question, pending_for
from app.tutoring.misconceptions import catalog, catalog_ids, is_catalog_id


def _common_mistakes(pattern: str) -> str:
    text = (Path(CORPUS_DIR) / f"{pattern}.md").read_text(encoding="utf-8")
    match = re.search(r"^## Common Mistakes\n(.*?)(?=^## )", text, flags=re.MULTILINE | re.DOTALL)
    assert match is not None, f"{pattern}.md has no Common Mistakes section"
    return match.group(1)


def test_every_catalog_entry_quotes_its_corpus_common_mistakes_section() -> None:
    for item in catalog():
        assert item.corpus_quote in _common_mistakes(item.corpus_pattern), item.id


def test_catalog_ids_are_stable_dotted_slugs_and_unique() -> None:
    ids = [item.id for item in catalog()]
    assert len(ids) == len(set(ids))
    for item_id in ids:
        assert re.fullmatch(r"[a-z_]+\.[a-z_]+", item_id), item_id


def test_free_text_is_never_a_catalog_id() -> None:
    assert not is_catalog_id("off by one")
    assert not is_catalog_id(None)
    assert is_catalog_id("bfs.mark_visited_on_dequeue")


def test_every_misconception_question_exists_in_the_bank() -> None:
    for item in catalog():
        assert get_question(item.question_id) is not None, item.id


def test_bank_misconception_references_are_catalog_ids() -> None:
    for qid in (
        "hashing.two_sum.lookup",
        "hashing.checked_vs_accessed",
        "trees.max_path.return_value",
        "bfs.vs_dfs.shortest",
        "dfs.bridges.parallel_edges",
        "bfs.visited_on_enqueue",
    ):
        check = pending_for(qid)
        assert check is not None
        named = set(check.misconception_ids) | {m for m in check.wrong_answers.values() if m}
        named |= {o.misconception for o in check.options if o.misconception}
        assert named <= catalog_ids(), qid


def test_concept_evidence_is_weaker_than_sandbox_and_capped_below_hard() -> None:
    assert CONCEPT_ALPHA < ALPHA
    assert CONCEPT_CEILING < HARD_SKILL
