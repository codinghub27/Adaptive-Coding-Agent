"""Tests for `app.graph.nodes.retrieve_knowledge`/`should_retrieve`/
`build_retrieval_query`, the graph topology change it introduces, and the
`/chat` + lifespan wiring that feeds it a real `Retriever`.

`FakeRetriever` stands in for `app.knowledge.retrieve.Retriever` throughout:
no embedder/reranker/Qdrant is ever touched here.
"""

import asyncio
from collections.abc import Callable, Sequence

import pytest
from langgraph.runtime import Runtime  # pyright: ignore[reportMissingTypeStubs]
from starlette.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.graph.build import get_graph, run_graph
from app.graph.nodes import build_retrieval_query, retrieve_knowledge, should_retrieve
from app.graph.routing import ROUTE_NODES
from app.graph.state import AgentState, GraphContext, RawInput
from app.knowledge.ingest import CorpusError, chunk_corpus, load_corpus
from app.knowledge.retrieve import Retriever
from app.main import create_app
from app.schemas.input import StructuredInput
from app.schemas.intent import Intent, IntentResult
from app.schemas.knowledge import KnowledgeChunk, RetrievalHit
from app.schemas.plan import TeachingPlan
from tests.input.fakes import FakeLLMClient

MakeSettings = Callable[..., Settings]

_CHUNKS = chunk_corpus(load_corpus())
_HIT_1 = RetrievalHit(chunk=_CHUNKS[0], score=0.9, retrievers=("bm25",), reranked=False)
_HIT_2 = RetrievalHit(chunk=_CHUNKS[1], score=0.5, retrievers=("dense", "bm25"), reranked=True)

_DEBUG_RULE_TEXT = (
    "```python\n"
    "def get_item(items, idx):\n"
    "    return items[idx]\n"
    "```\n"
    "\n"
    "IndexError: list index out of range\n"
)


class FakeRetriever:
    """Records `(query, top_k)` calls; returns scripted hits (or raises/sleeps)."""

    def __init__(
        self,
        *,
        hits: Sequence[RetrievalHit] = (),
        raise_error: bool = False,
        sleep_s: float = 0.0,
    ) -> None:
        self.hits = list(hits)
        self.raise_error = raise_error
        self.sleep_s = sleep_s
        self.calls: list[tuple[str, int]] = []

    async def retrieve(self, query: str, top_k: int) -> list[RetrievalHit]:
        self.calls.append((query, top_k))
        if self.sleep_s:
            await asyncio.sleep(self.sleep_s)
        if self.raise_error:
            raise RuntimeError("secret-marker")
        return list(self.hits)


def _runtime(
    *, retriever: Retriever | None = None, knowledge_top_k: int = 4
) -> Runtime[GraphContext]:
    return Runtime(
        context=GraphContext(
            llm=FakeLLMClient(), retriever=retriever, knowledge_top_k=knowledge_top_k
        )
    )


def _plan(*, topic: str | None = "arrays", strategy: str = "socratic_hints") -> TeachingPlan:
    return TeachingPlan(
        difficulty="easy",  # type: ignore[arg-type]
        assistance_level="hint",
        solution_strategy=strategy,  # type: ignore[arg-type]
        topic=topic,
        skill_level=0.5,
    )


def _intent(intent: Intent, confidence: float = 0.9) -> IntentResult:
    return IntentResult(intent=intent, confidence=confidence, source="rule")


def _structured(
    *, question: str | None = "how do I do this", error: str | None = None
) -> StructuredInput:
    return StructuredInput(source="text", question=question, error=error)


def _state(
    *,
    structured_input: StructuredInput | None = None,
    intent: IntentResult | None = None,
    plan: TeachingPlan | None = None,
) -> AgentState:
    return AgentState(
        input=RawInput(text="hi"),
        structured_input=structured_input,
        intent=intent,
        plan=plan,
    )


# ---------------------------------------------------------------------------
# should_retrieve
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "intent_value",
    [
        Intent.DSA_SOLVE,
        Intent.DSA_HINT,
        Intent.APPROACH_DISCUSSION,
        Intent.CONCEPT_EXPLANATION,
    ],
)
def test_should_retrieve_true_for_dsa_and_explain_intents(intent_value: Intent) -> None:
    state = _state(structured_input=_structured(), intent=_intent(intent_value))
    assert should_retrieve(state) is True


def test_should_retrieve_false_for_code_debug_with_error() -> None:
    state = _state(
        structured_input=_structured(error="IndexError: boom"),
        intent=_intent(Intent.CODE_DEBUG),
    )
    assert should_retrieve(state) is False


def test_should_retrieve_true_for_code_debug_without_error() -> None:
    state = _state(
        structured_input=_structured(error=None),
        intent=_intent(Intent.CODE_DEBUG),
    )
    assert should_retrieve(state) is True


def test_should_retrieve_true_for_error_explanation_with_error() -> None:
    state = _state(
        structured_input=_structured(error="KeyError: 'x'"),
        intent=_intent(Intent.ERROR_EXPLANATION),
    )
    assert should_retrieve(state) is True


def test_should_retrieve_false_for_low_confidence_intent() -> None:
    state = _state(
        structured_input=_structured(),
        intent=_intent(Intent.DSA_SOLVE, confidence=0.2),
    )
    assert should_retrieve(state) is False


def test_should_retrieve_false_for_none_intent() -> None:
    state = _state(structured_input=_structured(), intent=None)
    assert should_retrieve(state) is False


def test_should_retrieve_ignores_plan_even_when_clarify() -> None:
    """`should_retrieve` runs before `plan_teaching` in the graph now, so a
    plan does not exist yet at this point in a real run -- and even when one
    is present on the state object anyway (as in this synthetic test), it
    must not be consulted. This replaces the old
    `test_should_retrieve_false_for_clarify_plan`: that behaviour (gating on
    the plan already having chosen "clarify") could never fire before the
    plan exists, so it is intentionally dropped rather than reproduced -- see
    the `should_retrieve` docstring."""
    state = _state(
        structured_input=_structured(),
        intent=_intent(Intent.DSA_SOLVE),
        plan=_plan(strategy="clarify"),
    )
    assert should_retrieve(state) is True


def test_should_retrieve_false_for_empty_input() -> None:
    state = _state(
        structured_input=StructuredInput(source="text"),
        intent=_intent(Intent.DSA_SOLVE),
    )
    assert should_retrieve(state) is False


# ---------------------------------------------------------------------------
# build_retrieval_query
# ---------------------------------------------------------------------------


def test_build_retrieval_query_excludes_code_and_error() -> None:
    structured = StructuredInput(
        source="text",
        question="how do sliding windows work",
        problem="Find the max sum subarray",
        error="CODE-MARKER: IndexError boom",
        code=[],
    )
    state = _state(structured_input=structured)

    query = build_retrieval_query(state)

    assert "CODE-MARKER" not in query
    assert "how do sliding windows work" in query
    assert "Find the max sum subarray" in query


def test_build_retrieval_query_ignores_plan() -> None:
    """`build_retrieval_query` runs before `plan_teaching` now, so a plan
    doesn't exist yet at this point in a real run -- and even when one is
    present on the state object anyway (as here), its topic must not be
    joined into the query (this was always circular: the plan's topic is
    itself meant to be inferred from what retrieval returns)."""
    structured = StructuredInput(source="text", question="how do sliding windows work")
    state = _state(structured_input=structured, plan=_plan(topic="two_pointers"))

    query = build_retrieval_query(state)

    assert "two pointers" not in query
    assert query == "how do sliding windows work"


def test_build_retrieval_query_empty_when_nothing_present() -> None:
    state = _state(structured_input=StructuredInput(source="text"))
    assert build_retrieval_query(state) == ""


def test_build_retrieval_query_dedupes_question_repeated_in_problem() -> None:
    """Identical text present in both fields must not be repeated in the
    query text (order-preserving dedup), so BM25 doesn't over-weight it."""
    structured = StructuredInput(
        source="text", question="sliding window problem", problem="sliding window problem"
    )
    state = _state(structured_input=structured)

    query = build_retrieval_query(state)

    assert query == "sliding window problem"


# ---------------------------------------------------------------------------
# retrieve_knowledge node
# ---------------------------------------------------------------------------


async def test_retrieve_knowledge_calls_retriever_when_should_retrieve_true() -> None:
    retriever = FakeRetriever(hits=[_HIT_1, _HIT_2])
    state = _state(
        structured_input=_structured(question="two pointers pattern"),
        intent=_intent(Intent.DSA_SOLVE),
    )

    update = await retrieve_knowledge(state, _runtime(retriever=retriever, knowledge_top_k=3))

    assert update == {"retrieved_context": [_HIT_1, _HIT_2]}
    assert retriever.calls == [("two pointers pattern", 3)]


async def test_retrieve_knowledge_skips_when_should_retrieve_false() -> None:
    retriever = FakeRetriever(hits=[_HIT_1])
    state = _state(
        structured_input=_structured(error="boom"),
        intent=_intent(Intent.CODE_DEBUG),
    )

    update = await retrieve_knowledge(state, _runtime(retriever=retriever))

    assert update == {"retrieved_context": []}
    assert retriever.calls == []


async def test_retrieve_knowledge_none_retriever_returns_empty() -> None:
    state = _state(structured_input=_structured(), intent=_intent(Intent.DSA_SOLVE))

    update = await retrieve_knowledge(state, _runtime(retriever=None))

    assert update == {"retrieved_context": []}


# ---------------------------------------------------------------------------
# end-to-end run_graph
# ---------------------------------------------------------------------------


async def test_run_graph_dsa_solve_calls_retriever_once_with_top_k() -> None:
    retriever = FakeRetriever(hits=[_HIT_1])
    fake = FakeLLMClient(
        chat_content='{"intent": "DSA_SOLVE", "confidence": 0.95, "rationale": "clear"}'
    )
    raw = RawInput(text="Can you help me find the two sum pattern for this problem?")

    result = await run_graph(raw, llm=fake, retriever=retriever, knowledge_top_k=2)

    assert result.state.retrieved_context == [_HIT_1]
    assert len(retriever.calls) == 1
    assert retriever.calls[0][1] == 2


async def test_run_graph_plan_topic_derived_from_retrieval() -> None:
    """Retrieval now runs before planning, so a fresh learner (empty profile,
    no `topic_hint`) whose retrieval surfaces a `two_pointers` chunk still
    gets a plan tagged with that topic instead of `None`."""
    chunk = KnowledgeChunk(
        id="two-sum-1",
        text="Use two pointers on a sorted array to find a pair summing to target.",
        source="two_pointers.md",
        title="Two Pointers",
        heading="Two Sum",
        topic="arrays",
        pattern="two_pointers",
    )
    hit = RetrievalHit(chunk=chunk, score=-1.2, retrievers=("bm25", "dense"), reranked=True)
    retriever = FakeRetriever(hits=[hit])
    fake = FakeLLMClient(
        chat_content='{"intent": "DSA_SOLVE", "confidence": 0.95, "rationale": "clear"}'
    )
    raw = RawInput(text="Can you help me find the two sum pattern for this problem?")

    result = await run_graph(raw, llm=fake, retriever=retriever)

    assert result.state.plan is not None
    assert result.state.plan.topic == "two_pointers"


async def test_run_graph_runtime_error_debug_skips_retriever() -> None:
    retriever = FakeRetriever(hits=[_HIT_1])
    fake = FakeLLMClient()

    result = await run_graph(RawInput(text=_DEBUG_RULE_TEXT), llm=fake, retriever=retriever)

    assert result.state.route == "debug"
    assert result.state.retrieved_context == []
    assert retriever.calls == []


async def test_run_graph_retriever_none_completes_with_empty_context() -> None:
    fake = FakeLLMClient(
        chat_content='{"intent": "DSA_SOLVE", "confidence": 0.95, "rationale": "clear"}'
    )
    raw = RawInput(text="Can you help me find the two sum pattern for this problem?")

    result = await run_graph(raw, llm=fake, retriever=None)

    assert result.state.retrieved_context == []


async def test_run_graph_raising_retriever_degrades_without_leaking() -> None:
    retriever = FakeRetriever(raise_error=True)
    fake = FakeLLMClient(
        chat_content='{"intent": "DSA_SOLVE", "confidence": 0.95, "rationale": "clear"}'
    )
    raw = RawInput(text="Can you help me find the two sum pattern for this problem?")

    result = await run_graph(raw, llm=fake, retriever=retriever)

    assert result.state.retrieved_context == []
    assert any(e.node == "retrieve_knowledge" for e in result.state.errors)
    serialized = result.state.model_dump_json()
    assert "secret-marker" not in serialized


async def test_run_graph_llm_calls_identical_with_and_without_retriever() -> None:
    raw = RawInput(text="Can you help me find the two sum pattern for this problem?")

    fake_without = FakeLLMClient(
        chat_content='{"intent": "DSA_SOLVE", "confidence": 0.95, "rationale": "clear"}'
    )
    result_without = await run_graph(raw, llm=fake_without, retriever=None)

    fake_with = FakeLLMClient(
        chat_content='{"intent": "DSA_SOLVE", "confidence": 0.95, "rationale": "clear"}'
    )
    result_with = await run_graph(raw, llm=fake_with, retriever=FakeRetriever(hits=[_HIT_1]))

    assert result_without.llm_calls == result_with.llm_calls


# ---------------------------------------------------------------------------
# topology
# ---------------------------------------------------------------------------


def test_graph_has_retrieve_knowledge_edges_and_unchanged_route_targets() -> None:
    """Retrieval now runs before planning (`load_learner_profile ->
    retrieve_knowledge -> plan_teaching -> route`), so its labels can inform
    the plan instead of the other way around."""
    drawable = get_graph().get_graph()
    edges = {(edge.source, edge.target) for edge in drawable.edges}

    assert ("load_learner_profile", "retrieve_knowledge") in edges
    assert ("retrieve_knowledge", "plan_teaching") in edges
    assert ("plan_teaching", "route") in edges

    route_targets = {edge.target for edge in drawable.edges if edge.source == "route"}
    assert route_targets == set(ROUTE_NODES.values())


# ---------------------------------------------------------------------------
# lifespan wiring
# ---------------------------------------------------------------------------


def test_lifespan_retriever_none_when_knowledge_disabled(make_settings: MakeSettings) -> None:
    settings = make_settings(knowledge_enabled=False)

    with TestClient(create_app(settings)) as client:
        assert client.app.state.retriever is None  # type: ignore[union-attr]


def test_lifespan_retriever_none_when_create_retriever_raises(
    monkeypatch: pytest.MonkeyPatch, make_settings: MakeSettings
) -> None:
    settings = make_settings(knowledge_enabled=True)

    async def _raising_create_retriever(*args: object, **kwargs: object) -> Retriever:
        del args, kwargs
        raise RuntimeError("model load failed")

    monkeypatch.setattr(main_module, "create_retriever", _raising_create_retriever)

    with TestClient(create_app(settings)) as client:
        assert client.app.state.retriever is None  # type: ignore[union-attr]


def test_lifespan_retriever_set_when_create_retriever_succeeds(
    monkeypatch: pytest.MonkeyPatch, make_settings: MakeSettings
) -> None:
    settings = make_settings(knowledge_enabled=True)
    fake_retriever = FakeRetriever()

    async def _fake_create_retriever(*args: object, **kwargs: object) -> Retriever:
        del args, kwargs
        return fake_retriever

    monkeypatch.setattr(main_module, "create_retriever", _fake_create_retriever)

    with TestClient(create_app(settings)) as client:
        assert client.app.state.retriever is fake_retriever  # type: ignore[union-attr]


def test_lifespan_retriever_none_when_startup_times_out(
    monkeypatch: pytest.MonkeyPatch, make_settings: MakeSettings
) -> None:
    """A `create_retriever` call that outlasts `knowledge_startup_timeout_s`
    must not hang the app -- it degrades to `retriever=None`, same as any
    other startup failure."""
    settings = make_settings(knowledge_enabled=True, knowledge_startup_timeout_s=0.05)

    async def _slow_create_retriever(*args: object, **kwargs: object) -> Retriever:
        del args, kwargs
        await asyncio.sleep(5.0)
        return FakeRetriever()

    monkeypatch.setattr(main_module, "create_retriever", _slow_create_retriever)

    with TestClient(create_app(settings)) as client:
        assert client.app.state.retriever is None  # type: ignore[union-attr]


def test_lifespan_raises_on_corpus_error_instead_of_degrading(
    monkeypatch: pytest.MonkeyPatch, make_settings: MakeSettings
) -> None:
    """A malformed curated corpus is a code bug, not a runtime condition to
    degrade gracefully around -- startup must fail loudly rather than
    silently coming up with `retriever=None`."""
    settings = make_settings(knowledge_enabled=True)

    async def _raising_create_retriever(*args: object, **kwargs: object) -> Retriever:
        del args, kwargs
        raise CorpusError("malformed corpus")

    monkeypatch.setattr(main_module, "create_retriever", _raising_create_retriever)

    with pytest.raises(CorpusError), TestClient(create_app(settings)):
        pass
