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


def _runtime(*, llm: FakeLLMClient | None = None) -> Runtime[GraphContext]:
    return Runtime(context=GraphContext(llm=llm if llm is not None else FakeLLMClient()))


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
# Specialized agents (real Phase 07 subgraphs, not the retired Phase 04 stubs)
# ---------------------------------------------------------------------------
#
# The three agents no longer share one uniform stub contract, so a single
# parametrized "no echo" test can no longer share one fixed LLM/response
# setup across all three: `dsa_agent` reaches the LLM whenever the problem
# text is non-empty, `debug_agent` short-circuits before any LLM call
# whenever `runtime.context.runner` is `None` (as it is in every test here),
# and `explain_agent` only calls the LLM when there is learner *code* to
# explain (there isn't, in `_input()`). Each gets its own test below instead,
# preserving the one property that mattered in the old shared test: the
# learner's raw input text is never echoed into the outcome.


AgentNode = Callable[[AgentState, Runtime[GraphContext]], Awaitable[AgentStateUpdate]]


async def test_dsa_agent_degrades_gracefully_and_does_not_echo_user_input() -> None:
    """`dsa_agent` (unlike the retired stub) makes a real LLM call whenever
    the problem text is non-empty -- a materially new LLM-call path on this
    route. `analyze_dsa_problem` never raises on a bad/empty response
    though, so a fake that returns `"{}"` (a syntactically valid but
    content-free analysis) still produces a real hint, not a crash."""
    plan = _plan(strategy="guided_debugging", assistance="hint", difficulty="easy", topic="graphs")
    state = _state(structured_input=_input(), intent=_intent(Intent.CODE_DEBUG), plan=plan)
    fake = FakeLLMClient(chat_content="{}")

    update = await dsa_agent(state, _runtime(llm=fake))

    outcome = update.get("agent_output")
    assert outcome is not None
    assert fake.chat_calls  # confirms the real subgraph reached the LLM
    # `DSAResult.to_outcome()` never reports a definite solved/unsolved
    # verdict for a hint -- unlike debug_agent below.
    assert outcome.solved is None
    assert MARKER not in outcome.text


async def test_debug_agent_without_sandbox_runner_skips_llm_and_returns_empty_text() -> None:
    """With no sandbox runner wired up (`GraphContext.runner is None`, as in
    every test in this module), `run_debug` short-circuits before the
    debugger subgraph -- and therefore before any LLM call -- to a
    `DebugResult` whose only verdict is "skipped". `DebugResult.to_outcome()`
    never derives `topic` from the plan (unlike the old stub), and reports a
    definite `solved=False` here since "skipped" != "pass"."""
    plan = _plan(strategy="guided_debugging", assistance="hint", difficulty="easy", topic="graphs")
    state = _state(structured_input=_input(), intent=_intent(Intent.CODE_DEBUG), plan=plan)
    fake = FakeLLMClient()

    update = await debug_agent(state, _runtime(llm=fake))

    outcome = update.get("agent_output")
    assert outcome is not None
    assert fake.chat_calls == []
    assert outcome.text == ""
    assert outcome.topic is None
    # No runner means nothing was executed, so there is NO evidence about the
    # learner either way: `solved` must stay None so `update_learner_model`
    # skips the event rather than recording an unobserved failure.
    assert outcome.solved is None
    assert MARKER not in outcome.text


async def test_explain_agent_without_code_skips_llm_and_has_no_topic() -> None:
    """`_input()` carries a question but no code, so `extract_learner_code`
    returns `None` and the explainer subgraph's two LLM-calling nodes both
    skip -- zero LLM calls, matching the old stub's LLM-free property (for
    this no-code input) even though the pipeline behind it is now real.
    `ExplainResult.to_outcome()` never carries a `topic` at all."""
    plan = _plan(strategy="guided_debugging", assistance="hint", difficulty="easy", topic="graphs")
    state = _state(structured_input=_input(), intent=_intent(Intent.CODE_DEBUG), plan=plan)
    fake = FakeLLMClient()

    update = await explain_agent(state, _runtime(llm=fake))

    outcome = update.get("agent_output")
    assert outcome is not None
    assert fake.chat_calls == []
    assert outcome.solved is None
    assert outcome.topic is None
    assert MARKER not in outcome.text


async def test_debug_agent_without_runner_ignores_plan_content() -> None:
    """Renamed from the retired `test_debug_agent_stub_text_matches_plan_template`:
    the old stub echoed the plan's strategy/assistance/difficulty into its
    placeholder text. The real `debug_agent`, short-circuited by the absence
    of a sandbox runner, produces empty text regardless of the plan's
    content -- the plan's fields are simply not part of this result."""
    plan = _plan(strategy="guided_debugging", assistance="hint", difficulty="easy")
    state = _state(structured_input=_input(), intent=_intent(Intent.CODE_DEBUG), plan=plan)

    update = await debug_agent(state, _runtime())

    outcome = update.get("agent_output")
    assert outcome is not None
    assert outcome.text == ""


async def test_debug_agent_with_no_plan_has_no_topic_and_no_solved_evidence() -> None:
    """Renamed from the retired `test_stub_agent_with_no_plan_has_no_topic`:
    the old stub's "topic is None regardless of plan" invariant doesn't hold
    for `solved` any more -- `debug_agent` reports a definite `solved=False`
    (from the "skipped" verdict) whether or not a plan is present, since
    `run_debug`'s no-runner short-circuit never even looks at `state.plan`."""
    state = _state(structured_input=_input(), intent=_intent(Intent.CODE_DEBUG), plan=None)

    update = await debug_agent(state, _runtime())

    outcome = update.get("agent_output")
    assert outcome is not None
    assert outcome.topic is None
    # No runner means nothing was executed, so there is NO evidence about the
    # learner either way: `solved` must stay None so `update_learner_model`
    # skips the event rather than recording an unobserved failure.
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
