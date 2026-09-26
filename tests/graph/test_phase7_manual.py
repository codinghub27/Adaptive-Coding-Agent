"""Phase 7 manual test cases: the three real specialized-agent subgraphs.

Mirrors `tests/graph/test_phase4_manual.py`/`test_phase6_manual.py`'s style,
but calls the subgraph-boundary functions (`run_dsa`/`run_debug`/
`run_explain`) directly rather than driving the whole outer teaching graph --
these tests are about what each specialized capability itself produces, not
about intent routing (already covered by `test_phase4_manual.py`).

Local `_RoutedLLM` stands in for the shared `tests.input.fakes.FakeLLMClient`
where a scenario needs more than one distinct canned response per run (the
shared fake only ever returns one fixed `chat_content`); it dispatches by a
substring match against each call's own system prompt, so call order never
matters.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from typing import Final

import pytest
from docker.errors import DockerException, ImageNotFound
from langgraph.runtime import Runtime  # pyright: ignore[reportMissingTypeStubs]
from pydantic import JsonValue

from app.agents.hint_engine import HintProgress
from app.config import Settings
from app.execution.base import canonical
from app.execution.runner import SandboxRunner, build_sandbox_runner
from app.execution.sandbox import SANDBOX_LABEL, make_docker_client
from app.graph.state import AgentState, GraphContext, RawInput
from app.graph.subgraphs.debug import run_debug
from app.graph.subgraphs.dsa import run_dsa
from app.graph.subgraphs.explain import run_explain
from app.llm.base import ChatMessage, ChatResult
from app.schemas.agent_results import HintLevel
from app.schemas.execution import (
    CaseResult,
    ExecutionRequest,
    ExecutionResult,
    TestCase,
    TestSuite,
)
from app.schemas.input import CodeBlock, StructuredInput
from app.schemas.plan import TeachingPlan

MARKER: Final = "zzz_untrusted_marker_zzz"
MakeSettings = Callable[..., Settings]


class _RoutedLLM:
    """A local `LLMClient` test double returning a different canned chat
    response per call, dispatched by a substring match against that call's
    own system prompt (`messages[0].content`). `tests.input.fakes.
    FakeLLMClient` only supports one fixed response per instance, which is
    not enough for scenarios (the debugger, the explainer) that make more
    than one distinct kind of LLM call per run.
    """

    def __init__(
        self, routes: Sequence[tuple[str, str]] = (), *, default: str | None = None
    ) -> None:
        self._routes = routes
        self._default = default
        self.chat_calls: list[Sequence[ChatMessage]] = []

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        del temperature, max_tokens
        self.chat_calls.append(messages)
        system = messages[0].content if messages else ""
        for marker, content in self._routes:
            if marker in system:
                return ChatResult(content=content, provider="fake", model="fake-model")
        if self._default is not None:
            return ChatResult(content=self._default, provider="fake", model="fake-model")
        raise AssertionError(f"no canned response configured for prompt: {system[:120]!r}")

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise NotImplementedError

    async def vision(
        self, image: bytes, prompt: str, *, mime_type: str = "image/png"
    ) -> ChatResult:
        raise NotImplementedError


# --------------------------------------------------------------------------
# Manual Test 1: DSA_HINT hint ladder -- one rung per ask, full code only at
# the top of the ladder, never leaked below it.
# --------------------------------------------------------------------------

_SUBARRAY_SUM_TEXT: Final = (
    "Given an array of integers nums and an integer k, return the total "
    "number of contiguous subarrays whose sum equals k.\n"
)

_DSA_ANALYSIS_JSON: Final = json.dumps(
    {
        "understanding": "Count contiguous subarrays of nums that sum to exactly k.",
        "constraints": ["1 <= nums.length <= 2 * 10^4", "-1000 <= nums[i] <= 1000"],
        "topic": "arrays",
        "pattern": "prefix_sum",
        "common_mistakes": ["forgetting to seed the running-count map with {0: 1}"],
        "brute_force": "Sum every contiguous subarray directly with nested loops.",
        "why_slow": "Checking every subarray directly costs O(n^2) time.",
        "key_insight": (
            "Track a running prefix sum and count how many earlier prefix sums equal prefix - k."
        ),
        "pseudocode": (
            "prefix = 0; seen = {0: 1}; count = 0\n"
            "for n in nums:\n"
            "    prefix += n\n"
            "    count += seen.get(prefix - k, 0)\n"
            "    seen[prefix] = seen.get(prefix, 0) + 1\n"
            "return count"
        ),
        "complexity_time": "O(n)",
        "complexity_space": "O(n)",
        "code": (
            "def subarray_sum(nums, k):\n"
            "    prefix = 0\n"
            "    seen = {0: 1}\n"
            "    count = 0\n"
            "    for n in nums:\n"
            "        prefix += n\n"
            "        count += seen.get(prefix - k, 0)\n"
            "        seen[prefix] = seen.get(prefix, 0) + 1\n"
            "    return count\n"
        ),
    }
)


async def test_manual_1_dsa_hint_ladder_climbs_one_rung_at_a_time_never_leaking_code() -> None:
    """`DSA_HINT` on "Subarray Sum Equals K", mid-level `prefers_hints`
    learner (`assistance_level="hint"`, ceiling `L2_DATA_STRUCTURE`).

    Repeated asks are driven by feeding the previous turn's `HintProgress`
    back in, mirroring how `dsa_agent` reconstructs it from learning-event
    history across real turns (see `app.graph.nodes.resolve_hint_progress`).
    """
    plan = TeachingPlan(
        difficulty="medium",
        assistance_level="hint",
        solution_strategy="socratic_hints",
        topic="subarray_sum",
        skill_level=0.5,
    )
    problem = StructuredInput(source="text", question=_SUBARRAY_SUM_TEXT)
    state = AgentState(input=RawInput(text=MARKER), structured_input=problem, plan=plan)
    llm = _RoutedLLM(default=_DSA_ANALYSIS_JSON)
    runtime = Runtime(context=GraphContext(llm=llm))

    progress = HintProgress()
    levels: list[HintLevel] = []
    last_run = None
    for _ in range(4):
        run = await run_dsa(state, runtime, progress=progress)
        last_run = run
        hint = run.result.hint
        assert hint is not None
        levels.append(hint.level)
        # Core security property: at assistance_level="hint" (ceiling L2), no
        # runnable solution body is ever attached to the result, no matter
        # how many times the learner asks.
        assert run.result.code is None
        assert not hint.reveals_code
        assert MARKER not in hint.text
        assert "def subarray_sum" not in hint.text
        progress = HintProgress(last_level=hint.level, solved=False)

    # First ask starts at L0; each subsequent ask climbs exactly one rung;
    # once the "hint" ceiling (L2) is reached, further asks are idempotent.
    assert levels == [
        HintLevel.L0_NUDGE,
        HintLevel.L1_WHAT_TO_TRACK,
        HintLevel.L2_DATA_STRUCTURE,
        HintLevel.L2_DATA_STRUCTURE,
    ]
    assert last_run is not None
    assert last_run.execution_request is None  # no L6 solution => nothing to sandbox-verify
    assert llm.chat_calls  # `_understand` makes a real LLM call every turn (material vs. Phase 04)

    # "Full solution only at the top level" -- demonstrated by raising
    # assistance to "full" (ceiling L6) and climbing the entire ladder.
    full_plan = plan.model_copy(update={"assistance_level": "full"})
    full_state = state.model_copy(update={"plan": full_plan})
    full_progress = HintProgress()
    full_levels: list[HintLevel] = []
    full_run = None
    for _ in range(7):
        full_run = await run_dsa(full_state, runtime, progress=full_progress)
        hint = full_run.result.hint
        assert hint is not None
        full_levels.append(hint.level)
        if hint.level < HintLevel.L6_FULL:
            assert full_run.result.code is None
        full_progress = HintProgress(last_level=hint.level, solved=False)

    assert full_levels == list(HintLevel)  # L0 through L6, one rung climbed per ask
    assert full_run is not None
    assert full_run.result.code is not None
    assert "def subarray_sum" in full_run.result.code
    assert full_run.execution_request is not None
    assert full_run.execution_request.code == full_run.result.code


# --------------------------------------------------------------------------
# Manual Test 2: CODE_DEBUG on a wrong sliding-window solution with tests.
#
# Nothing upstream populates `state.execution_request.tests` yet (see
# PHASE-07 Known Issues), so this sets `state.execution_request` directly,
# exactly as the phase doc documents.
# --------------------------------------------------------------------------

_SLIDING_WINDOW_QUESTION: Final = (
    "Given an array of positive integers nums and an integer limit, return the "
    "length of the longest contiguous subarray whose sum is at most limit.\n"
)

#: Window never shrinks: `left`/`total` are never adjusted once the window
#: sum exceeds `limit`, so `best` counts windows whose sum is invalid.
_BUGGY_SLIDING_WINDOW: Final = (
    "def longest_subarray_at_most(nums, limit):\n"
    "    left = 0\n"
    "    total = 0\n"
    "    best = 0\n"
    "    for right in range(len(nums)):\n"
    "        total += nums[right]\n"
    "        best = max(best, right - left + 1)\n"
    "    return best\n"
)

_PATCHED_SLIDING_WINDOW: Final = (
    "def longest_subarray_at_most(nums, limit):\n"
    "    left = 0\n"
    "    total = 0\n"
    "    best = 0\n"
    "    for right in range(len(nums)):\n"
    "        total += nums[right]\n"
    "        while total > limit:\n"
    "            total -= nums[left]\n"
    "            left += 1\n"
    "        best = max(best, right - left + 1)\n"
    "    return best\n"
)

#: c1 exercises the bug (buggy code answers 4, correct answer is 3); c2
#: passes even in the buggy code (its running sum never exceeds `limit`),
#: matching the realistic "only some cases fail" shape of `test_phase6_manual
#: .py`'s off-by-one suite.
_SLIDING_WINDOW_TESTS: Final = TestSuite(
    entrypoint="longest_subarray_at_most",
    cases=[
        TestCase(name="c1", args=[[5, 1, 1, 1], 3], expected=3),
        TestCase(name="c2", args=[[1, 2, 3], 6], expected=3),
    ],
)

_INFER_APPROACH_JSON: Final = json.dumps(
    {
        "inferred_approach": (
            "A variable-length sliding window that expands to the right and is meant "
            "to shrink from the left whenever the window's sum exceeds the limit."
        )
    }
)

_BUG_EXPLANATION_JSON: Final = json.dumps(
    {
        "bug_explanation": (
            "The loop grows the window and updates `total`, but it never subtracts "
            "elements or advances `left` once `total` exceeds `limit`, so `best` gets "
            "updated from windows whose sum is actually over the limit."
        )
    }
)

_PATCH_JSON: Final = json.dumps({"patched_code": _PATCHED_SLIDING_WINDOW})


def _debug_state_and_llm() -> tuple[AgentState, _RoutedLLM]:
    plan = TeachingPlan(
        difficulty="easy",
        assistance_level="hint",
        solution_strategy="guided_debugging",
        topic="sliding_window",
        skill_level=0.4,
    )
    problem = StructuredInput(
        source="text",
        question=_SLIDING_WINDOW_QUESTION,
        code=[CodeBlock(content=_BUGGY_SLIDING_WINDOW, language="python")],
    )
    state = AgentState(
        input=RawInput(text=MARKER),
        structured_input=problem,
        plan=plan,
        execution_request=ExecutionRequest(
            language="python", code=_BUGGY_SLIDING_WINDOW, tests=_SLIDING_WINDOW_TESTS
        ),
    )
    llm = _RoutedLLM(
        routes=[
            (
                "what approach or algorithm the learner appears to be attempting",
                _INFER_APPROACH_JSON,
            ),
            ("why the bug happens", _BUG_EXPLANATION_JSON),
            ("Produce a corrected, complete, runnable version", _PATCH_JSON),
        ]
    )
    return state, llm


def _sha256(value: JsonValue) -> str:
    """The host-authoritative hash a real harness would report for `value`;
    see `app.execution.verification`'s host-side pass/fail check."""
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _case(name: str, actual: int, *, passed: bool) -> CaseResult:
    return CaseResult(
        name=name,
        passed=passed,
        actual=actual,
        actual_repr=str(actual),
        actual_sha256=_sha256(actual),
        duration_ms=1.0,
    )


def _buggy_result() -> ExecutionResult:
    return ExecutionResult(
        status="failed",
        phase="tests",
        cases=[_case("c1", 4, passed=False), _case("c2", 3, passed=True)],
    )


def _fixed_result() -> ExecutionResult:
    return ExecutionResult(
        status="passed",
        phase="tests",
        cases=[_case("c1", 3, passed=True), _case("c2", 3, passed=True)],
    )


class _ScriptedDebugRunner:
    """Fake `CodeRunner`: recognizes exactly the buggy/patched source it was
    scripted for and returns the matching canned result. Never actually
    executes anything (mirrors `tests/graph/test_execute_verify_nodes.py`'s
    `FakeRunner`), so this variant runs anywhere without Docker.
    """

    def __init__(self, *, buggy_code: str, patched_code: str) -> None:
        self._buggy_code = buggy_code.strip()
        self._patched_code = patched_code.strip()
        self.calls: list[ExecutionRequest] = []

    async def run(self, request: ExecutionRequest) -> ExecutionResult:
        self.calls.append(request)
        code = (request.code or "").strip()
        if code == self._buggy_code:
            return _buggy_result()
        if code == self._patched_code:
            return _fixed_result()
        raise AssertionError(f"unscripted code submitted to fake runner:\n{request.code}")


async def test_manual_2_debug_sliding_window_patches_and_reverifies_fake_runner() -> None:
    state, llm = _debug_state_and_llm()
    runner = _ScriptedDebugRunner(
        buggy_code=_BUGGY_SLIDING_WINDOW, patched_code=_PATCHED_SLIDING_WINDOW
    )
    runtime = Runtime(context=GraphContext(llm=llm, runner=runner))

    run = await run_debug(state, runtime)
    result = run.result

    # Failing case identified from the sandbox's own ground truth, never the LLM.
    assert result.initial_verdict is not None
    assert result.initial_verdict.status == "fail"
    assert result.initial_verdict.category == "wrong_answer"
    assert result.initial_verdict.first_failing_case == "c1"
    assert result.failing_case is not None

    # Bug explained (LLM call #2, only once a real failure was established).
    assert result.bug_explanation is not None
    assert MARKER not in result.bug_explanation

    # A patch was produced and re-run through the sandbox exactly once.
    assert result.patched_code is not None
    assert result.attempts == 1
    assert len(runner.calls) == 2  # one initial run, one re-run of the patch

    # Verification passes -- sandbox ground truth, never the LLM's own claim.
    assert result.final_verdict is not None
    assert result.final_verdict.status == "pass"
    assert result.fixed is True

    assert run.execution_request is not None
    assert run.execution_request.code == result.patched_code


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
async def test_manual_2_debug_sliding_window_patches_and_reverifies_real_sandbox(
    make_settings: MakeSettings,
) -> None:
    """Same scenario as the fake-runner variant, but against the real Docker
    sandbox (`aca-sandbox:py3.11-v1`). The fake LLM still returns a known-
    correct patch deterministically -- this proves the *pipeline* re-runs and
    verifies against real execution, not that a live model writes good
    patches."""
    settings = make_settings(sandbox_enabled=True)
    _skip_unless_docker_available(settings)

    runner_obj, close_runner = await build_sandbox_runner(settings)
    if runner_obj is None:
        pytest.skip("sandbox runner unavailable at startup")
    runner: SandboxRunner = runner_obj

    state, llm = _debug_state_and_llm()
    runtime = Runtime(context=GraphContext(llm=llm, runner=runner))

    try:
        run = await run_debug(state, runtime)
    finally:
        close_runner()

    result = run.result
    print(
        f"ACTUAL: initial={result.initial_verdict.status if result.initial_verdict else None!r} "
        f"category={result.initial_verdict.category if result.initial_verdict else None!r} "
        f"first_failing_case="
        f"{result.initial_verdict.first_failing_case if result.initial_verdict else None!r} "
        f"attempts={result.attempts} "
        f"final={result.final_verdict.status if result.final_verdict else None!r} "
        f"fixed={result.fixed}"
    )

    assert result.initial_verdict is not None
    assert result.initial_verdict.status == "fail"
    assert result.initial_verdict.category == "wrong_answer"
    assert result.initial_verdict.first_failing_case == "c1"

    assert result.patched_code is not None
    assert result.final_verdict is not None
    assert result.final_verdict.status == "pass"
    assert result.fixed is True

    assert run.execution_request is not None
    assert _no_sandbox_containers_remain()


# --------------------------------------------------------------------------
# Manual Test 3: CODE_EXPLAIN on a `two_sum` implementation.
# --------------------------------------------------------------------------

_TWO_SUM_CODE: Final = (
    "def two_sum(nums, target):\n"
    "    seen = {}\n"
    "    for i, n in enumerate(nums):\n"
    "        complement = target - n\n"
    "        if complement in seen:\n"
    "            return [seen[complement], i]\n"
    "        seen[n] = i\n"
    "    return []\n"
)

_LINE_EXPLANATIONS_JSON: Final = json.dumps(
    {
        "line_explanations": [
            {
                "lineno": 1,
                "explanation": "Defines two_sum, taking the list of numbers and the target sum.",
            },
            {"lineno": 2, "explanation": "Creates an empty map from a seen value to its index."},
            {"lineno": 3, "explanation": "Iterates over each number together with its index."},
            {
                "lineno": 4,
                "explanation": "Computes the value that would complete the pair for the target.",
            },
            {"lineno": 5, "explanation": "Checks whether that complement has already been seen."},
            {
                "lineno": 6,
                "explanation": "Returns the indices of the two numbers once a match is found.",
            },
            {"lineno": 7, "explanation": "Records the current number's index for future lookups."},
            {"lineno": 8, "explanation": "Returns an empty list if no pair was found."},
        ]
    }
)

_COMPLEXITY_RATIONALE_JSON: Final = json.dumps(
    {
        "complexity_rationale": (
            "The function makes a single pass over nums, doing O(1) dict work per "
            "element, so the time is O(n); the seen map can hold up to n entries, "
            "so the space is also O(n)."
        )
    }
)


async def test_manual_3_code_explain_two_sum_line_by_line_and_complexity() -> None:
    plan = TeachingPlan(
        difficulty="medium",
        assistance_level="concept",
        solution_strategy="step_by_step_explanation",
        topic="hash_map",
        skill_level=0.5,
    )
    problem = StructuredInput(
        source="text",
        question=MARKER,
        code=[CodeBlock(content=_TWO_SUM_CODE, language="python")],
    )
    state = AgentState(input=RawInput(text=MARKER), structured_input=problem, plan=plan)
    llm = _RoutedLLM(
        routes=[
            ("For each numbered line shown", _LINE_EXPLANATIONS_JSON),
            ("time and space complexity", _COMPLEXITY_RATIONALE_JSON),
        ]
    )
    runtime = Runtime(context=GraphContext(llm=llm))

    run = await run_explain(state, runtime)
    result = run.result

    assert run.execution_request is None  # the explainer never executes anything

    assert result.structure is not None
    assert result.structure.kind == "module"
    assert len(result.structure.children) == 1
    top = result.structure.children[0]
    assert top.kind == "function"
    assert top.name == "two_sum"

    source_lines = _TWO_SUM_CODE.splitlines()
    assert result.line_explanations  # non-empty: every candidate line got a real explanation
    for explanation in result.line_explanations:
        assert 1 <= explanation.lineno <= len(source_lines)
        # Compound-statement headers keep their original indentation;
        # simple-statement segments (`ast.get_source_segment`) start at the
        # statement's own column offset, so exclude leading whitespace --
        # compare stripped either way to confirm each maps to a real line.
        assert explanation.code.strip() == source_lines[explanation.lineno - 1].strip()
        assert explanation.explanation  # non-empty prose
    explained_linenos = {le.lineno for le in result.line_explanations}
    assert explained_linenos == set(range(1, len(source_lines) + 1))

    assert result.complexity_time == "O(n)"
    assert result.complexity_space == "O(n)"
    assert result.complexity_rationale is not None
    assert MARKER not in result.complexity_rationale
