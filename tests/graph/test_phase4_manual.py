"""Phase 4 manual test cases, run end-to-end through `run_graph`.

Every test drives the *whole* teaching graph (never individual nodes)
directly through `app.graph.build.run_graph` (or, for the node-failure sweep,
a graph built the same way `run_graph` builds/configures one, just with a
single node swapped for a function that always raises). The two "manual"
tests print an `ACTUAL:` line (visible with `pytest -s`) summarizing the
run's route/intent/plan/events/errors/llm_calls, for pasting into the phase
doc.
"""

from typing import Final
from uuid import UUID

import pytest
from langgraph.runtime import Runtime  # pyright: ignore[reportMissingTypeStubs]
from sqlalchemy.ext.asyncio import AsyncSession

from app.graph.build import (
    NODE_FUNCTIONS,
    RECURSION_LIMIT,
    GraphRunResult,
    build_graph,
    run_graph,
)
from app.graph.nodes import Node
from app.graph.routing import INTENT_ROUTES, ROUTE_NODES
from app.graph.state import AgentState, AgentStateUpdate, GraphContext, RawInput
from app.llm.budget import DEFAULT_MAX_LLM_CALLS, BudgetedLLMClient
from app.memory.profile import ensure_profile, get_profile
from app.schemas.intent import Intent
from tests.input.fakes import FakeLLMClient

# --------------------------------------------------------------------------
# Manual Test 1: db-backed debug turn, weak-skill + prefers-hints learner
# --------------------------------------------------------------------------

_MT1_TEXT: Final = (
    "```python\n"
    "def max_sum_subarray(nums, k):\n"
    "    window_sum = 0\n"
    "    for i in range(len(nums) + 1):\n"
    "        window_sum += nums[i]\n"
    "    return window_sum\n"
    "```\n"
    "\n"
    "Traceback (most recent call last):\n"
    '  File "solution.py", line 5, in <module>\n'
    "    window_sum += nums[i]\n"
    "IndexError: list index out of range\n"
    "\n"
    "my sliding window solution crashes, why?\n"
)


async def _seed_profile(
    session: AsyncSession,
    user_id: UUID,
    *,
    skills: dict[str, float],
    preferences: dict[str, bool],
    errors: dict[str, int],
) -> None:
    """Seed skill levels, preferences, and error counts directly on the profile row.

    Mirrors the `ensure_profile(..., for_update=True)` + direct field
    assignment pattern `tests/graph/test_chat_api.py::_set_skill_level` (and
    `app.memory.profile.set_learning_preferences`/`set_language`) use, applied
    to all three fields at once for this test's setup.
    """
    profile = await ensure_profile(session, user_id, for_update=True)
    profile.skill_levels = dict(skills)
    profile.learning_preferences = dict(preferences)
    profile.common_errors = dict(errors)
    await session.flush()


@pytest.mark.db
async def test_manual_1_debug_turn_for_weak_skill_hint_preferring_learner(
    db_session: AsyncSession, user_id: UUID
) -> None:
    await _seed_profile(
        db_session,
        user_id,
        skills={"sliding_window": 0.3, "arrays": 0.8},
        preferences={"prefers_hints": True},
        errors={"IndexError": 2},
    )

    fake = FakeLLMClient(
        chat_content=(
            '{"intent": "CODE_DEBUG", "confidence": 0.9, "rationale": "failing code, traceback"}'
        )
    )

    result = await run_graph(
        RawInput(text=_MT1_TEXT), llm=fake, session=db_session, user_id=user_id
    )
    state = result.state

    print(
        f"ACTUAL: route={state.route!r} "
        f"intent={state.intent.intent if state.intent else None} "
        f"conf={state.intent.confidence if state.intent else -1.0:.2f} "
        f"source={state.intent.source if state.intent else None} "
        f"plan(difficulty={state.plan.difficulty if state.plan else None!r} "
        f"assistance={state.plan.assistance_level if state.plan else None!r} "
        f"strategy={state.plan.solution_strategy if state.plan else None!r} "
        f"topic={state.plan.topic if state.plan else None!r} "
        f"rationale={state.plan.rationale if state.plan else None!r}) "
        f"events={len(state.events)} events_persisted={state.events_persisted!r} "
        f"errors={state.errors!r} llm_calls={result.llm_calls}"
    )

    assert fake.chat_calls  # the question forced Phase 2 onto the LLM path
    assert result.llm_calls == 1

    assert state.route == "debug"
    assert state.plan is not None
    assert state.plan.assistance_level == "hint"
    assert state.plan.solution_strategy == "guided_debugging"
    assert state.plan.difficulty == "easy"
    assert state.plan.topic == "sliding_window"
    assert "weak_skill" in state.plan.rationale
    assert "prefers_hints" in state.plan.rationale

    # No sandbox runner is configured for this test (`run_graph` is called
    # without one), so `run_debug` short-circuits before any code executes
    # and before any filler text of its own; the Phase 08 response layer
    # still renders the (skipped) verification outcome, so the response is
    # non-empty rather than a stub placeholder.
    assert state.response is not None
    assert state.response != ""
    assert "stub" not in state.response.lower()

    assert len(state.events) == 1
    event = state.events[0]
    assert event.topic == "sliding_window"
    assert event.requested_help == "debug"
    assert event.difficulty == "easy"
    # The sandbox runner is unavailable in this test, so the debugger never
    # executed anything: the verdict is "skipped", so `DebugResult.
    # to_outcome()` reports `solved=None` ("we could not check", not
    # evidence of failure). `update_learner_model` persists this event
    # regardless -- it records exposure to the topic, not an outcome.
    assert event.solved is None
    assert len(state.events_persisted) == 1

    # `solved=None` is exposure, not an observed outcome: `apply_event`
    # leaves both already-seeded skill levels exactly as they were.
    profile_after = await get_profile(db_session, user_id)
    assert profile_after.skill_levels == {"sliding_window": 0.3, "arrays": 0.8}


# --------------------------------------------------------------------------
# Manual Test 2: ambiguous message -> clarify, with and without a usable LLM
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "chat_content",
    [
        pytest.param(
            '{"intent": "CODE_EXPLAIN", "confidence": 0.3, "rationale": "genuinely ambiguous"}',
            id="valid_low_confidence",
        ),
        pytest.param("not valid json at all, sorry", id="unparsable_falls_back_to_keywords"),
    ],
)
async def test_manual_2_ambiguous_message_routes_to_clarify(chat_content: str) -> None:
    fake = FakeLLMClient(chat_content=chat_content)
    user_text = "hmm can you look at this thing"

    result = await run_graph(RawInput(text=user_text), llm=fake)
    state = result.state

    print(
        f"ACTUAL: route={state.route!r} "
        f"intent={state.intent.intent if state.intent else None} "
        f"conf={state.intent.confidence if state.intent else -1.0:.2f} "
        f"source={state.intent.source if state.intent else None} "
        f"plan(strategy={state.plan.solution_strategy if state.plan else None!r} "
        f"rationale={state.plan.rationale if state.plan else None!r}) "
        f"events={len(state.events)} events_persisted={state.events_persisted!r} "
        f"errors={state.errors!r} llm_calls={result.llm_calls}"
    )

    assert result.llm_calls == 1
    assert state.intent is not None
    assert state.intent.confidence <= 0.45 or state.intent.confidence == 0.3
    assert state.intent.low_confidence

    assert state.route == "clarify"
    assert state.plan is not None
    assert state.plan.solution_strategy == "clarify"

    assert state.response is not None
    assert "?" in state.response
    assert user_text not in state.response

    assert state.events == []


# --------------------------------------------------------------------------
# Every intent routes end-to-end
# --------------------------------------------------------------------------

_PLAIN_QUESTION: Final = "Can you help me understand this better?"


@pytest.mark.parametrize("intent", list(Intent))
async def test_each_intent_routes_end_to_end(intent: Intent) -> None:
    """Each intent still reaches its real specialized-agent subgraph.

    The Phase 04 stub text (`"[<label> stub] ..."`) is gone -- each route's
    real response shape now differs by subgraph and input (e.g. the debug
    route is `""` with no sandbox runner configured; the explain route is
    `""` too when `_PLAIN_QUESTION` carries no code to explain; the dsa
    route produces a real first-rung hint). This test's job is routing, not
    response content, so it only asserts the response is never `None` and
    never echoes the retired stub marker.
    """
    fake = FakeLLMClient(
        chat_content=f'{{"intent": "{intent.value}", "confidence": 0.95, "rationale": "clear"}}'
    )

    result = await run_graph(RawInput(text=_PLAIN_QUESTION), llm=fake)
    state = result.state

    expected_route = INTENT_ROUTES[intent]
    assert ROUTE_NODES[expected_route]  # sanity: route resolves to a real node name

    assert state.intent is not None
    assert state.intent.intent == intent
    assert state.route == expected_route
    assert state.response is not None
    assert "stub" not in state.response.lower()


# --------------------------------------------------------------------------
# A failing node mid-run never crashes the graph or leaks its exception
# --------------------------------------------------------------------------

_DEBUG_RULE_TEXT: Final = (
    "```python\n"
    "def get_item(items, idx):\n"
    "    return items[idx]\n"
    "```\n"
    "\n"
    "IndexError: list index out of range\n"
)

_DSA_RULE_TEXT: Final = (
    "Given an array of integers nums and an integer target, return indices "
    "of the two numbers such that they add up to target.\n"
    "\n"
    "You may assume that each input would have exactly one solution, and you "
    "may not use the same element twice.\n"
    "\n"
    "Example 1:\n"
    "Input: nums = [2,7,11,15], target = 9\n"
    "Output: [0,1]\n"
    "Explanation: Because nums[0] + nums[1] == 9, we return [0, 1].\n"
    "\n"
    "Constraints:\n"
    "- 2 <= nums.length <= 10^4\n"
    "- -10^9 <= nums[i] <= 10^9\n"
)

_EXPLAIN_LLM_TEXT: Final = "What does this approach generally accomplish?"


def _node_inputs() -> dict[str, tuple[RawInput, FakeLLMClient]]:
    """Per-node `(raw input, fake llm)` guaranteed to route through that node.

    Most nodes sit on every turn's straight-line path, so a generic empty
    input (which deterministically routes to "clarify" with zero LLM calls)
    reaches them. The three specialized-agent stubs need a route-specific
    input instead, since only one of them runs per turn.
    """
    return {
        "understand_input": (RawInput(), FakeLLMClient()),
        "classify_intent": (RawInput(), FakeLLMClient()),
        "load_learner_profile": (RawInput(), FakeLLMClient()),
        "plan_teaching": (RawInput(), FakeLLMClient()),
        "retrieve_knowledge": (RawInput(), FakeLLMClient()),
        "route": (RawInput(), FakeLLMClient()),
        "clarify": (RawInput(), FakeLLMClient()),
        "final_response": (RawInput(), FakeLLMClient()),
        "update_learner_model": (RawInput(), FakeLLMClient()),
        "dsa_agent": (RawInput(text=_DSA_RULE_TEXT), FakeLLMClient()),
        "debug_agent": (RawInput(text=_DEBUG_RULE_TEXT), FakeLLMClient()),
        "explain_agent": (
            RawInput(text=_EXPLAIN_LLM_TEXT),
            FakeLLMClient(
                chat_content='{"intent": "CODE_EXPLAIN", "confidence": 0.95, "rationale": "n/a"}'
            ),
        ),
        # No agent sets an `execution_request` in Phase 06, so these two
        # nodes are always no-op skips on the straight-line path -- any input
        # that reaches them (i.e. not "clarify", which skips execute_code
        # entirely) is enough to exercise the failure sweep.
        "execute_code": (RawInput(text=_DSA_RULE_TEXT), FakeLLMClient()),
        "verify": (RawInput(text=_DSA_RULE_TEXT), FakeLLMClient()),
    }


def _make_raiser() -> Node:
    async def raiser(state: AgentState, *, runtime: Runtime[GraphContext]) -> AgentStateUpdate:
        del state, runtime
        raise RuntimeError("boom-secret")

    return raiser


async def _run_with_node_override(name: str, raw: RawInput, llm: FakeLLMClient) -> GraphRunResult:
    """Mirror `run_graph`'s body, on a fresh graph with node `name` forced to fail."""
    graph = build_graph(node_overrides={name: _make_raiser()})
    budgeted = BudgetedLLMClient(llm, DEFAULT_MAX_LLM_CALLS)
    context = GraphContext(llm=budgeted, session=None, user_id=None, conversation_id=None)
    result = await graph.ainvoke(  # pyright: ignore[reportUnknownMemberType]
        AgentState(input=raw),
        config={"recursion_limit": RECURSION_LIMIT, "run_name": "teaching_graph"},
        context=context,
    )
    state = AgentState.model_validate(result)
    return GraphRunResult(state=state, llm_calls=budgeted.calls)


@pytest.mark.parametrize("name", sorted(NODE_FUNCTIONS))
async def test_any_node_failure_degrades_without_crashing_or_leaking(name: str) -> None:
    raw, fake = _node_inputs()[name]

    result = await _run_with_node_override(name, raw, fake)

    assert any(err.node == name for err in result.state.errors)
    assert result.state.response  # non-empty, even for update_learner_model
    serialized = result.state.model_dump_json()
    assert "boom-secret" not in serialized


# --------------------------------------------------------------------------
# LLM call budget is respected across vision + chat in the same turn
# --------------------------------------------------------------------------

_FAKE_PNG: Final = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
_VISION_JSON: Final = (
    '{"problem": null, "code": null, "code_language": null, "error": null, '
    '"constraints": [], "question": null}'
)


async def test_image_plus_ambiguous_text_llm_calls_bounded_by_default_budget() -> None:
    fake = FakeLLMClient(
        vision_content=_VISION_JSON,
        chat_content='{"intent": "CODE_EXPLAIN", "confidence": 0.3, "rationale": "unclear"}',
    )
    raw = RawInput(text="hmm, take a look at this", image=_FAKE_PNG, image_mime="image/png")

    result = await run_graph(raw, llm=fake)

    total_fake_calls = len(fake.vision_calls) + len(fake.chat_calls)
    assert result.llm_calls <= DEFAULT_MAX_LLM_CALLS
    assert result.llm_calls == total_fake_calls


async def test_image_plus_ambiguous_text_with_budget_of_one_falls_back_after_vision() -> None:
    fake = FakeLLMClient(
        vision_content=_VISION_JSON,
        chat_content='{"intent": "CODE_EXPLAIN", "confidence": 0.3, "rationale": "unclear"}',
    )
    raw = RawInput(text="hmm, take a look at this", image=_FAKE_PNG, image_mime="image/png")

    result = await run_graph(raw, llm=fake, max_llm_calls=1)

    assert result.llm_calls == 1
    assert len(fake.vision_calls) == 1
    assert fake.chat_calls == []
    assert result.state.intent is not None
    assert result.state.intent.source == "fallback"
    assert result.state.response is not None
