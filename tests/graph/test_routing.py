"""Tests for intent -> route selection and the stub/clarify nodes.

Pure unit tests: no database, no LLM, no markers.
"""

from collections.abc import Awaitable, Callable
from typing import get_args

import pytest
from langgraph.runtime import Runtime

from app.graph.nodes import clarify, debug_agent, dsa_agent, explain_agent
from app.graph.routing import INTENT_ROUTES, ROUTE_NODES, route_after, select_route
from app.graph.state import AgentState, AgentStateUpdate, GraphContext, RawInput, RouteKey
from app.schemas.input import StructuredInput
from app.schemas.intent import Intent, IntentResult
from app.schemas.plan import TeachingPlan
from tests.input.fakes import FakeLLMClient

MARKER = "zzz_untrusted_marker_zzz"


def _runtime() -> Runtime[GraphContext]:
    return Runtime(context=GraphContext(llm=FakeLLMClient()))


def _plan(
    strategy: str = "socratic_hints",
    assistance: str = "hint",
    difficulty: str = "easy",
    topic: str | None = "arrays",
) -> TeachingPlan:
    return TeachingPlan(
        difficulty=difficulty,  # type: ignore[arg-type]
        assistance_level=assistance,  # type: ignore[arg-type]
        solution_strategy=strategy,  # type: ignore[arg-type]
        topic=topic,
        skill_level=0.5,
    )


def _intent(intent: Intent, confidence: float = 0.9) -> IntentResult:
    return IntentResult(intent=intent, confidence=confidence, source="rule")


def _input(question: str | None = MARKER) -> StructuredInput:
    return StructuredInput(source="text", question=question)


def _state(
    *,
    structured_input: StructuredInput | None = None,
    intent: IntentResult | None = None,
    plan: TeachingPlan | None = None,
) -> AgentState:
    return AgentState(
        input=RawInput(text=MARKER),
        structured_input=structured_input,
        intent=intent,
        plan=plan,
    )


# ---------------------------------------------------------------------------
# INTENT_ROUTES / ROUTE_NODES contract
# ---------------------------------------------------------------------------


def test_intent_routes_covers_every_intent() -> None:
    assert set(INTENT_ROUTES) == set(Intent)


def test_intent_routes_values_are_route_node_keys() -> None:
    for route_key in INTENT_ROUTES.values():
        assert route_key in ROUTE_NODES


def test_route_nodes_keys_match_route_key_literal() -> None:
    assert set(ROUTE_NODES) == set(get_args(RouteKey))


# ---------------------------------------------------------------------------
# select_route: happy path over all intents
# ---------------------------------------------------------------------------

_EXPECTED_ROUTE: dict[Intent, RouteKey] = {
    Intent.DSA_SOLVE: "dsa",
    Intent.DSA_HINT: "dsa",
    Intent.APPROACH_DISCUSSION: "dsa",
    Intent.CODE_DEBUG: "debug",
    Intent.ERROR_EXPLANATION: "debug",
    Intent.TEST_CASE_ANALYSIS: "debug",
    Intent.CODE_EXPLAIN: "explain",
    Intent.CONCEPT_EXPLANATION: "explain",
    Intent.IMAGE_CODE_ANALYSIS: "explain",
    Intent.CODE_REVIEW: "explain",
    Intent.OPTIMIZATION: "explain",
}


@pytest.mark.parametrize("intent_value", list(Intent))
def test_select_route_high_confidence(intent_value: Intent) -> None:
    state = _state(structured_input=_input(), intent=_intent(intent_value, 0.9), plan=_plan())
    assert select_route(state) == _EXPECTED_ROUTE[intent_value]


@pytest.mark.parametrize("intent_value", list(Intent))
def test_select_route_low_confidence_clarifies(intent_value: Intent) -> None:
    state = _state(structured_input=_input(), intent=_intent(intent_value, 0.3), plan=_plan())
    assert select_route(state) == "clarify"


def test_select_route_structured_input_none_clarifies() -> None:
    state = _state(structured_input=None, intent=_intent(Intent.CODE_DEBUG), plan=_plan())
    assert select_route(state) == "clarify"


def test_select_route_structured_input_empty_clarifies() -> None:
    state = _state(
        structured_input=StructuredInput(source="text"),
        intent=_intent(Intent.CODE_DEBUG),
        plan=_plan(),
    )
    assert select_route(state) == "clarify"


def test_select_route_intent_none_clarifies() -> None:
    state = _state(structured_input=_input(), intent=None, plan=_plan())
    assert select_route(state) == "clarify"


def test_select_route_plan_strategy_clarify_clarifies() -> None:
    state = _state(
        structured_input=_input(),
        intent=_intent(Intent.CODE_DEBUG, 0.9),
        plan=_plan(strategy="clarify", assistance="hint"),
    )
    assert select_route(state) == "clarify"


# ---------------------------------------------------------------------------
# route_after
# ---------------------------------------------------------------------------


def test_route_after_returns_state_route() -> None:
    state = _state(structured_input=_input(), intent=_intent(Intent.DSA_SOLVE), plan=_plan())
    routed = state.model_copy(update={"route": "dsa"})
    assert route_after(routed) == "dsa"


def test_route_after_defaults_to_clarify_when_none() -> None:
    state = _state(structured_input=_input(), intent=_intent(Intent.DSA_SOLVE), plan=_plan())
    assert state.route is None
    assert route_after(state) == "clarify"


# ---------------------------------------------------------------------------
# Agent stubs
# ---------------------------------------------------------------------------


AgentNode = Callable[[AgentState, Runtime[GraphContext]], Awaitable[AgentStateUpdate]]


@pytest.mark.parametrize(
    "agent_node",
    [dsa_agent, debug_agent, explain_agent],
)
async def test_stub_agents_return_outcome_without_echoing_user_input(
    agent_node: AgentNode,
) -> None:
    plan = _plan(strategy="guided_debugging", assistance="hint", difficulty="easy", topic="graphs")
    state = _state(structured_input=_input(), intent=_intent(Intent.CODE_DEBUG), plan=plan)

    update = await agent_node(state, _runtime())

    outcome = update.get("agent_output")
    assert outcome is not None
    assert outcome.solved is None
    assert outcome.topic == "graphs"
    assert MARKER not in outcome.text


async def test_debug_agent_stub_text_matches_plan_template() -> None:
    plan = _plan(strategy="guided_debugging", assistance="hint", difficulty="easy")
    state = _state(structured_input=_input(), intent=_intent(Intent.CODE_DEBUG), plan=plan)

    update = await debug_agent(state, _runtime())

    outcome = update.get("agent_output")
    assert outcome is not None
    assert "guided_debugging" in outcome.text
    assert "'hint'" in outcome.text
    assert "easy" in outcome.text


async def test_stub_agent_with_no_plan_has_no_topic() -> None:
    state = _state(structured_input=_input(), intent=_intent(Intent.CODE_DEBUG), plan=None)

    update = await debug_agent(state, _runtime())

    outcome = update.get("agent_output")
    assert outcome is not None
    assert outcome.topic is None
    assert outcome.solved is None
    assert MARKER not in outcome.text


# ---------------------------------------------------------------------------
# clarify node
# ---------------------------------------------------------------------------


async def test_clarify_empty_input_asks_for_problem_code_error() -> None:
    state = _state(structured_input=None, intent=None, plan=None)

    update = await clarify(state, _runtime())

    outcome = update.get("agent_output")
    assert outcome is not None
    assert outcome.solved is None
    assert "problem statement" in outcome.text
    assert "code" in outcome.text
    assert "error" in outcome.text
    assert MARKER not in outcome.text


async def test_clarify_with_intent_mentions_best_guess_phrase() -> None:
    state = _state(structured_input=_input(), intent=_intent(Intent.CODE_DEBUG, 0.9), plan=None)

    update = await clarify(state, _runtime())

    outcome = update.get("agent_output")
    assert outcome is not None
    assert "debug your code" in outcome.text
    assert MARKER not in outcome.text


async def test_clarify_generic_variant_when_no_intent_but_has_input() -> None:
    state = _state(structured_input=_input(), intent=None, plan=None)

    update = await clarify(state, _runtime())

    outcome = update.get("agent_output")
    assert outcome is not None
    assert MARKER not in outcome.text
    assert "hint" in outcome.text
