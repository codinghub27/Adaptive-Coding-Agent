"""`adapt_nodes`: tree and linked-list solutions callable with plain lists.

The static tests need nothing. The behavioural ones run the adapted code in
the REAL sandbox (never on the host) and are skipped without Docker.
"""

import ast
from collections.abc import AsyncGenerator, Callable

import pytest
from pydantic import JsonValue

from app.config import Settings
from app.execution.node_adapter import adapt_nodes
from app.execution.runner import SandboxRunner, build_sandbox_runner
from app.execution.testgen import extract_test_suite
from app.execution.verification import verify
from app.schemas.execution import ExecutionRequest, TestCase, TestSuite
from app.schemas.input import CodeBlock, StructuredInput

_MAX_PATH_WRONG = (
    "def maxPathSum(root):\n"
    "    if not root:\n"
    "        return 0\n"
    "\n"
    "    left = maxPathSum(root.left)\n"
    "    right = maxPathSum(root.right)\n"
    "\n"
    "    return root.val + left + right\n"
)
_INVERT = (
    "def invertTree(root: Optional[TreeNode]) -> Optional[TreeNode]:\n"
    "    if root is None:\n"
    "        return None\n"
    "    root.left, root.right = invertTree(root.right), invertTree(root.left)\n"
    "    return root\n"
)
_REVERSE = (
    "def reverseList(head: Optional[ListNode]) -> Optional[ListNode]:\n"
    "    prev = None\n"
    "    while head:\n"
    "        head.next, prev, head = prev, head, head.next\n"
    "    return prev\n"
)
_COUNT = (
    "def countNodes(root):\n"
    "    def walk(node):\n"
    "        return 0 if node is None else 1 + walk(node.left) + walk(node.right)\n"
    "    return walk(root)\n"
)
_LENGTH = (
    "def length(head):\n"
    "    count = 0\n"
    "    while head:\n"
    "        count, head = count + 1, head.next\n"
    "    return count\n"
)


# --- static -----------------------------------------------------------------------


def test_code_without_node_parameters_is_left_alone() -> None:
    code = "def two_sum(nums, target):\n    return []\n"
    assert adapt_nodes(code) == (code, 0)
    assert adapt_nodes("def broken(:\n") == ("def broken(:\n", 0)


def test_a_list_annotated_parameter_named_like_a_tree_is_not_converted() -> None:
    code = "def total(root: list[int]):\n    return sum(root)\n"
    assert adapt_nodes(code) == (code, 0)


def test_the_submission_keeps_its_line_numbers_unless_a_class_must_go_above() -> None:
    adapted, shift = adapt_nodes(_MAX_PATH_WRONG)
    assert shift == 0
    assert adapted.startswith(_MAX_PATH_WRONG.rstrip("\n"))  # everything is appended
    ast.parse(adapted)
    # `Optional[TreeNode]` is evaluated when the `def` runs, so the class it
    # names has to exist first: it goes above and the shift says by how much.
    adapted, shift = adapt_nodes("from typing import *\n" + _INVERT)
    assert shift > 0
    assert adapted.split("\n")[shift] == "from typing import *"
    ast.parse(adapted)


def test_adapting_twice_changes_nothing() -> None:
    once, _ = adapt_nodes(_MAX_PATH_WRONG)
    assert adapt_nodes(once) == (once, 0)


def test_a_learners_own_node_class_is_not_redefined() -> None:
    own = (
        "class TreeNode:\n"
        "    def __init__(self, x):\n"
        "        self.val = x\n"
        "        self.left = None\n"
        "        self.right = None\n\n\n"
    )
    adapted, shift = adapt_nodes(own + _COUNT)
    assert shift == 0
    assert adapted.count("class TreeNode") == 1


def test_leetcode_examples_with_null_are_read() -> None:
    problem = StructuredInput(
        source="text",
        problem=(
            "Return the maximum path sum.\n\n"
            "Example 1:\nInput: root = [-10,9,20,null,null,15,7]\nOutput: 42"
        ),
        code=[CodeBlock(content=_MAX_PATH_WRONG)],
    )
    suite = extract_test_suite(problem)
    assert suite is not None
    values: list[JsonValue] = [*suite.cases[0].args, *suite.cases[0].kwargs.values()]
    assert values == [[-10, 9, 20, None, None, 15, 7]]
    assert suite.cases[0].expected == 42


# --- in the sandbox ------------------------------------------------------------------


@pytest.fixture
async def runner(make_settings: Callable[..., Settings]) -> AsyncGenerator[SandboxRunner]:
    settings = make_settings(sandbox_enabled=True)
    built, close = await build_sandbox_runner(settings)
    if built is None:
        pytest.skip("docker engine or sandbox image not available")
    yield built
    close()


async def _run(
    runner: SandboxRunner,
    code: str,
    entrypoint: str,
    cases: list[tuple[list[JsonValue], JsonValue]],
) -> tuple[int, int, str]:
    suite = TestSuite(
        entrypoint=entrypoint,
        cases=[
            TestCase(name=f"c{i}", args=args, expected=exp) for i, (args, exp) in enumerate(cases)
        ],
    )
    request = ExecutionRequest(code=code, tests=suite)
    verdict = verify(await runner.run(request), request)
    return verdict.cases_passed, verdict.cases_total, verdict.status


@pytest.mark.sandbox
async def test_trees_are_built_from_level_order_lists(runner: SandboxRunner) -> None:
    cases: list[tuple[list[JsonValue], JsonValue]] = [
        ([[]], 0),  # the empty tree
        ([[7]], 1),  # a single node
        ([[1, None, 2, None, 3, None, 4]], 4),  # skewed right
        ([[1, 2, None, 3, None, 4]], 4),  # skewed left
        ([[1, 2, 3, None, 4]], 4),  # a hole in the middle
    ]
    assert await _run(runner, _COUNT, "countNodes", cases) == (5, 5, "pass")


@pytest.mark.sandbox
async def test_a_returned_tree_comes_back_as_a_level_order_list(runner: SandboxRunner) -> None:
    code = "from typing import *\n" + _INVERT
    cases: list[tuple[list[JsonValue], JsonValue]] = [
        ([[4, 2, 7, 1, 3, 6, 9]], [4, 7, 2, 9, 6, 3, 1]),
        ([[1, 2]], [1, None, 2]),
        ([[]], []),  # annotated as returning a tree: the empty tree is []
    ]
    assert await _run(runner, code, "invertTree", cases) == (3, 3, "pass")


@pytest.mark.sandbox
async def test_linked_lists_go_in_and_come_back_as_plain_lists(runner: SandboxRunner) -> None:
    code = "from typing import *\n" + _REVERSE
    reverse: list[tuple[list[JsonValue], JsonValue]] = [
        ([[1, 2, 3]], [3, 2, 1]),
        ([[5]], [5]),
        ([[]], []),  # the empty list
    ]
    assert await _run(runner, code, "reverseList", reverse) == (3, 3, "pass")
    lengths: list[tuple[list[JsonValue], JsonValue]] = [([[]], 0), ([[1, 2, 3, 4]], 4)]
    assert await _run(runner, _LENGTH, "length", lengths) == (2, 2, "pass")


@pytest.mark.sandbox
async def test_a_wrong_tree_solution_gets_a_real_fail_verdict(runner: SandboxRunner) -> None:
    """Scenario 4: this used to end `inconclusive 0/0`."""
    cases: list[tuple[list[JsonValue], JsonValue]] = [
        ([[1, 2, 3]], 6),  # the two-sided sum happens to be right here
        ([[-10, 9, 20, None, None, 15, 7]], 42),  # and wrong here: it returns 41
        ([[-3]], -3),
    ]
    passed, total, status = await _run(runner, _MAX_PATH_WRONG, "maxPathSum", cases)
    assert (total, status) == (3, "fail")
    assert passed == 2
