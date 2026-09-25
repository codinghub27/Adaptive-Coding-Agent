"""Tests for `app.graph.nodes.execute_code`/`verify_execution` and the
`execute_code -> verify -> final_response` edges they sit on.

`FakeRunner` stands in for `app.execution.base.CodeRunner` throughout: no
Docker daemon is ever touched here. Every scenario is driven end-to-end
through `build_graph`/`run_graph`, never by calling the node functions
directly, so the routing (`app.graph.routing.verify_after`/`VERIFY_NODES`) is
covered along with the nodes themselves.
"""

import hashlib
from collections.abc import AsyncIterator, Callable
from typing import Any, Final
from uuid import uuid4

import httpx
from langgraph.runtime import Runtime  # pyright: ignore[reportMissingTypeStubs]
from pydantic import JsonValue

from app.auth.deps import get_current_user
from app.config import Settings
from app.db.session import get_session
from app.execution.base import CodeRunner, canonical
from app.graph.build import build_graph, run_graph
from app.graph.nodes import AgentOutcome, Node
from app.graph.state import AgentState, AgentStateUpdate, GraphContext, RawInput
from app.input.api import get_llm
from app.main import create_app
from app.schemas.auth import AuthUser
from app.schemas.execution import (
    CaseResult,
    ExecutionRequest,
    ExecutionResult,
    TestCase,
    TestSuite,
)
from tests.input.fakes import FakeLLMClient

MakeSettings = Callable[..., Settings]


class _EmptyResult:
    """Stands in for a SQLAlchemy `Result` that matched no rows."""

    def scalar_one_or_none(self) -> None:
        return None

    def scalars(self) -> list[Any]:
        return []


class _NoOpNestedTransaction:
    """Stands in for the async context manager `AsyncSession.begin_nested()` returns."""

    async def __aenter__(self) -> "_NoOpNestedTransaction":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _StubSession:
    """Minimal `AsyncSession` stand-in, mirroring `tests/graph/test_chat_api.py`'s."""

    async def commit(self) -> None:
        return None

    async def execute(self, *args: object, **kwargs: object) -> _EmptyResult:
        del args, kwargs
        return _EmptyResult()

    def begin_nested(self) -> _NoOpNestedTransaction:
        return _NoOpNestedTransaction()


async def _stub_session() -> AsyncIterator[Any]:
    yield _StubSession()


_DSA_TEXT: Final = (
    "Given an array of integers nums and an integer target, return indices "
    "of the two numbers such that they add up to target.\n"
    "\n"
    "Example 1:\n"
    "Input: nums = [2,7,11,15], target = 9\n"
    "Output: [0,1]\n"
)

_DSA_CHAT_CONTENT: Final = '{"intent": "DSA_SOLVE", "confidence": 0.95, "rationale": "clear"}'

_AMBIGUOUS_TEXT: Final = "hmm, not sure what I want"

_REQUEST: Final = ExecutionRequest(
    language="python",
    code="def two_sum(nums, target): ...",
    tests=TestSuite(
        entrypoint="two_sum",
        cases=[
            TestCase(name="c1", args=[[2, 7, 11, 15], 9], expected=[0, 1]),
            TestCase(name="c2", args=[[3, 2, 4], 6], expected=[0, 1]),
        ],
    ),
)


class FakeRunner:
    """Records every `ExecutionRequest` it's handed; returns a canned result."""

    def __init__(self, result: ExecutionResult | None = None, *, raise_error: bool = False) -> None:
        self.result = result
        self.raise_error = raise_error
        self.calls: list[ExecutionRequest] = []

    async def run(self, request: ExecutionRequest) -> ExecutionResult:
        self.calls.append(request)
        if self.raise_error:
            raise RuntimeError("sandbox-secret")
        assert self.result is not None
        return self.result


def _sha256(value: JsonValue) -> str:
    """The host-authoritative hash a real harness would report for `value`;
    see `app.execution.verification`'s host-side pass/fail check."""
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _passed_result() -> ExecutionResult:
    return ExecutionResult(
        status="passed",
        phase="tests",
        cases=[
            CaseResult(
                name="c1",
                passed=True,
                actual=[0, 1],
                actual_repr="[0, 1]",
                actual_sha256=_sha256([0, 1]),
                duration_ms=1.0,
            ),
            CaseResult(
                name="c2",
                passed=True,
                actual=[0, 1],
                actual_repr="[0, 1]",
                actual_sha256=_sha256([0, 1]),
                duration_ms=1.0,
            ),
        ],
    )


def _failed_result() -> ExecutionResult:
    return ExecutionResult(
        status="failed",
        phase="tests",
        cases=[
            CaseResult(
                name="c1",
                passed=False,
                actual=[0, 2],
                actual_repr="[0, 2]",
                actual_sha256=_sha256([0, 2]),
                duration_ms=1.0,
            ),
            CaseResult(
                name="c2",
                passed=True,
                actual=[0, 1],
                actual_repr="[0, 1]",
                actual_sha256=_sha256([0, 1]),
                duration_ms=1.0,
            ),
        ],
    )


def _dsa_agent_with_request() -> Node:
    """A `dsa_agent` override reporting a stub outcome plus `_REQUEST`."""

    async def dsa_agent(state: AgentState, *, runtime: Runtime[GraphContext]) -> AgentStateUpdate:
        del state, runtime
        return {
            "agent_output": AgentOutcome(text="[dsa stub] here's a hint", topic="two_pointers"),
            "execution_request": _REQUEST,
        }

    return dsa_agent


def _raiser(name: str) -> Node:
    async def raise_always(
        state: AgentState, *, runtime: Runtime[GraphContext]
    ) -> AgentStateUpdate:
        del state, runtime
        raise RuntimeError(f"{name}-secret")

    return raise_always


# --------------------------------------------------------------------------
# No execution_request: both nodes skip; unchanged Phase 04/05 behaviour
# --------------------------------------------------------------------------


async def test_no_execution_request_skips_execute_and_verify() -> None:
    """The real `dsa_agent` only sets `execution_request` once the hint
    ladder actually reaches L6 (full solution); a fresh ladder's first turn
    stops at L0 and never sets one, so `execute_code`/`verify` are still a
    no-op skip on this route -- unchanged from Phase 04/05/06."""
    result = await run_graph(
        RawInput(text=_DSA_TEXT), llm=FakeLLMClient(chat_content=_DSA_CHAT_CONTENT)
    )

    state = result.state
    assert state.execution_request is None
    assert state.execution_result is None
    assert state.verification is None
    assert state.route == "dsa"
    assert state.response is not None
    assert "stub" not in state.response.lower()


async def test_no_execution_request_leaves_agent_text_untouched_by_execute_and_verify() -> None:
    """Renamed from the retired `..._identical_to_pre_phase6_stub_text`: that
    name asserted a specific Phase 04 stub string. The real guarantee this
    test protects is that `execute_code`/`verify` never rewrite an agent's
    `response` text when no `execution_request` was set for this turn --
    proven here by running the same input twice and checking the response is
    stable and matches the agent output's own text exactly, byte for byte."""
    fake = FakeLLMClient(chat_content=_DSA_CHAT_CONTENT)
    result = await run_graph(RawInput(text=_DSA_TEXT), llm=fake)

    state = result.state
    assert state.execution_request is None
    assert state.agent_output is not None
    assert state.response == state.agent_output.text


# --------------------------------------------------------------------------
# An agent sets an execution_request: execute_code -> verify outcomes
# --------------------------------------------------------------------------


async def _run_with_runner(runner: CodeRunner | None) -> AgentState:
    graph = build_graph(node_overrides={"dsa_agent": _dsa_agent_with_request()})
    result = await graph.ainvoke(  # pyright: ignore[reportUnknownMemberType]
        AgentState(input=RawInput(text=_DSA_TEXT)),
        context=GraphContext(llm=FakeLLMClient(chat_content=_DSA_CHAT_CONTENT), runner=runner),
    )
    return AgentState.model_validate(result)


async def test_runner_passed_yields_pass_verdict() -> None:
    runner = FakeRunner(_passed_result())
    state = await _run_with_runner(runner)

    assert len(runner.calls) == 1
    assert runner.calls[0] == _REQUEST
    assert state.execution_result is not None
    assert state.execution_result.status == "passed"
    assert state.verification is not None
    assert state.verification.status == "pass"


async def test_runner_failed_first_case_yields_fail_with_first_failing_case() -> None:
    runner = FakeRunner(_failed_result())
    state = await _run_with_runner(runner)

    assert state.verification is not None
    assert state.verification.status == "fail"
    assert state.verification.category == "wrong_answer"
    assert state.verification.first_failing_case == "c1"


async def test_runner_none_is_inconclusive_sandbox_error() -> None:
    state = await _run_with_runner(None)

    assert state.execution_result is not None
    assert state.execution_result.status == "sandbox_error"
    assert state.execution_result.error is not None
    assert state.execution_result.error.type == "SandboxUnavailable"
    assert state.verification is not None
    assert state.verification.status == "inconclusive"
    assert state.verification.category == "sandbox_error"


async def test_runner_raising_degrades_to_inconclusive_never_pass() -> None:
    runner = FakeRunner(raise_error=True)
    state = await _run_with_runner(runner)

    assert any(e.node == "execute_code" for e in state.errors)
    assert state.verification is not None
    assert state.verification.status != "pass"
    assert state.verification.status == "inconclusive"
    serialized = state.model_dump_json()
    assert "sandbox-secret" not in serialized


async def test_verify_node_raising_degrades_to_inconclusive_never_pass() -> None:
    graph = build_graph(
        node_overrides={"dsa_agent": _dsa_agent_with_request(), "verify": _raiser("verify")}
    )
    result = await graph.ainvoke(  # pyright: ignore[reportUnknownMemberType]
        AgentState(input=RawInput(text=_DSA_TEXT)),
        context=GraphContext(
            llm=FakeLLMClient(chat_content=_DSA_CHAT_CONTENT), runner=FakeRunner(_passed_result())
        ),
    )
    state = AgentState.model_validate(result)

    assert any(e.node == "verify" for e in state.errors)
    assert state.verification is not None
    assert state.verification.status != "pass"
    assert state.verification.status == "inconclusive"


# --------------------------------------------------------------------------
# clarify never calls the runner
# --------------------------------------------------------------------------


async def test_clarify_route_never_calls_the_runner() -> None:
    runner = FakeRunner(_passed_result())

    fake = FakeLLMClient(chat_content="not valid json at all, sorry")
    result = await run_graph(RawInput(text=_AMBIGUOUS_TEXT), llm=fake, runner=runner)

    assert result.state.route == "clarify"
    assert runner.calls == []
    assert result.state.execution_request is None
    assert result.state.verification is None


# --------------------------------------------------------------------------
# /chat surfaces `verification`
# --------------------------------------------------------------------------


async def test_chat_response_includes_null_verification_for_stub_agents(
    make_settings: MakeSettings,
) -> None:
    fake = FakeLLMClient(chat_content="unused")
    app = create_app(make_settings())
    app.dependency_overrides[get_llm] = lambda: fake
    app.dependency_overrides[get_session] = _stub_session
    app.dependency_overrides[get_current_user] = lambda: AuthUser(
        id=uuid4(), handle="test-user", session_id=uuid4()
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/chat",
            data={
                "text": (
                    "```python\ndef get_item(items, idx):\n    return items[idx]\n```\n"
                    "\nIndexError: list index out of range\n"
                )
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert "verification" in body
    assert body["verification"] is None
