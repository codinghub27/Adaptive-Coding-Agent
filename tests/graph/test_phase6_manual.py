"""Phase 6 manual test case, run end-to-end through the real Docker sandbox.

Mirrors `tests/graph/test_phase4_manual.py`'s "print an ACTUAL: line" style,
but drives the graph's `execute_code`/`verify` nodes against the *real*
`SandboxRunner` (built via `app.execution.runner.build_sandbox_runner`), not
a fake -- so it's marked `@pytest.mark.sandbox` and skipped (not failed) when
Docker isn't reachable or the sandbox image hasn't been built locally.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

import pytest
from docker.errors import DockerException, ImageNotFound
from langgraph.runtime import Runtime  # pyright: ignore[reportMissingTypeStubs]

from app.config import Settings
from app.execution.runner import SandboxRunner, build_sandbox_runner
from app.execution.sandbox import SANDBOX_LABEL, make_docker_client
from app.graph.build import build_graph
from app.graph.nodes import AgentOutcome, Node
from app.graph.state import AgentState, AgentStateUpdate, GraphContext, RawInput
from app.schemas.execution import ExecutionRequest, TestCase, TestSuite
from tests.input.fakes import FakeLLMClient

pytestmark = pytest.mark.sandbox

MakeSettings = Callable[..., Settings]

_DSA_TEXT: Final = (
    "Given an array of integers nums and an integer target, return indices "
    "of the two numbers such that they add up to target.\n"
    "\n"
    "Example 1:\n"
    "Input: nums = [2,7,11,15], target = 9\n"
    "Output: [0,1]\n"
)

_DSA_CHAT_CONTENT: Final = '{"intent": "DSA_SOLVE", "confidence": 0.95, "rationale": "clear"}'

#: An off-by-one `two_sum`: returns `i + 1` instead of `i` for the second
#: index, so every case whose second index isn't the last element is wrong.
_OFF_BY_ONE_TWO_SUM: Final = (
    "def two_sum(nums, target):\n"
    "    seen = {}\n"
    "    for i, n in enumerate(nums):\n"
    "        if target - n in seen:\n"
    "            return [seen[target - n], i + 1]\n"
    "        seen[n] = i\n"
    "    return []\n"
)

_REQUEST: Final = ExecutionRequest(
    language="python",
    code=_OFF_BY_ONE_TWO_SUM,
    tests=TestSuite(
        entrypoint="two_sum",
        cases=[
            TestCase(name="c1", args=[[2, 7, 11, 15], 9], expected=[0, 1]),
            TestCase(name="c2", args=[[3, 2, 4], 6], expected=[1, 2]),
            TestCase(name="c3", args=[[3, 3], 6], expected=[0, 1]),
            TestCase(name="c4", args=[[1, 5, 9], 10], expected=[0, 2]),
        ],
    ),
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


def _dsa_agent_with_request() -> Node:
    async def dsa_agent(state: AgentState, *, runtime: Runtime[GraphContext]) -> AgentStateUpdate:
        del state, runtime
        return {
            "agent_output": AgentOutcome(text="[dsa stub] here's a hint", topic="two_pointers"),
            "execution_request": _REQUEST,
        }

    return dsa_agent


def _no_sandbox_containers_remain() -> bool:
    client = make_docker_client()
    try:
        remaining = client.containers.list(all=True, filters={"label": f"{SANDBOX_LABEL}=1"})
        return len(remaining) == 0
    finally:
        client.close()


async def test_manual_off_by_one_two_sum_verifies_as_wrong_answer(
    make_settings: MakeSettings,
) -> None:
    settings = make_settings(sandbox_enabled=True)
    _skip_unless_docker_available(settings)

    runner_obj, close_runner = await build_sandbox_runner(settings)
    if runner_obj is None:
        pytest.skip("sandbox runner unavailable at startup")
    runner: SandboxRunner = runner_obj

    try:
        graph = build_graph(node_overrides={"dsa_agent": _dsa_agent_with_request()})
        result = await graph.ainvoke(  # pyright: ignore[reportUnknownMemberType]
            AgentState(input=RawInput(text=_DSA_TEXT)),
            context=GraphContext(llm=FakeLLMClient(chat_content=_DSA_CHAT_CONTENT), runner=runner),
        )
        state = AgentState.model_validate(result)
    finally:
        close_runner()

    verdict = state.verification
    execution = state.execution_result

    print(
        f"ACTUAL: route={state.route!r} "
        f"execution_status={execution.status if execution else None!r} "
        f"verdict_status={verdict.status if verdict else None!r} "
        f"category={verdict.category if verdict else None!r} "
        f"first_failing_case={verdict.first_failing_case if verdict else None!r} "
        f"expected={verdict.expected if verdict else None!r} "
        f"actual={verdict.actual if verdict else None!r} "
        f"diagnostics={verdict.diagnostics if verdict else None!r} "
        f"summary={verdict.summary if verdict else None!r}"
    )

    assert verdict is not None
    assert verdict.status == "fail"
    assert verdict.category == "wrong_answer"
    assert verdict.first_failing_case == "c1"
    assert "off_by_one_suspected" in verdict.diagnostics
    assert _no_sandbox_containers_remain()
