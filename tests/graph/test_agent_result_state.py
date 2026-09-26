"""P0 tests: `AgentState.agent_result` carries the full structured Phase 07
agent result alongside the lossy `AgentOutcome` projection.

Covers: `dsa_agent`/`debug_agent`/`explain_agent` surfacing the same result
object their subgraph produced, `explain_agent`'s reviewer/explainer
dispatch tagging the right `kind`, and the discriminated-union round-trip
through `model_dump()`/`model_validate()` that `run_graph` performs on
LangGraph's output -- the entire point of tagging `AgentResult` with `kind`.
No Docker, no network, no real LLM/provider calls.
"""

from app.graph.nodes import debug_agent, dsa_agent, explain_agent
from app.graph.state import AgentState, RawInput
from app.schemas.agent_results import DebugResult, DSAResult, ExplainResult, ReviewResult
from app.schemas.input import CodeBlock, StructuredInput
from app.schemas.intent import Intent
from tests.graph.test_phase7_wiring import (
    _pipeline_state,  # pyright: ignore[reportPrivateUsage]
    _plan,  # pyright: ignore[reportPrivateUsage]
    _runtime,  # pyright: ignore[reportPrivateUsage]
)
from tests.input.fakes import FakeLLMClient

# ---------------------------------------------------------------------------
# Each agent node surfaces the same result object its subgraph produced
# ---------------------------------------------------------------------------


async def test_dsa_agent_surfaces_agent_result_of_kind_dsa() -> None:
    state = _pipeline_state(
        structured=StructuredInput(source="text", problem="Given an array of integers, ..."),
        plan=_plan(topic="arrays", assistance_level="hint"),
    )
    llm = FakeLLMClient(chat_content="{}")

    update = await dsa_agent(state, _runtime(llm=llm))

    result = update.get("agent_result")
    assert isinstance(result, DSAResult)
    assert result.kind == "dsa"


async def test_debug_agent_surfaces_agent_result_of_kind_debug() -> None:
    state = _pipeline_state(
        route_key="debug",
        intent=Intent.CODE_DEBUG,
        structured=StructuredInput(
            source="text", question="why does this fail", code=[CodeBlock(content="def f(): 1/0")]
        ),
    )

    update = await debug_agent(state, _runtime())

    result = update.get("agent_result")
    assert isinstance(result, DebugResult)
    assert result.kind == "debug"


async def test_explain_agent_surfaces_agent_result_of_kind_explain_for_explain_intent() -> None:
    state = _pipeline_state(
        route_key="explain",
        intent=Intent.CODE_EXPLAIN,
        structured=StructuredInput(source="text", question="what does this do"),
    )

    update = await explain_agent(state, _runtime())

    result = update.get("agent_result")
    assert isinstance(result, ExplainResult)
    assert result.kind == "explain"


async def test_explain_agent_surfaces_agent_result_of_kind_review_for_code_review_intent() -> None:
    code = "def f():\n    return 1\n"
    structured = StructuredInput(
        source="text", question="review this", code=[CodeBlock(content=code)]
    )
    state = _pipeline_state(
        route_key="explain",
        intent=Intent.CODE_REVIEW,
        structured=structured,
    )
    llm = FakeLLMClient(chat_content='{"findings": []}')

    update = await explain_agent(state, _runtime(llm=llm))

    result = update.get("agent_result")
    assert isinstance(result, ReviewResult)
    assert result.kind == "review"


# ---------------------------------------------------------------------------
# The discriminated-union round-trip guard: this is the point of `kind`
# ---------------------------------------------------------------------------


def _base_state(**kwargs: object) -> AgentState:
    return AgentState(input=RawInput(text="hello"), **kwargs)  # type: ignore[arg-type]


def test_agent_state_round_trips_dsa_result_through_model_dump_and_validate() -> None:
    result = DSAResult(topic="arrays", pattern="two_pointers", understanding="explain this")
    state = _base_state(agent_result=result)

    restored = AgentState.model_validate(state.model_dump())

    assert type(restored.agent_result) is DSAResult
    assert restored.agent_result == result


def test_agent_state_round_trips_debug_result_through_model_dump_and_validate() -> None:
    result = DebugResult(inferred_approach="brute force", bug_explanation="off by one")
    state = _base_state(agent_result=result)

    restored = AgentState.model_validate(state.model_dump())

    assert type(restored.agent_result) is DebugResult
    assert restored.agent_result == result


def test_agent_state_round_trips_explain_result_through_model_dump_and_validate() -> None:
    result = ExplainResult(complexity_time="O(n)", complexity_rationale="single pass")
    state = _base_state(agent_result=result)

    restored = AgentState.model_validate(state.model_dump())

    assert type(restored.agent_result) is ExplainResult
    assert restored.agent_result == result


def test_agent_state_round_trips_review_result_through_model_dump_and_validate() -> None:
    result = ReviewResult()
    state = _base_state(agent_result=result)

    restored = AgentState.model_validate(state.model_dump())

    assert type(restored.agent_result) is ReviewResult
    assert restored.agent_result == result


def test_agent_state_with_no_agent_result_still_validates() -> None:
    state = _base_state(agent_result=None)

    restored = AgentState.model_validate(state.model_dump())

    assert restored.agent_result is None
