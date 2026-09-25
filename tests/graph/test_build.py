"""Tests for `app.graph.build`: graph assembly, structure, and `run_graph`."""

from langgraph.runtime import Runtime  # pyright: ignore[reportMissingTypeStubs]

from app.graph.build import NODE_FUNCTIONS, build_graph, get_graph, run_graph
from app.graph.nodes import FALLBACKS
from app.graph.routing import ROUTE_NODES
from app.graph.state import AgentState, AgentStateUpdate, GraphContext, RawInput
from tests.input.fakes import FakeLLMClient

_NODE_NAMES = frozenset(NODE_FUNCTIONS)

_EXPECTED_NODES = _NODE_NAMES | {"__start__", "__end__"}

_EXPECTED_EDGES: frozenset[tuple[str, str]] = frozenset(
    {
        ("__start__", "understand_input"),
        ("understand_input", "classify_intent"),
        ("classify_intent", "load_learner_profile"),
        ("load_learner_profile", "plan_teaching"),
        ("plan_teaching", "retrieve_knowledge"),
        ("retrieve_knowledge", "route"),
        ("route", "dsa_agent"),
        ("route", "debug_agent"),
        ("route", "explain_agent"),
        ("route", "clarify"),
        ("dsa_agent", "execute_code"),
        ("debug_agent", "execute_code"),
        ("explain_agent", "execute_code"),
        ("clarify", "final_response"),
        ("execute_code", "verify"),
        ("verify", "final_response"),
        ("final_response", "update_learner_model"),
        ("update_learner_model", "__end__"),
    }
)

_DEBUG_TEXT = (
    "```python\n"
    "def get_item(items, idx):\n"
    "    return items[idx]\n"
    "```\n"
    "\n"
    "IndexError: list index out of range\n"
)


def test_graph_structure_matches_the_stable_edge_contract() -> None:
    drawable = get_graph().get_graph()

    assert set(drawable.nodes) == _EXPECTED_NODES
    assert {(edge.source, edge.target) for edge in drawable.edges} == _EXPECTED_EDGES


def test_node_functions_and_fallbacks_declare_the_same_names() -> None:
    assert set(NODE_FUNCTIONS) == set(FALLBACKS)
    # Sanity check the route contract lines up with real node names too.
    assert set(ROUTE_NODES.values()) <= set(NODE_FUNCTIONS)


async def test_end_to_end_debug_route_with_zero_llm_calls() -> None:
    """No sandbox runner is configured for this run, so `run_debug` short-
    circuits before spending any of the turn's LLM budget (the "zero LLM
    calls" property this test name promises still holds for the real
    debugger, not just the retired stub). With no runner, `DebugResult`
    carries no filler text of its own, so the turn's response is the empty
    string rather than a stub placeholder."""
    fake = FakeLLMClient()

    result = await run_graph(RawInput(text=_DEBUG_TEXT), llm=fake)

    assert result.llm_calls == 0
    assert result.state.route == "debug"
    assert result.state.response == ""
    assert result.state.plan is not None
    assert result.state.errors == []


async def test_a_failing_node_degrades_to_its_fallback_and_still_produces_a_response() -> None:
    async def failing_plan_teaching(
        state: AgentState, *, runtime: Runtime[GraphContext]
    ) -> AgentStateUpdate:
        del state, runtime
        raise RuntimeError("boom")

    # `build_graph` with an override, not the cached `get_graph()`, so this
    # doesn't pollute the shared compiled graph used by other tests.
    graph = build_graph(node_overrides={"plan_teaching": failing_plan_teaching})

    result = await graph.ainvoke(  # pyright: ignore[reportUnknownMemberType]
        AgentState(input=RawInput(text=_DEBUG_TEXT)),
        context=GraphContext(llm=FakeLLMClient()),
    )

    errors = result["errors"]
    assert any(e.node == "plan_teaching" for e in errors)
    assert result.get("response") is not None


async def test_budget_of_zero_forces_keyword_fallback_and_clarify_route() -> None:
    fake = FakeLLMClient(
        chat_content='{"intent": "OPTIMIZATION", "confidence": 0.7, "rationale": "n/a"}'
    )

    result = await run_graph(RawInput(text="why is my code slow?"), llm=fake, max_llm_calls=0)

    assert result.llm_calls == 0
    assert fake.chat_calls == []
    assert result.state.intent is not None
    assert result.state.intent.source == "fallback"
    assert result.state.intent.low_confidence is True
    assert result.state.route == "clarify"


async def test_empty_input_routes_to_clarify_and_asks_for_more() -> None:
    result = await run_graph(RawInput(), llm=FakeLLMClient())

    assert result.state.route == "clarify"
    assert result.state.response is not None
    response = result.state.response.lower()
    assert "problem" in response
    assert "code" in response
    assert "error" in response


_FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
_VISION_JSON = (
    '{"problem": null, "code": null, "code_language": null, "error": null, '
    '"constraints": [], "question": null}'
)


async def test_image_bytes_are_not_carried_through_the_final_state() -> None:
    fake = FakeLLMClient(
        vision_content=_VISION_JSON,
        chat_content='{"intent": "CODE_EXPLAIN", "confidence": 0.9, "rationale": "ok"}',
    )
    raw = RawInput(text="what does this do", image=_FAKE_PNG, image_mime="image/png")

    result = await run_graph(raw, llm=fake)

    assert result.state.input.image is None
    assert result.state.input.image_mime == "image/png"
