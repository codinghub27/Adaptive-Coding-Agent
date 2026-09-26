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

from app.graph.build import run_graph
from app.graph.state import RawInput
from app.llm.client import Tracer
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
