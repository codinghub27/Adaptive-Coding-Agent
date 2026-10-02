"""ADAPTIVE-upgrade P2: topic resolution from the corpus's own recognition vocabulary,
and non-problem requests that must never enter the hint ladder."""

from app.agents.planner import analyze_problem
from app.graph.nodes import problem_key, resolve_problem_relation
from app.input.intent import rule_intent
from app.knowledge.ingest import chunk_corpus, load_corpus
from app.schemas.input import ActiveProblem, CodeBlock, StructuredInput
from app.schemas.intent import Intent
from app.schemas.knowledge import KnowledgeChunk, RetrievalHit
from app.schemas.profile import LearnerProfileView

_CHUNKS = chunk_corpus(load_corpus())
_NL = "\n"


def _chunk(pattern: str) -> KnowledgeChunk:
    return next(c for c in _CHUNKS if c.pattern == pattern)


def _hit(pattern: str, score: float) -> RetrievalHit:
    return RetrievalHit(chunk=_chunk(pattern), score=score, retrievers=("bm25",), reranked=True)


def _analyze(problem: str, hits: list[RetrievalHit], code: str | None = None) -> str | None:
    inp = StructuredInput(
        source="text",
        problem=problem,
        code=[CodeBlock(content=code)] if code else [],
    )
    return analyze_problem(inp, LearnerProfileView.empty(), context=hits).topic


def test_a_named_representative_problem_decides_the_topic() -> None:
    """T1/T7: the statement's heading is the problem's own title."""
    problem = "Problem: Word Ladder" + _NL + "Return the number of words..."
    assert _analyze(problem, [_hit("dfs", 3.0)]) == "bfs"


def test_an_ambiguous_title_counts_only_among_surfaced_docs() -> None:
    """ "Two Sum" is listed by hashing AND prefix_sum: no surfaced doc -> no title verdict."""
    problem = "Two Sum" + _NL + "find two indices"
    assert _analyze(problem, [_hit("two_pointers", 1.0)]) == "two_pointers"
    assert _analyze(problem, [_hit("two_pointers", 1.0), _hit("hashing", 0.5)]) == "hashing"


def test_a_title_inside_a_sentence_is_not_a_title() -> None:
    """Code review P2: "Binary Search" inside "binary search tree" must not win."""
    assert _analyze("How do I insert into a binary search tree?", [_hit("trees", 2.0)]) == "trees"


def test_identification_signals_break_a_sibling_tie() -> None:
    """binary_search vs two_pointers within the sibling margin: the O(log n) cue decides."""
    hits = [_hit("two_pointers", -0.2), _hit("binary_search", -1.2)]
    problem = "Given a sorted array, find the first and last position of target in O(log n) time."
    assert _analyze(problem, hits) == "binary_search"


def test_a_sibling_far_below_the_top_hit_is_not_considered() -> None:
    hits = [_hit("two_pointers", 4.0), _hit("binary_search", 0.0)]
    assert _analyze("sorted array, O(log n) time", hits) == "two_pointers"


def test_code_shape_separates_dfs_from_bfs() -> None:
    stack_code = _NL.join(
        ["def f(g, s):", "    stack = [s]", "    while stack:", "        n = stack.pop()"]
    )
    queue_code = _NL.join(
        ["from collections import deque", "def f(g, s):", "    q = deque([s])", "    q.popleft()"]
    )
    assert (
        _analyze("explore the graph", [_hit("bfs", -3.6), _hit("dfs", -5.1)], stack_code) == "dfs"
    )
    assert (
        _analyze("explore the graph", [_hit("dfs", -3.6), _hit("bfs", -5.1)], queue_code) == "bfs"
    )


def test_a_deque_popped_from_the_right_is_a_dfs_stack() -> None:
    code = _NL.join(
        [
            "from collections import deque",
            "def f(g, s):",
            "    stack = deque([s])",
            "    stack.pop()",
        ]
    )
    assert _analyze("explore the graph", [_hit("bfs", -3.6), _hit("dfs", -5.1)], code) == "dfs"


def test_greetings_are_low_confidence_general_guidance() -> None:
    for text in ("hi", "Hello!", "thanks", "ok", "ok thanks"):
        result = rule_intent(StructuredInput(source="text", question=text))
        assert result is not None
        assert result.intent is Intent.GENERAL_GUIDANCE
        assert result.low_confidence


def test_general_guidance_never_inherits_the_active_problem() -> None:
    """T12: a study-plan request after a graph problem must not climb its ladder."""
    stored = StructuredInput(source="text", problem="Return all critical connections.")
    key = problem_key(stored)
    assert key is not None
    active = ActiveProblem(problem=stored, key=key, topic="dfs")
    plan = StructuredInput(source="text", question="give me a 4 month placement plan")
    assert resolve_problem_relation(plan, active, Intent.GENERAL_GUIDANCE) == ("none", None)
    assert resolve_problem_relation(plan, active, Intent.DSA_HINT)[0] == "followup"


async def test_a_greeting_gets_an_invitation_not_a_key_error() -> None:
    """Code review P2: `clarify` looked GENERAL_GUIDANCE up in a phrase map that lacked it."""
    from typing import Any, cast

    from app.graph.nodes import clarify
    from app.graph.state import AgentState, RawInput
    from app.schemas.intent import IntentResult

    state = AgentState(
        input=RawInput(text="hi"),
        structured_input=StructuredInput(source="text", question="hi"),
        intent=IntentResult(intent=Intent.GENERAL_GUIDANCE, confidence=0.3, source="rule"),
    )
    update = await clarify(state, cast(Any, None))
    outcome = update.get("agent_output")
    assert outcome is not None
    assert outcome.text.startswith("Hi!")
