"""Tests for the LangSmith parent-run tracing `app.graph.build.run_graph` adds
via its `tracer` keyword (PACKET P-LS).

Uses the same `Tracer(enabled=True, client=MagicMock(spec=Client), ...)`
double as `tests/llm/test_client.py`'s `Tracer.run` tests -- a real `Tracer`
backed by a mocked `langsmith.Client`, never a real network call.
"""

import json
from typing import cast
from unittest.mock import MagicMock

from langsmith import Client as LangSmithClient

from app.graph.build import GraphResultEvent, GraphStageEvent, run_graph, stream_graph
from app.graph.state import RawInput
from app.llm.client import Tracer, redact_trace_payload
from tests.input.fakes import FakeLLMClient

#: Deterministic debug-route input: code + traceback, no topic hint, so
#: (per `test_build.py`/`test_chat_api.py`) it resolves with zero LLM calls
#: and no learning event -- nothing here is randomly generated per-run, so
#: two separate `run_graph` calls with this input produce byte-identical
#: state.
_DEBUG_TEXT = (
    "```python\n"
    "def get_item(items, idx):\n"
    "    return items[idx]\n"
    "```\n"
    "\n"
    "IndexError: list index out of range\n"
)

#: A distinctive sentinel embedded in otherwise-identical debug-route input,
#: used only to prove the sentinel never reaches the trace payload.
_SENTINEL = "zzqxflarp_9182_do_not_leak_this_learner_text"
_SENTINEL_DEBUG_TEXT = (
    "```python\n"
    f"def get_item(items, idx):  # {_SENTINEL}\n"
    "    return items[idx]\n"
    "```\n"
    "\n"
    f"IndexError: {_SENTINEL} list index out of range\n"
)

_EXPECTED_INPUT_KEYS = {
    "has_text",
    "has_image",
    "language",
    "user_id",
    "conversation_id",
    "max_llm_calls",
}
_EXPECTED_OUTPUT_KEYS = {
    "route",
    "intent",
    "topic",
    "verification_status",
    "error_count",
    "event_count",
    "llm_calls",
    # ADAPTIVE-upgrade P0: per-turn token + cost accounting rides on the root
    # run (numbers only, allow-listed in `_ALLOWED_TRACE_METADATA_KEYS`).
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cost_usd",
    "priced_calls",
}


def _mock_langsmith_client() -> LangSmithClient:
    """A `MagicMock(spec=Client)` standing in for a real `langsmith.Client`:
    `create_run`/`update_run` calls are recorded, never sent over the network."""
    return cast(LangSmithClient, MagicMock(spec=LangSmithClient))


def _kwargs_for_run_named(mock: MagicMock, name: str) -> dict[str, object]:
    """The `kwargs` of the one call to `mock` (a `create_run`/`update_run`
    mock) whose own `name="..."` kwarg matches `name`.

    LangGraph auto-instruments a run per node once the ambient LangSmith
    tracing context is open (that's what makes the node tree nest under the
    `teaching_graph` parent), so `mock.call_args_list` holds many calls, not
    just this run's own -- `mock.call_args` (the *last* call) is not
    necessarily `"teaching_graph"`'s.
    """
    matches = [c.kwargs for c in mock.call_args_list if c.kwargs.get("name") == name]
    assert len(matches) == 1, f"expected exactly one {name!r} run, found {len(matches)}"
    return cast("dict[str, object]", matches[0])


async def test_disabled_tracer_is_a_true_noop_and_result_is_unchanged() -> None:
    """`tracer=None` and an explicit `Tracer.disabled()` must behave
    identically -- both a complete no-op that never touches LangSmith and
    never changes the graph's own result."""
    without_tracer = await run_graph(RawInput(text=_DEBUG_TEXT), llm=FakeLLMClient())
    with_disabled_tracer = await run_graph(
        RawInput(text=_DEBUG_TEXT), llm=FakeLLMClient(), tracer=Tracer.disabled()
    )

    assert without_tracer.llm_calls == with_disabled_tracer.llm_calls == 0
    assert without_tracer.state.route == with_disabled_tracer.state.route == "debug"
    assert without_tracer.state.model_dump() == with_disabled_tracer.state.model_dump()


async def test_parent_run_receives_the_expected_metadata_keys() -> None:
    mock_client = _mock_langsmith_client()
    tracer = Tracer(enabled=True, client=mock_client, project_name="test-project")

    result = await run_graph(RawInput(text=_DEBUG_TEXT), llm=FakeLLMClient(), tracer=tracer)

    assert result.state.route == "debug"

    create_run = cast(MagicMock, mock_client.create_run)  # pyright: ignore[reportAttributeAccessIssue]
    create_kwargs = _kwargs_for_run_named(create_run, "teaching_graph")
    assert create_kwargs["run_type"] == "chain"
    assert set(cast("dict[str, object]", create_kwargs["inputs"])) == _EXPECTED_INPUT_KEYS

    update_run = cast(MagicMock, mock_client.update_run)  # pyright: ignore[reportUnknownMemberType]
    update_kwargs = _kwargs_for_run_named(update_run, "teaching_graph")
    assert set(cast("dict[str, object]", update_kwargs["outputs"])) == _EXPECTED_OUTPUT_KEYS


async def test_trace_payload_never_carries_learner_text() -> None:
    """Guard test for the owner decision that the trace carries metadata only
    -- ids, flags, counts, enum-ish values -- and never the learner's raw
    text/code/error content. `_SENTINEL_DEBUG_TEXT` plants a distinctive
    string inside the turn's code/traceback and this asserts it appears
    nowhere in either payload handed to the tracer."""
    mock_client = _mock_langsmith_client()
    tracer = Tracer(enabled=True, client=mock_client, project_name="test-project")

    result = await run_graph(
        RawInput(text=_SENTINEL_DEBUG_TEXT), llm=FakeLLMClient(), tracer=tracer
    )

    assert result.state.route == "debug"

    create_run = cast(MagicMock, mock_client.create_run)  # pyright: ignore[reportAttributeAccessIssue]
    update_run = cast(MagicMock, mock_client.update_run)  # pyright: ignore[reportUnknownMemberType]

    create_kwargs = _kwargs_for_run_named(create_run, "teaching_graph")
    update_kwargs = _kwargs_for_run_named(update_run, "teaching_graph")

    inputs_payload = json.dumps(create_kwargs["inputs"])
    outputs_payload = json.dumps(update_kwargs["outputs"])

    assert _SENTINEL not in inputs_payload
    assert _SENTINEL not in outputs_payload


async def test_stream_graph_creates_exactly_one_parent_run_with_usage_metadata() -> None:
    """B6: `/chat/stream` used to produce no parent run at all (the `tracer`
    argument was accepted and ignored), so the path the UI uses was untraced.
    It must now open the same single `teaching_graph` root `run_graph` does,
    with node runs nested under it, and still stream stage events."""
    mock_client = _mock_langsmith_client()
    tracer = Tracer(enabled=True, client=mock_client, project_name="test-project")

    stages: list[str] = []
    results: list[GraphResultEvent] = []
    async for event in stream_graph(
        RawInput(text=_SENTINEL_DEBUG_TEXT), llm=FakeLLMClient(), tracer=tracer
    ):
        if isinstance(event, GraphStageEvent):
            stages.append(event.node)
        else:
            results.append(event)

    assert len(results) == 1
    assert results[0].result.state.route == "debug"
    assert stages[0] == "understand_input"
    assert stages[-1] == "update_learner_model"

    create_run = cast(MagicMock, mock_client.create_run)  # pyright: ignore[reportAttributeAccessIssue]
    update_run = cast(MagicMock, mock_client.update_run)  # pyright: ignore[reportUnknownMemberType]
    create_kwargs = _kwargs_for_run_named(create_run, "teaching_graph")
    update_kwargs = _kwargs_for_run_named(update_run, "teaching_graph")
    assert set(cast("dict[str, object]", create_kwargs["inputs"])) == _EXPECTED_INPUT_KEYS
    assert set(cast("dict[str, object]", update_kwargs["outputs"])) == _EXPECTED_OUTPUT_KEYS

    # Node runs nest under the root rather than arriving as separate roots.
    root_id = create_kwargs["id"]
    nested = [c.kwargs for c in create_run.call_args_list if c.kwargs.get("parent_run_id")]
    assert nested, "no node run was nested under the teaching_graph root"
    roots = [c.kwargs for c in create_run.call_args_list if not c.kwargs.get("parent_run_id")]
    assert [r["id"] for r in roots] == [root_id]

    # Node runs reach the client with raw state; the real client passes every
    # payload through `hide_inputs=redact_trace_payload` (wired in
    # `Tracer.from_settings`) before sending. Apply that same function here.
    payload = json.dumps(
        [redact_trace_payload(c.kwargs.get("inputs")) for c in create_run.call_args_list],
        default=str,
    )
    assert _SENTINEL not in payload


async def test_stream_graph_without_tracer_still_streams() -> None:
    events = [e async for e in stream_graph(RawInput(text=_DEBUG_TEXT), llm=FakeLLMClient())]
    assert isinstance(events[-1], GraphResultEvent)
    assert sum(isinstance(e, GraphResultEvent) for e in events) == 1


async def test_closing_stream_graph_early_cancels_the_graph_task() -> None:
    """A client disconnect closes the stream after the first stage; closing must
    cancel the background graph task rather than leave it running on a session
    the caller is about to close (code-review finding on P0)."""
    import asyncio

    before = {t for t in asyncio.all_tasks() if not t.done()}
    stream = stream_graph(RawInput(text=_DEBUG_TEXT), llm=FakeLLMClient())
    first = await anext(stream)
    assert isinstance(first, GraphStageEvent)
    await stream.aclose()
    leftover = {t for t in asyncio.all_tasks() if not t.done()} - before
    leftover.discard(asyncio.current_task())  # pyright: ignore[reportArgumentType]
    assert leftover == set()
