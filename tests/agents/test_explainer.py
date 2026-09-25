"""Tests for `app.agents.explainer` / `app.graph.subgraphs.explain` (Phase 07 P5).

No real LLM, no Docker, no network: `FakeLLMClient` (`tests.input.fakes`)
stands in throughout, and nothing here ever touches `runtime.context.runner`
(the explainer never executes code).
"""

import json
from typing import Final

from langgraph.runtime import Runtime  # pyright: ignore[reportMissingTypeStubs]

from app.agents.explainer import build_structure, estimate_complexity
from app.graph.state import AgentState, GraphContext, RawInput
from app.graph.subgraphs.explain import run_explain
from app.schemas.input import CodeBlock, StructuredInput
from tests.input.fakes import FakeLLMClient

_TWO_SUM_HASHMAP: Final = (
    "def two_sum(nums, target):\n"
    "    seen = {}\n"
    "    for i, num in enumerate(nums):\n"
    "        complement = target - num\n"
    "        if complement in seen:\n"
    "            return [seen[complement], i]\n"
    "        seen[num] = i\n"
    "    return []\n"
)

_TWO_SUM_BRUTE_FORCE: Final = (
    "def two_sum(nums, target):\n"
    "    n = len(nums)\n"
    "    for i in range(n):\n"
    "        for j in range(i + 1, n):\n"
    "            if nums[i] + nums[j] == target:\n"
    "                return [i, j]\n"
    "    return []\n"
)

_STRUCTURED_CODE: Final = (
    "class Solution:\n"
    "    def two_sum(self, nums, target):\n"
    "        seen = {}\n"
    "        for i, num in enumerate(nums):\n"
    "            if target - num in seen:\n"
    "                return [seen[target - num], i]\n"
    "            seen[num] = i\n"
    "        return []\n"
)

_BROKEN_CODE: Final = "def two_sum(nums, target:\n    return nums[0\n"

# A single JSON payload whose keys satisfy both `_LineExplanationsOutput` and
# `_ComplexityRationaleOutput` (each parser uses `extra="ignore"`), so one
# `FakeLLMClient(chat_content=...)` answers both of the explainer's LLM calls.
_LLM_CONTENT: Final = json.dumps(
    {
        "line_explanations": [
            {"lineno": 1, "explanation": "Defines the two_sum function."},
            {"lineno": 2, "explanation": "Creates an empty lookup dict."},
            {"lineno": 999, "explanation": "This lineno does not exist in the source."},
        ],
        "complexity_rationale": "A single pass over nums with O(1) dict lookups per element.",
    }
)


def _runtime(llm: FakeLLMClient) -> Runtime[GraphContext]:
    return Runtime(context=GraphContext(llm=llm))


def _state(code: str) -> AgentState:
    structured = StructuredInput(source="text", code=[CodeBlock(content=code, language="python")])
    return AgentState(input=RawInput(text="explain this"), structured_input=structured)


# --------------------------------------------------------------------------
# Manual Test 3: two_sum hashmap -> O(n)/O(n)
# --------------------------------------------------------------------------


async def test_two_sum_hashmap_reports_linear_time_and_space() -> None:
    llm = FakeLLMClient(chat_content=_LLM_CONTENT)

    run_result = await run_explain(_state(_TWO_SUM_HASHMAP), _runtime(llm))

    assert run_result.result.complexity_time == "O(n)"
    assert run_result.result.complexity_space == "O(n)"
    assert run_result.execution_request is None


# --------------------------------------------------------------------------
# Nested-loop brute force -> O(n^2) time
# --------------------------------------------------------------------------


async def test_nested_loop_brute_force_reports_quadratic_time() -> None:
    estimate = estimate_complexity(_TWO_SUM_BRUTE_FORCE)

    assert estimate.time == "O(n^2)"


async def test_nested_loop_brute_force_run_explain_reports_quadratic_time() -> None:
    llm = FakeLLMClient(chat_content=_LLM_CONTENT)

    run_result = await run_explain(_state(_TWO_SUM_BRUTE_FORCE), _runtime(llm))

    assert run_result.result.complexity_time == "O(n^2)"


# --------------------------------------------------------------------------
# Structure tree: module/class/function nesting with correct linenos
# --------------------------------------------------------------------------


def test_structure_tree_has_correct_module_class_function_nesting() -> None:
    structure = build_structure(_STRUCTURED_CODE)

    assert structure is not None
    assert structure.kind == "module"
    assert structure.lineno == 1
    assert structure.end_lineno == 8

    assert len(structure.children) == 1
    class_node = structure.children[0]
    assert class_node.kind == "class"
    assert class_node.name == "Solution"
    assert class_node.lineno == 1
    assert class_node.end_lineno == 8

    assert len(class_node.children) == 1
    func_node = class_node.children[0]
    assert func_node.kind == "function"
    assert func_node.name == "two_sum"
    assert func_node.lineno == 2
    assert func_node.end_lineno == 8

    assert len(func_node.children) == 1
    for_node = func_node.children[0]
    assert for_node.kind == "block"
    assert for_node.name == "for"
    assert for_node.lineno == 4
    assert for_node.end_lineno == 7

    assert len(for_node.children) == 1
    if_node = for_node.children[0]
    assert if_node.kind == "block"
    assert if_node.name == "if"
    assert if_node.lineno == 5
    assert if_node.end_lineno == 6


# --------------------------------------------------------------------------
# Every line_explanations[*].lineno is within the submitted source's range
# --------------------------------------------------------------------------


async def test_line_explanations_linenos_are_within_source_range() -> None:
    llm = FakeLLMClient(chat_content=_LLM_CONTENT)
    total_lines = len(_TWO_SUM_HASHMAP.splitlines())

    run_result = await run_explain(_state(_TWO_SUM_HASHMAP), _runtime(llm))

    assert run_result.result.line_explanations, "expected at least one line explanation"
    for explanation in run_result.result.line_explanations:
        assert 1 <= explanation.lineno <= total_lines
    # The out-of-range lineno (999) in the canned response must have been dropped.
    assert all(e.lineno != 999 for e in run_result.result.line_explanations)


# --------------------------------------------------------------------------
# Syntactically broken code: tree-sitter fallback still produces a partial
# structure, no crash
# --------------------------------------------------------------------------


def test_syntactically_broken_code_still_yields_partial_structure() -> None:
    structure = build_structure(_BROKEN_CODE)

    assert structure is not None
    assert structure.kind == "module"


async def test_explain_run_over_broken_code_does_not_crash() -> None:
    llm = FakeLLMClient(chat_content=_LLM_CONTENT)

    run_result = await run_explain(_state(_BROKEN_CODE), _runtime(llm))

    assert run_result.result.structure is not None
    # Unparseable code: no LLM-derived per-line explanations, but no crash either.
    assert run_result.result.line_explanations == []


# --------------------------------------------------------------------------
# LLMError degrades gracefully
# --------------------------------------------------------------------------


async def test_llm_error_degrades_gracefully() -> None:
    llm = FakeLLMClient(raise_chat=True)

    run_result = await run_explain(_state(_TWO_SUM_HASHMAP), _runtime(llm))

    assert run_result.result.line_explanations == []
    assert run_result.result.complexity_rationale is None
    # The deterministic complexity estimate must still be present -- it never
    # depends on the LLM.
    assert run_result.result.complexity_time == "O(n)"
    assert run_result.result.complexity_space == "O(n)"
    assert run_result.result.structure is not None
