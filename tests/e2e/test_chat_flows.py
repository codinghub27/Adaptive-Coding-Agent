"""End-to-end tests: full-stack turns through the real FastAPI app over HTTP.

These drive `POST /chat` / `POST /chat/stream` exactly as a real client would
(`httpx.AsyncClient` over an ASGI transport built by `app.main.create_app`),
covering the Phase 08 response layer end to end: hint-ladder gating, a real
sandbox-verified debug turn, an explain turn, streaming parity with the
non-streaming endpoint, and graceful degradation when the sandbox/retriever
are unavailable.

Reuses the app-construction and test-double patterns already established by
`tests/graph/test_chat_api.py` (`_client_for`, `_StubSession`/`_stub_session`,
`FakeLLMClient`), `tests/graph/test_chat_stream_api.py` (the SSE frame
plumbing), and `tests/graph/test_phase7_manual.py` (`_RoutedLLM`, for
scenarios needing more than one distinct canned LLM response per run) rather
than inventing new test doubles.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from docker.errors import DockerException, ImageNotFound
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.config import Settings
from app.db.session import get_session
from app.execution.runner import SandboxRunner, build_sandbox_runner
from app.execution.sandbox import SANDBOX_LABEL, make_docker_client
from app.input.api import get_llm
from app.main import create_app
from app.memory.conversation import start_conversation
from app.memory.profile import set_learning_preferences
from app.schemas.auth import AuthUser
from tests.graph.test_chat_api import (
    _DEFAULT_TEST_USER,  # pyright: ignore[reportPrivateUsage]
    _client_for,  # pyright: ignore[reportPrivateUsage]
    _stub_session,  # pyright: ignore[reportPrivateUsage]
)
from tests.graph.test_chat_stream_api import (
    _build_app,  # pyright: ignore[reportPrivateUsage]
    _collect_sse_frames,  # pyright: ignore[reportPrivateUsage]
    _FakeSession,  # pyright: ignore[reportPrivateUsage]
    _FakeSessionFactory,  # pyright: ignore[reportPrivateUsage]
)
from tests.graph.test_phase7_manual import _RoutedLLM  # pyright: ignore[reportPrivateUsage]
from tests.input.fakes import FakeLLMClient

MakeSettings = Any

# ---------------------------------------------------------------------------
# Case 1: DSA hint turn -- the teaching invariant, end to end (needs DB for
# the hint-ladder store, keyed on conversation + topic).
# ---------------------------------------------------------------------------

_TWO_POINTERS_PROBLEM = (
    "Given a sorted array of integers nums and an integer target, return the "
    "indices of the two numbers whose values sum exactly to target. Each "
    "input has exactly one valid answer, and you may not reuse the same "
    "array element twice. Aim for a solution that uses O(n) time and O(1) "
    "extra space beyond the output pair."
)


@pytest.mark.db
async def test_dsa_hint_turn_climbs_ladder_across_turns_and_never_leaks_code(
    make_settings: MakeSettings, db_session: AsyncSession, user_id: UUID
) -> None:
    """A learner who prefers hints gets a hint-only turn whose ladder climbs
    one rung per ask on the same conversation+topic, holding at the "hint"
    assistance level's ceiling (`HintLevel.L2_DATA_STRUCTURE`) -- never a
    runnable solution body, at any point."""
    await set_learning_preferences(db_session, user_id, {"prefers_hints": True})
    conversation_id = await start_conversation(db_session, user_id)

    async def session_override() -> AsyncIterator[AsyncSession]:
        yield db_session

    current_user = AuthUser(id=user_id, handle="test-user", session_id=uuid4())
    fake = FakeLLMClient(chat_content="{}")

    levels: list[int] = []
    for _ in range(4):
        async with _client_for(
            make_settings, fake, session_override, current_user=current_user
        ) as client:
            response = await client.post(
                "/chat",
                data={
                    "text": _TWO_POINTERS_PROBLEM,
                    "conversation_id": str(conversation_id),
                    "topic": "two_pointers",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["route"] == "dsa"
        generated = body["generated"]
        assert generated is not None
        assert generated["hint_level"] is not None
        assert generated["reveals_code"] is False
        assert all(section["kind"] not in ("code", "patch") for section in generated["sections"])
        assert body["response"] != ""
        assert "def " not in body["response"]
        levels.append(generated["hint_level"])

    # Strictly climbs for the first three asks, then holds at the ceiling.
    assert levels[0] < levels[1] < levels[2]
    assert levels[2] == levels[3]
    assert max(levels) == 2  # HintLevel.L2_DATA_STRUCTURE: the "hint" assistance ceiling
    assert levels[3] <= 2  # never exceeds the ceiling


# ---------------------------------------------------------------------------
# Case 2: debug turn, sandbox-verified -- the dead-before-this-phase path
# where `extract_test_suite` derives a suite from worked examples.
# ---------------------------------------------------------------------------

_DEBUG_WITH_EXAMPLES_TEXT = (
    "Given a non-empty list of integers, return its last element.\n\n"
    "Example 1:\n"
    "Input: [1, 2, 3]\n"
    "Output: 3\n\n"
    "Example 2:\n"
    "Input: [5]\n"
    "Output: 5\n\n"
    "```python\n"
    "def get_last(items):\n"
    "    return items[len(items)]\n"
    "```\n"
    "\n"
    "IndexError: list index out of range\n"
)

_DEBUG_INTENT_JSON = json.dumps(
    {"intent": "CODE_DEBUG", "confidence": 0.95, "rationale": "buggy code with a real error"}
)
_INFER_APPROACH_JSON = json.dumps(
    {"inferred_approach": "computing the last index and indexing directly into the list"}
)
_EXPLAIN_BUG_JSON = json.dumps(
    {
        "bug_explanation": (
            "len(items) is one past the last valid index, so indexing with it raises "
            "an IndexError instead of returning the last element."
        )
    }
)
_PATCHED_CODE_JSON = json.dumps(
    {"patched_code": "def get_last(items):\n    return items[len(items) - 1]\n"}
)


def _skip_unless_docker_available(settings: Settings) -> None:
    try:
        client = make_docker_client()
        if not client.ping():
            pytest.skip("docker engine not reachable")
        client.images.get(settings.sandbox_image)
        client.close()
    except DockerException as exc:
        if isinstance(exc, ImageNotFound):
            pytest.skip(f"sandbox image {settings.sandbox_image!r} not built locally")
        pytest.skip(f"docker engine not usable: {type(exc).__name__}")


def _no_sandbox_containers_remain() -> bool:
    client = make_docker_client()
    try:
        remaining = client.containers.list(all=True, filters={"label": f"{SANDBOX_LABEL}=1"})
        return len(remaining) == 0
    finally:
        client.close()


@pytest.mark.sandbox
async def test_debug_turn_reaches_real_sandbox_and_never_overclaims_correctness(
    make_settings: MakeSettings,
) -> None:
    """Code with a real bug, plus a problem statement carrying worked
    examples, drives `debug_agent` through `extract_test_suite` and a real
    Docker sandbox run -- this is the path that was dead before this phase
    (no `TestSuite` ever reached the sandbox on a debug turn)."""
    settings = make_settings(sandbox_enabled=True)
    _skip_unless_docker_available(settings)

    runner_obj, close_runner = await build_sandbox_runner(settings)
    if runner_obj is None:
        pytest.skip("sandbox runner unavailable at startup")
    runner: SandboxRunner = runner_obj

    fake = _RoutedLLM(
        routes=[
            ("intent classifier", _DEBUG_INTENT_JSON),
            ("infer, in one or two sentences", _INFER_APPROACH_JSON),
            ("why the bug happens", _EXPLAIN_BUG_JSON),
            ("Produce a corrected", _PATCHED_CODE_JSON),
        ]
    )

    app = create_app(settings)
    app.state.runner = runner
    app.dependency_overrides[get_llm] = lambda: fake
    app.dependency_overrides[get_session] = _stub_session
    app.dependency_overrides[get_current_user] = lambda: _DEFAULT_TEST_USER
    transport = httpx.ASGITransport(app=app)

    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/chat", data={"text": _DEBUG_WITH_EXAMPLES_TEXT})
    finally:
        close_runner()

    assert response.status_code == 200
    body = response.json()
    assert body["route"] == "debug"

    # The turn actually reached the sandbox -- not "skipped" (the regression
    # this case exists to catch: a debug turn with no test suite reaching the
    # sandbox at all).
    verification = body["verification"]
    assert verification is not None
    assert verification["status"] != "skipped"

    generated = body["generated"]
    assert generated is not None
    assert any(section["kind"] == "bug_explanation" for section in generated["sections"])

    # Correctness is never claimed without a passing verdict: the only
    # section that could claim a fix worked is a "patch" section, gated on
    # `assistance_level == "full"` (never reached by a debug turn's default
    # "hint" assistance) -- so it must never appear unless the run truly
    # passed.
    if verification["status"] != "pass":
        assert not any(section["kind"] == "patch" for section in generated["sections"])

    assert _no_sandbox_containers_remain()


# ---------------------------------------------------------------------------
# Case 3: explain turn -- the Phase 07 blank-reply regression (a `None`
# `complexity_rationale` must not produce an empty reply).
# ---------------------------------------------------------------------------

_TWO_SUM_CODE = (
    "def two_sum(nums, target):\n"
    "    seen = {}\n"
    "    for i, n in enumerate(nums):\n"
    "        complement = target - n\n"
    "        if complement in seen:\n"
    "            return [seen[complement], i]\n"
    "        seen[n] = i\n"
    "    return []\n"
)

_EXPLAIN_TEXT = f"```python\n{_TWO_SUM_CODE}```\n\nCan you explain what this function does?"

_EXPLAIN_INTENT_JSON = json.dumps(
    {"intent": "CODE_EXPLAIN", "confidence": 0.9, "rationale": "asks what code does"}
)
_LINE_EXPLANATIONS_JSON = json.dumps(
    {
        "line_explanations": [
            {"lineno": i, "explanation": f"Explanation for line {i}."} for i in range(1, 9)
        ]
    }
)


async def test_explain_turn_renders_structure_and_lines_even_without_a_rationale(
    make_settings: MakeSettings,
) -> None:
    fake = _RoutedLLM(
        routes=[
            ("intent classifier", _EXPLAIN_INTENT_JSON),
            ("For each numbered line shown", _LINE_EXPLANATIONS_JSON),
        ],
        # The complexity-rationale call falls through to this: unparseable,
        # so `complexity_rationale` degrades to `None` -- the exact Phase 07
        # regression this case guards against (a `None` rationale must not
        # blank out the whole reply).
        default="not valid json at all",
    )

    async with _client_for(make_settings, fake) as client:
        response = await client.post("/chat", data={"text": _EXPLAIN_TEXT})

    assert response.status_code == 200
    body = response.json()
    assert body["route"] == "explain"
    generated = body["generated"]
    assert generated is not None
    assert body["response"] != ""

    kinds = {section["kind"] for section in generated["sections"]}
    assert "structure" in kinds
    assert "line_by_line" in kinds


# ---------------------------------------------------------------------------
# Case 4: streaming parity -- stage frames never leak the submitted problem
# text, exactly one terminal frame, and structural parity with `POST /chat`.
# ---------------------------------------------------------------------------


async def test_chat_stream_parity_with_plain_chat_and_no_leaked_problem_text(
    make_settings: MakeSettings,
) -> None:
    marker = "zzz_e2e_distinctive_marker_qux_98765"
    text = f"{_TWO_POINTERS_PROBLEM} ({marker})"
    fake_stream = FakeLLMClient(chat_content="{}")

    transport_stream = _build_app(
        make_settings, fake_stream, session_factory=_FakeSessionFactory(_FakeSession())
    )
    async with httpx.AsyncClient(transport=transport_stream, base_url="http://test") as client:
        stream_response = await client.post("/chat/stream", data={"text": text})

    assert stream_response.status_code == 200
    frames = await _collect_sse_frames(stream_response)

    stage_frames = [data for event, data in frames if event == "stage"]
    done_frames = [data for event, data in frames if event == "done"]
    error_frames = [data for event, data in frames if event == "error"]

    assert stage_frames  # at least one stage frame arrived
    assert len(done_frames) + len(error_frames) == 1  # exactly one terminal frame
    assert error_frames == []
    for frame_data in stage_frames:
        assert marker not in frame_data

    streamed_body = json.loads(done_frames[0])

    # A fresh conversation via plain `POST /chat`, same input, separate app/
    # session/LLM instance -- hint-ladder state (if any) is per-conversation,
    # so this compares structural shape rather than byte-identical text.
    fake_plain = FakeLLMClient(chat_content="{}")
    transport_plain = _build_app(make_settings, fake_plain)
    async with httpx.AsyncClient(transport=transport_plain, base_url="http://test") as client:
        plain_response = await client.post("/chat", data={"text": text})

    assert plain_response.status_code == 200
    plain_body = plain_response.json()

    assert streamed_body["route"] == plain_body["route"]
    assert streamed_body["generated"]["reveals_code"] == plain_body["generated"]["reveals_code"]
    streamed_kinds = sorted(s["kind"] for s in streamed_body["generated"]["sections"])
    plain_kinds = sorted(s["kind"] for s in plain_body["generated"]["sections"])
    assert streamed_kinds == plain_kinds


# ---------------------------------------------------------------------------
# Case 5: degraded paths never break a turn -- no sandbox, no retriever.
# ---------------------------------------------------------------------------

_DEGRADED_DEBUG_TEXT = (
    "```python\n"
    "def get_item(items, idx):\n"
    "    return items[idx]\n"
    "```\n"
    "\n"
    "IndexError: list index out of range\n"
)


async def test_debug_turn_degrades_gracefully_with_no_sandbox_and_no_retriever(
    make_settings: MakeSettings,
) -> None:
    """No `app.state.runner`, no `app.state.retriever` configured (the
    default for an app built without its lifespan) -- the agent must degrade
    to a "skipped" verdict and a non-empty, non-overclaiming reply, never a
    500."""
    fake = FakeLLMClient(chat_content="unused")
    async with _client_for(make_settings, fake) as client:
        response = await client.post("/chat", data={"text": _DEGRADED_DEBUG_TEXT})

    assert response.status_code == 200
    body = response.json()
    assert body["route"] == "debug"
    assert body["response"] != ""

    verification = body["verification"]
    assert verification is None or verification["status"] != "pass"

    generated = body["generated"]
    assert generated is not None
    assert not any(section["kind"] == "patch" for section in generated["sections"])
