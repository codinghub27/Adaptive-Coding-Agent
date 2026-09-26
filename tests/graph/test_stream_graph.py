"""Tests for `app.graph.build.stream_graph`."""

from app.graph.build import GraphResultEvent, GraphStageEvent, run_graph, stream_graph
from app.graph.state import RawInput
from tests.input.fakes import FakeLLMClient

_DEBUG_TEXT = (
    "```python\n"
    "def get_item(items, idx):\n"
    "    return items[idx]\n"
    "```\n"
    "\n"
    "IndexError: list index out of range\n"
)


async def test_stream_graph_emits_stage_events_in_graph_order_and_one_result() -> None:
    fake = FakeLLMClient()

    events = [event async for event in stream_graph(RawInput(text=_DEBUG_TEXT), llm=fake)]

    stage_events = [e for e in events if isinstance(e, GraphStageEvent)]
    result_events = [e for e in events if isinstance(e, GraphResultEvent)]

    assert len(result_events) == 1
    node_names = [e.node for e in stage_events]
    assert node_names[0] == "understand_input"
    assert node_names[-1] == "update_learner_model"
    # The result event is always terminal, arriving after every stage event.
    assert events[-1] is result_events[0]


async def test_stream_graph_final_state_matches_run_graph_for_the_same_input() -> None:
    fake_stream = FakeLLMClient()
    fake_run = FakeLLMClient()

    events = [
        event async for event in stream_graph(RawInput(text=_DEBUG_TEXT), llm=fake_stream)
    ]
    result_event = next(e for e in events if isinstance(e, GraphResultEvent))

    run_result = await run_graph(RawInput(text=_DEBUG_TEXT), llm=fake_run)

    assert result_event.result.state.response == run_result.state.response
