"""Packet P2: probe-set accuracy for `analyze_problem`'s inferred topic.

`plan.topic` becomes a **key in the learner's skill map**, so a wrong topic
doesn't just add noise -- it spends real evidence (see the verified-suite/
skill-move packets that preceded this one) on the wrong subject and starves
the right one. This module is the regression harness for that: a 30-turn
probe set spanning every route (DSA, debug, explain, review) plus deliberately
contentless turns, driven through the *real* pipeline (`should_retrieve` ->
`build_retrieval_query` -> the live retriever -> `analyze_problem`) against
the live Qdrant + cached embedding/reranker models -- no mocked retrieval, so
this can't silently drift from what production actually returns.

Two probes reproduce the exact misfiles that motivated this packet:
- `debug_known_failure_1_binary_search_indexerror`: an `IndexError` loop bug
  in a binary-search implementation, previously misfiled under `hashing`.
- `debug_known_failure_2_factorial_off_by_one`: a `factorial` off-by-one bug,
  previously misfiled under `binary_search_on_answer`. No corpus pattern
  covers plain recursion/loop arithmetic bugs like this one, so the *correct*
  label is `None` -- this is the same "no real topic" case as a contentless
  follow-up, just reached via a real (but off-corpus) subject rather than an
  empty one.

Bar: at least 80% correct over the full probe set (contentless turns count as
probes; assigning one of them a topic is a miss). Never creates, deletes, or
modifies the `dsa_knowledge` collection -- read-only, like
`tests/knowledge/test_phase5_manual.py`.
"""

from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass

import pytest
import pytest_asyncio
from qdrant_client import AsyncQdrantClient

from app.agents.planner import ProblemAnalysis, analyze_problem
from app.config import Settings, get_settings
from app.graph.nodes import build_retrieval_query, should_retrieve
from app.graph.state import AgentState, RawInput
from app.knowledge.retrieve import Retriever, create_retriever
from app.llm.client import Tracer
from app.schemas.input import CodeBlock, StructuredInput
from app.schemas.intent import Intent, IntentResult
from app.schemas.knowledge import RetrievalHit
from app.schemas.profile import LearnerProfileView

pytestmark = pytest.mark.integration

#: Minimum fraction of the probe set `analyze_problem` must get right. This is
#: a floor, not a target: see the P2 packet report for the measured baseline
#: (56.67%, before `build_retrieval_query`/`MIN_RETRIEVAL_TOPIC_SCORE`
#: changed) and the measured result after (90.00%).
ACCURACY_BAR: float = 0.8


def _code(src: str) -> list[CodeBlock]:
    return [CodeBlock(content=src, language="python")]


@dataclass(frozen=True)
class Probe:
    id: str
    input: StructuredInput
    intent: Intent
    expected: str | None


PROBES: tuple[Probe, ...] = (
    # ---------------- DSA (14) ----------------
    Probe(
        id="dsa_two_sum",
        input=StructuredInput(
            source="text",
            problem=(
                "Given an unsorted array of integers and a target value, return the "
                "indices of the two numbers that add up to the target. The array is "
                "not sorted, and you may not reorder it."
            ),
            question=(
                "What's the fastest way to check whether the complement of each "
                "number has already been seen, in a single pass?"
            ),
        ),
        intent=Intent.DSA_SOLVE,
        expected="hashing",
    ),
    Probe(
        id="dsa_binary_search",
        input=StructuredInput(
            source="text",
            problem=(
                "Given a sorted array of integers that may contain duplicates, find "
                "the first and last position of a given target value, in O(log n) "
                "time."
            ),
            question="How do I find both boundaries without falling back to a linear scan?",
        ),
        intent=Intent.DSA_SOLVE,
        expected="binary_search",
    ),
    Probe(
        id="dsa_sliding_window",
        input=StructuredInput(
            source="text",
            problem=(
                "Given a string, find the length of the longest substring that "
                "has no repeating characters."
            ),
            question="What's an approach that avoids checking every substring?",
        ),
        intent=Intent.DSA_SOLVE,
        expected="sliding_window",
    ),
    Probe(
        id="dsa_fast_slow_pointers",
        input=StructuredInput(
            source="text",
            problem=(
                "Given the head of a singly linked list, determine whether the "
                "list contains a cycle without using extra memory."
            ),
            question="Is there a way to detect this without a hash set?",
        ),
        intent=Intent.DSA_SOLVE,
        expected="fast_slow_pointers",
    ),
    Probe(
        id="dsa_dfs_islands",
        input=StructuredInput(
            source="text",
            problem=(
                "Given an m x n grid of 1s (land) and 0s (water), count how many "
                "islands there are, where an island is a group of connected 1s."
            ),
            question="How do I make sure I count each island exactly once?",
        ),
        intent=Intent.DSA_SOLVE,
        expected="dfs",
    ),
    Probe(
        id="dsa_topological_sort",
        input=StructuredInput(
            source="text",
            problem=(
                "There are n courses labeled 0 to n-1 and a list of prerequisite "
                "pairs. Determine if it's possible to finish all courses."
            ),
            question="How do I detect if the prerequisites form a cycle?",
        ),
        intent=Intent.DSA_SOLVE,
        expected="topological_sort",
    ),
    Probe(
        id="dsa_heaps",
        input=StructuredInput(
            source="text",
            problem="Given an unsorted array, find the kth largest element.",
            question="What data structure keeps this efficient if k is small?",
        ),
        intent=Intent.DSA_SOLVE,
        expected="heaps",
    ),
    Probe(
        id="dsa_backtracking",
        input=StructuredInput(
            source="text",
            problem=(
                "Given a set of distinct integers, return all possible subsets (the "
                "power set), including the empty set and the full set itself."
            ),
            question=(
                "How do I make sure I generate every subset exactly once, including "
                "or excluding each element?"
            ),
        ),
        intent=Intent.DSA_SOLVE,
        expected="backtracking",
    ),
    Probe(
        id="dsa_union_find",
        input=StructuredInput(
            source="text",
            problem=(
                "There are n cities, and some are directly connected. Find the "
                "number of provinces, where a province is a group of directly or "
                "indirectly connected cities."
            ),
            question="What's an efficient way to group cities into connected components?",
        ),
        intent=Intent.DSA_SOLVE,
        expected="union_find",
    ),
    Probe(
        id="dsa_trie",
        input=StructuredInput(
            source="text",
            problem=(
                "Design a data structure that supports inserting words and "
                "searching whether a string is a prefix of any inserted word."
            ),
            question="What structure supports fast prefix lookups?",
        ),
        intent=Intent.DSA_SOLVE,
        expected="trie",
    ),
    Probe(
        id="dsa_dp_2d",
        input=StructuredInput(
            source="text",
            problem=(
                "Given two strings, find the length of their longest common "
                "subsequence -- a sequence that appears in both, in order, but not "
                "necessarily contiguously."
            ),
            question="How do I compare a prefix of one string against a prefix of the other?",
        ),
        intent=Intent.DSA_SOLVE,
        expected="dp_2d",
    ),
    Probe(
        id="dsa_dp_1d",
        input=StructuredInput(
            source="text",
            problem=(
                "You are a robber planning to rob houses in a row, but you can't "
                "rob two adjacent houses. Maximize the total amount robbed."
            ),
            question="What's the recurrence for the best amount up to each house?",
        ),
        intent=Intent.DSA_SOLVE,
        expected="dp_1d",
    ),
    Probe(
        id="dsa_greedy",
        input=StructuredInput(
            source="text",
            problem=(
                "Given a list of tasks and a cooldown period n between two same "
                "tasks, find the minimum time to finish all tasks by always "
                "scheduling the most frequent remaining task next."
            ),
            question=(
                "Why does greedily picking the most frequent task each round "
                "minimize the total time?"
            ),
        ),
        intent=Intent.DSA_SOLVE,
        expected="greedy",
    ),
    Probe(
        id="dsa_dijkstra",
        input=StructuredInput(
            source="text",
            problem=(
                "Given a weighted directed graph, find the shortest time for a "
                "signal to reach all nodes from a source node."
            ),
            question="What algorithm finds shortest paths with positive weights efficiently?",
        ),
        intent=Intent.DSA_SOLVE,
        expected="dijkstra",
    ),
    # ---------------- Debug (5, incl. the two known failures) ----------------
    Probe(
        id="debug_known_failure_1_binary_search_indexerror",
        input=StructuredInput(
            source="text",
            question="Why do I get an IndexError here?",
            code=_code(
                "def search(nums, target):\n"
                "    lo, hi = 0, len(nums)\n"
                "    while lo <= hi:\n"
                "        mid = (lo + hi) // 2\n"
                "        if nums[mid] == target:\n"
                "            return mid\n"
                "        if nums[mid] < target:\n"
                "            lo = mid + 1\n"
                "        else:\n"
                "            hi = mid - 1\n"
                "    return -1\n"
            ),
            error="IndexError: list index out of range",
        ),
        intent=Intent.ERROR_EXPLANATION,
        expected="binary_search",
    ),
    Probe(
        id="debug_known_failure_2_factorial_off_by_one",
        input=StructuredInput(
            source="text",
            question="This factorial function gives me the wrong answer, seems off by one.",
            code=_code(
                "def factorial(n):\n"
                "    result = 1\n"
                "    for i in range(1, n):\n"
                "        result *= i\n"
                "    return result\n"
            ),
        ),
        intent=Intent.CODE_DEBUG,
        expected=None,
    ),
    Probe(
        id="debug_sliding_window_off_by_one",
        input=StructuredInput(
            source="text",
            question=(
                "My longest-substring function is one character short on some inputs, what's wrong?"
            ),
            code=_code(
                "def length_of_longest_substring(s):\n"
                "    seen = {}\n"
                "    left = 0\n"
                "    best = 0\n"
                "    for right, ch in enumerate(s):\n"
                "        if ch in seen and seen[ch] >= left:\n"
                "            left = seen[ch]\n"
                "        seen[ch] = right\n"
                "        best = max(best, right - left + 1)\n"
                "    return best\n"
            ),
        ),
        intent=Intent.TEST_CASE_ANALYSIS,
        expected="sliding_window",
    ),
    Probe(
        id="debug_linked_list_attributeerror",
        input=StructuredInput(
            source="text",
            question="Why do I get this error reversing my linked list?",
            code=_code(
                "def reverse_list(head):\n"
                "    prev = None\n"
                "    curr = head\n"
                "    while curr:\n"
                "        nxt = curr.next\n"
                "        curr.next = prev\n"
                "        prev = curr\n"
                "        curr = nxt.next\n"
                "    return prev\n"
            ),
            error="AttributeError: 'NoneType' object has no attribute 'next'",
        ),
        intent=Intent.ERROR_EXPLANATION,
        expected="linked_list",
    ),
    Probe(
        id="debug_two_sum_wrong_logic",
        input=StructuredInput(
            source="text",
            question="This returns None even though a valid pair exists in the array, why?",
            code=_code(
                "def two_sum(nums, target):\n"
                "    seen = {}\n"
                "    for i, num in enumerate(nums):\n"
                "        complement = target + num\n"
                "        if complement in seen:\n"
                "            return [seen[complement], i]\n"
                "        seen[num] = i\n"
                "    return None\n"
            ),
        ),
        intent=Intent.CODE_DEBUG,
        expected="hashing",
    ),
    # ---------------- Explain (4) ----------------
    Probe(
        id="explain_dfs_traversal",
        input=StructuredInput(
            source="text",
            question=(
                "Why does this explore one branch of the graph all the way to the "
                "end before moving on to the next branch, instead of visiting nodes "
                "level by level?"
            ),
            code=_code(
                "def explore(graph, start):\n"
                "    visited = set()\n"
                "    stack = [start]\n"
                "    while stack:\n"
                "        node = stack.pop()\n"
                "        if node in visited:\n"
                "            continue\n"
                "        visited.add(node)\n"
                "        for neighbor in graph[node]:\n"
                "            stack.append(neighbor)\n"
                "    return visited\n"
            ),
        ),
        intent=Intent.CODE_EXPLAIN,
        expected="dfs",
    ),
    Probe(
        id="explain_two_pointers_concept",
        input=StructuredInput(
            source="text",
            question="What is the two pointers technique and when should I use it?",
        ),
        intent=Intent.CONCEPT_EXPLANATION,
        expected="two_pointers",
    ),
    Probe(
        id="explain_monotonic_stack",
        input=StructuredInput(
            source="text",
            question="Why does this use a stack instead of nested loops?",
            code=_code(
                "def daily_temperatures(temps):\n"
                "    answer = [0] * len(temps)\n"
                "    stack = []\n"
                "    for i, t in enumerate(temps):\n"
                "        while stack and temps[stack[-1]] < t:\n"
                "            j = stack.pop()\n"
                "            answer[j] = i - j\n"
                "        stack.append(i)\n"
                "    return answer\n"
            ),
        ),
        intent=Intent.CODE_EXPLAIN,
        expected="monotonic_stack",
    ),
    Probe(
        id="explain_trie_concept",
        input=StructuredInput(
            source="text",
            question="Can you explain how a trie supports prefix search?",
        ),
        intent=Intent.CONCEPT_EXPLANATION,
        expected="trie",
    ),
    # ---------------- Review (3) ----------------
    Probe(
        id="review_hash_duplicate",
        input=StructuredInput(
            source="text",
            question="Can you review this duplicate-detection function?",
            code=_code(
                "def has_duplicate(nums):\n"
                "    seen = set()\n"
                "    for num in nums:\n"
                "        if num in seen:\n"
                "            return True\n"
                "        seen.append(num)\n"
                "    return False\n"
            ),
        ),
        intent=Intent.CODE_REVIEW,
        expected="hashing",
    ),
    Probe(
        id="optimize_pair_sum_brute_force",
        input=StructuredInput(
            source="text",
            question="How can I make this pair-sum search faster than O(n^2)?",
            code=_code(
                "def two_sum_brute(nums, target):\n"
                "    for i in range(len(nums)):\n"
                "        for j in range(i + 1, len(nums)):\n"
                "            if nums[i] + nums[j] == target:\n"
                "                return [i, j]\n"
                "    return None\n"
            ),
        ),
        intent=Intent.OPTIMIZATION,
        expected="hashing",
    ),
    Probe(
        id="review_merge_intervals",
        input=StructuredInput(
            source="text",
            question="Does this correctly merge all overlapping intervals?",
            code=_code(
                "def merge_intervals(intervals):\n"
                "    intervals.sort()\n"
                "    merged = []\n"
                "    for start, end in intervals:\n"
                "        if merged and start <= merged[-1][1]:\n"
                "            merged[-1][1] = max(merged[-1][1], end)\n"
                "        else:\n"
                "            merged.append([start, end])\n"
                "    return merged\n"
            ),
        ),
        intent=Intent.CODE_REVIEW,
        expected="intervals",
    ),
    # ---------------- Contentless (4, expected None) ----------------
    Probe(
        id="contentless_next_hint",
        input=StructuredInput(source="text", question="Give me the next hint."),
        intent=Intent.DSA_HINT,
        expected=None,
    ),
    Probe(
        id="contentless_ok_thanks",
        input=StructuredInput(source="text", question="ok thanks"),
        intent=Intent.CONCEPT_EXPLANATION,
        expected=None,
    ),
    Probe(
        id="contentless_can_you_help",
        input=StructuredInput(source="text", question="can you help me?"),
        intent=Intent.DSA_SOLVE,
        expected=None,
    ),
    Probe(
        id="contentless_okay_got_it",
        input=StructuredInput(source="text", question="Okay, got it."),
        intent=Intent.CODE_EXPLAIN,
        expected=None,
    ),
)


def _live_settings() -> Settings:
    return get_settings()


async def _open_client(settings: Settings) -> AsyncQdrantClient:
    return AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=(
            settings.qdrant_api_key.get_secret_value()
            if settings.qdrant_api_key is not None
            else None
        ),
        timeout=settings.qdrant_timeout,
        check_compatibility=False,
    )


@pytest_asyncio.fixture
async def live_retriever() -> AsyncGenerator[Retriever]:
    """The real `KnowledgeRetriever`: live Qdrant + cached fastembed models.

    Read-only against the shared `dsa_knowledge` collection -- mirrors
    `tests/knowledge/test_phase5_manual.py::live_retriever`.
    """
    settings = _live_settings()
    client = await _open_client(settings)
    try:
        retriever = await create_retriever(settings, client, Tracer.disabled())
        yield retriever
    finally:
        await client.close()


def _state(probe: Probe) -> AgentState:
    return AgentState(
        input=RawInput(text="probe"),
        structured_input=probe.input,
        intent=IntentResult(intent=probe.intent, confidence=0.95, source="rule"),
    )


async def _resolve(
    probe: Probe, retriever: Retriever, profile: LearnerProfileView
) -> ProblemAnalysis:
    """Run this probe through the real pipeline: `should_retrieve` ->
    `build_retrieval_query` -> the live retriever -> `analyze_problem`.

    Exactly mirrors what `retrieve_knowledge`/`plan_teaching` do in
    `app.graph.nodes` for a fresh account (empty profile, no explicit
    `topic_hint`) -- the same shape as the live HTTP check in the P2 report.
    """
    state = _state(probe)
    hits: Sequence[RetrievalHit] = ()
    if should_retrieve(state):
        query = build_retrieval_query(state)
        if query:
            hits = await retriever.retrieve(query, top_k=4)
    return analyze_problem(probe.input, profile, None, context=hits)


async def test_topic_accuracy_probe_set_clears_the_bar(live_retriever: Retriever) -> None:
    profile = LearnerProfileView.empty()
    misses: list[str] = []

    for probe in PROBES:
        analysis = await _resolve(probe, live_retriever, profile)
        if analysis.topic != probe.expected:
            misses.append(f"{probe.id}: expected {probe.expected!r}, got {analysis.topic!r}")

    accuracy = (len(PROBES) - len(misses)) / len(PROBES)
    print(f"\nACTUAL: accuracy={accuracy:.2%} ({len(PROBES) - len(misses)}/{len(PROBES)})")
    if misses:
        print("ACTUAL: misses=" + "; ".join(misses))

    assert accuracy >= ACCURACY_BAR, (
        f"topic accuracy {accuracy:.2%} fell below the {ACCURACY_BAR:.0%} bar; misses: {misses}"
    )


async def test_known_failure_1_indexerror_loop_resolves_to_binary_search(
    live_retriever: Retriever,
) -> None:
    """The exact misfile that motivated this packet: an `IndexError` loop bug
    in a binary-search implementation, previously filed under `hashing`."""
    probe = next(p for p in PROBES if p.id == "debug_known_failure_1_binary_search_indexerror")
    analysis = await _resolve(probe, live_retriever, LearnerProfileView.empty())
    assert analysis.topic == "binary_search"


async def test_known_failure_2_factorial_off_by_one_resolves_to_no_topic(
    live_retriever: Retriever,
) -> None:
    """The other misfile that motivated this packet: a `factorial` off-by-one
    bug, previously filed under `binary_search_on_answer`. No corpus pattern
    covers plain recursion/loop arithmetic, so the correct resolution is
    `None`, not a substitute wrong slug."""
    probe = next(p for p in PROBES if p.id == "debug_known_failure_2_factorial_off_by_one")
    analysis = await _resolve(probe, live_retriever, LearnerProfileView.empty())
    assert analysis.topic is None


async def test_no_real_topic_probes_never_acquire_a_topic(live_retriever: Retriever) -> None:
    """Every probe labelled `expected=None` -- both genuinely contentless
    follow-ups and the off-corpus `factorial` debug turn -- must still yield
    `topic=None`. This is the contentless-turn fix this packet must not trade
    away for accuracy elsewhere."""
    profile = LearnerProfileView.empty()
    for probe in PROBES:
        if probe.expected is not None:
            continue
        analysis = await _resolve(probe, retriever=live_retriever, profile=profile)
        assert analysis.topic is None, f"{probe.id} wrongly acquired topic {analysis.topic!r}"
