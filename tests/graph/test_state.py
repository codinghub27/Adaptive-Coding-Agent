"""Tests for the `AgentState`/`AgentStateUpdate` contract and TeachingPlan.

Includes a LangGraph smoke test proving `AgentState` (frozen, `extra="forbid"`)
works as a `StateGraph` state schema with `GraphContext` as `context_schema`
under the installed langgraph version.
"""

from typing import get_args, get_type_hints

import pytest
from langgraph.graph import END, START, StateGraph  # pyright: ignore[reportMissingTypeStubs]
from langgraph.runtime import Runtime
from pydantic import ValidationError

from app.graph.state import (
    AgentState,
    AgentStateUpdate,
    GraphContext,
    NodeError,
    RawInput,
)
from app.schemas.plan import ASSISTANCE_ORDER, AssistanceLevel, TeachingPlan
from tests.input.fakes import FakeLLMClient


def test_agent_state_update_matches_agent_state_fields() -> None:
    """Drift guard: `AgentStateUpdate` must mirror every `AgentState` field."""
    assert set(AgentStateUpdate.__annotations__.keys()) == set(AgentState.model_fields.keys())


def test_agent_state_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        AgentState.model_validate({"input": {"text": "hi"}, "bogus": 1})


def test_agent_state_is_frozen() -> None:
    state = AgentState(input=RawInput(text="hi"))
    with pytest.raises(ValidationError):
        state.response = "nope"  # type: ignore[misc]


def test_teaching_plan_rejects_out_of_range_skill_level() -> None:
    with pytest.raises(ValidationError):
        TeachingPlan(
            difficulty="easy",
            assistance_level="hint",
            solution_strategy="socratic_hints",
            skill_level=1.5,
        )


def test_teaching_plan_rejects_bad_assistance_level() -> None:
    with pytest.raises(ValidationError):
        TeachingPlan(
            difficulty="easy",
            assistance_level="answer",  # type: ignore[arg-type]
            solution_strategy="socratic_hints",
            skill_level=0.5,
        )


def test_teaching_plan_rejects_too_many_watch_errors() -> None:
    with pytest.raises(ValidationError):
        TeachingPlan(
            difficulty="easy",
            assistance_level="hint",
            solution_strategy="socratic_hints",
            skill_level=0.5,
            watch_errors=["a", "b", "c", "d", "e", "f"],
        )


def test_assistance_order_matches_literal_args() -> None:
    assert get_args(AssistanceLevel) == ASSISTANCE_ORDER


async def test_langgraph_smoke_state_roundtrip() -> None:
    """A minimal two-node graph proves `AgentState`/`GraphContext` work with
    langgraph 1.2.12: `errors` accumulates via the `operator.add` reducer and
    the raw result dict re-validates as `AgentState`."""

    async def node_a(state: AgentState, runtime: Runtime[GraphContext]) -> AgentStateUpdate:
        del state, runtime
        return {"errors": [NodeError(node="node_a", error_type="RuntimeError", message="boom")]}

    async def node_b(state: AgentState, runtime: Runtime[GraphContext]) -> AgentStateUpdate:
        del state, runtime
        return {
            "errors": [NodeError(node="node_b", error_type="ValueError", message="also boom")],
            "response": "done",
        }

    builder = StateGraph(AgentState, context_schema=GraphContext)
    builder.add_node("node_a", node_a)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("node_b", node_b)  # pyright: ignore[reportUnknownMemberType]
    builder.add_edge(START, "node_a")
    builder.add_edge("node_a", "node_b")
    builder.add_edge("node_b", END)
    graph = builder.compile()  # pyright: ignore[reportUnknownMemberType]

    result = await graph.ainvoke(  # pyright: ignore[reportUnknownMemberType]
        AgentState(input=RawInput(text="hi")),
        context=GraphContext(llm=FakeLLMClient()),
    )

    assert len(result["errors"]) == 2
    validated = AgentState.model_validate(result)
    assert validated.response == "done"
    assert [e.node for e in validated.errors] == ["node_a", "node_b"]


def test_agent_state_update_annotations_are_not_annotated_reducers() -> None:
    """`AgentStateUpdate` fields carry the same value types but no `Annotated`
    reducer metadata (it's a plain TypedDict for return-type checking)."""
    hints = get_type_hints(AgentStateUpdate)
    assert "events" in hints
    assert "errors" in hints
