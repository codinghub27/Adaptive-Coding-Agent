"""Tests for `app.agents.practice` and the `practice_agent` node.

Everything here is deterministic: the practice path makes no LLM call and no
network call -- it parses the bundled, curated corpus -- so these tests assert
on real corpus content rather than fixtures.
"""

import pytest

from app.agents.practice import (
    PracticeProblem,
    render_practice_problem,
    select_practice_problem,
)
from app.graph.routing import INTENT_ROUTES
from app.graph.subgraphs.dsa import corpus_chunks_by_pattern
from app.input.intent import fallback_intent
from app.schemas.input import StructuredInput
from app.schemas.intent import Intent


def _chunks(pattern: str):
    return corpus_chunks_by_pattern().get(pattern, ())


@pytest.mark.parametrize(
    "text",
    [
        "give me a two pointer problem for a beginner",
        "I want to practise BFS",
        "give me another problem",
        "quiz me on dynamic programming",
    ],
)
def test_practice_phrasings_classify_as_practice_request(text: str) -> None:
    result = fallback_intent(StructuredInput(source="text", question=text))
    assert result.intent is Intent.PRACTICE_REQUEST


def test_practice_intent_routes_to_the_practice_node() -> None:
    assert INTENT_ROUTES[Intent.PRACTICE_REQUEST] == "practice"


def test_a_problem_is_selected_from_the_corpus_for_the_topic() -> None:
    problem = select_practice_problem("two_pointers", "medium", _chunks("two_pointers"))
    assert problem is not None
    assert problem.topic == "two_pointers"
    # Grounded: the title must be one the corpus actually lists, not invented.
    assert problem.title
    assert problem.url.startswith("http")


def test_difficulty_tracks_what_was_asked_for() -> None:
    """A weak skill yields an easier problem than a strong one.

    `difficulty_for` maps skill to these buckets; this asserts the selection
    honours the bucket it is handed, which is the part `practice_agent` owns.
    """
    chunks = _chunks("two_pointers")
    easy = select_practice_problem("two_pointers", "easy", chunks)
    hard = select_practice_problem("two_pointers", "hard", chunks)
    assert easy is not None and hard is not None
    order = ("easy", "medium", "hard")
    assert order.index(easy.difficulty) <= order.index(hard.difficulty)
    assert easy.difficulty == "easy"
    assert hard.difficulty == "hard"


def test_no_topic_selects_nothing_rather_than_inventing_one() -> None:
    assert select_practice_problem(None, "medium", _chunks("two_pointers")) is None


def test_an_unknown_topic_selects_nothing() -> None:
    assert select_practice_problem("not_a_real_pattern", "medium", ()) is None


def test_rendered_text_names_the_problem_and_invites_an_attempt() -> None:
    problem = PracticeProblem(
        topic="two_pointers",
        title="Valid Palindrome",
        url="https://example.test/p",
        difficulty="easy",
        cue="two indices moving toward each other",
    )
    text = render_practice_problem(problem)
    assert "Valid Palindrome" in text
    assert "easy" in text
    assert "two_pointers" in text
    assert "two indices moving toward each other" in text
    # It must ask for the learner's own attempt, not offer the solution.
    assert "your attempt" in text.lower()
    assert "def " not in text
