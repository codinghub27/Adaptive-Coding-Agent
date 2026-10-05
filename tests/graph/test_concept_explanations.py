"""A concept the corpus does not cover is still explained; one it covers stays grounded.

Measured live: "explain the concept of recursion with examples" retrieved
dynamic-programming pages (top score -1.19), was labelled "Dynamic
programming", and the answer was "the references don't cover recursion".
"""

import json
from collections.abc import Sequence
from typing import cast
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.planner import (
    MIN_CONCEPT_TOPIC_SCORE,
    analyze_problem,
    relevant_to_concept,
)
from app.graph.build import GraphRunResult, run_graph
from app.graph.state import RawInput
from app.graph.subgraphs.dsa import corpus_chunks_by_pattern
from app.llm.base import ChatMessage, ChatResult
from app.schemas.execution import ExecutionRequest, ExecutionResult, HarnessError
from app.schemas.input import StructuredInput
from app.schemas.knowledge import RetrievalHit
from app.schemas.profile import LearnerProfileView
from tests.graph import test_conversation_regression as reg
from tests.graph.test_conversation_regression import (
    _Store,  # pyright: ignore[reportPrivateUsage]
    store,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)

_RECURSION = "explain the concept of recursion with examples"
_BINARY_SEARCH = "explain binary search"

_GOOD_EXAMPLE = "def fact(n):\n    return 1 if n <= 1 else n * fact(n - 1)\n\nprint(fact(5))\n"
_BROKEN_EXAMPLE = "def loop(n):\n    return loop(n)\n\nprint(loop(1))\n"


def _hits(pattern: str, top: float) -> list[RetrievalHit]:
    chunks = corpus_chunks_by_pattern()[pattern][:3]
    return [
        RetrievalHit(chunk=chunk, score=top - index, retrievers=("dense", "bm25"), reranked=True)
        for index, chunk in enumerate(chunks)
    ]


class _Retriever:
    """What the live retriever returned for each of the two questions."""

    async def retrieve(self, query: str, top_k: int) -> list[RetrievalHit]:
        del top_k
        if "recursion" in query:
            return _hits("dynamic_programming", -1.19)
        return _hits("binary_search", 7.47)


class _TutorLLM:
    def __init__(self) -> None:
        self.systems: list[str] = []
        self.users: list[str] = []

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        del temperature, max_tokens
        system, user = messages[0].content, messages[-1].content
        self.systems.append(system)
        self.users.append(user)
        if system.startswith("You are the intent classifier"):
            content = json.dumps({"intent": "CONCEPT_EXPLANATION", "confidence": 0.95})
        elif "the curated notes do not cover" in system:
            content = json.dumps(
                {
                    "answer": "Recursion is a function solving a problem by calling itself "
                    "on a smaller version of it, until a base case stops it.",
                    "examples": [
                        {"title": "Factorial", "code": _GOOD_EXAMPLE},
                        {"title": "No base case", "code": _BROKEN_EXAMPLE},
                    ],
                }
            )
        elif "using ONLY the numbered reference excerpts" in system:
            content = json.dumps(
                {"answer": "Binary search halves a sorted range each step.", "used": [1]}
            )
        else:
            content = "{}"
        return ChatResult(content=content, provider="fake", model="fake")

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise NotImplementedError

    async def vision(
        self, image: bytes, prompt: str, *, mime_type: str = "image/png"
    ) -> ChatResult:
        raise NotImplementedError

    def asked(self, marker: str) -> bool:
        return any(marker in system for system in self.systems)


class _Sandbox:
    """Runs nothing: the factorial prints 120, the endless recursion crashes."""

    def __init__(self) -> None:
        self.ran: list[str] = []

    async def run(self, request: ExecutionRequest) -> ExecutionResult:
        self.ran.append(request.code)
        if "loop(n)" in request.code:
            return ExecutionResult(
                status="runtime_error",
                error=HarnessError(type="RecursionError", message="x", lineno=2),
            )
        return ExecutionResult(status="completed", stdout="120\n")


async def _ask(text: str, llm: _TutorLLM, sandbox: _Sandbox | None) -> GraphRunResult:
    return await run_graph(
        RawInput(text=text),
        llm=llm,
        session=cast("AsyncSession", reg._Session()),  # pyright: ignore[reportPrivateUsage]
        user_id=uuid4(),
        conversation_id=uuid4(),
        retriever=_Retriever(),
        runner=sandbox,
    )


def _reply(result: GraphRunResult) -> str:
    assert result.state.response is not None
    return result.state.response


async def test_a_concept_the_corpus_lacks_is_explained_not_refused(store: _Store) -> None:  # noqa: F811
    del store
    llm, sandbox = _TutorLLM(), _Sandbox()
    result = await _ask(_RECURSION, llm, sandbox)
    reply = _reply(result)

    assert result.state.route == "explain"
    # A real explanation, from the tutor's own knowledge, and it says so.
    assert "calling itself" in reply
    assert "Not from the curated material" in reply
    assert "don't cover" not in reply.lower()
    assert "do not cover" not in reply.lower()
    assert llm.asked("the curated notes do not cover")
    # The dynamic-programming pages were never offered as its references.
    assert not llm.asked("using ONLY the numbered reference excerpts")
    assert not any("References:" in user for user in llm.users)
    assert result.state.generated_response is not None
    assert result.state.generated_response.citations == []


async def test_a_recursion_question_is_not_labelled_dynamic_programming(
    store: _Store,  # noqa: F811
) -> None:
    del store
    result = await _ask(_RECURSION, _TutorLLM(), _Sandbox())
    assert result.state.plan is not None
    assert result.state.plan.topic is None  # no "Mental model: Dynamic programming" card
    assert result.state.topic_source == "unknown"
    assert "dynamic programming" not in _reply(result).lower()
    assert all(event.topic != "dynamic_programming" for event in result.state.events)


async def test_examples_are_run_in_the_sandbox_before_they_are_shown(store: _Store) -> None:  # noqa: F811
    del store
    llm, sandbox = _TutorLLM(), _Sandbox()
    reply = _reply(await _ask(_RECURSION, llm, sandbox))

    assert len(sandbox.ran) == 2  # both examples went through the sandbox
    assert "print(fact(5))" in reply
    assert "Output (run in the sandbox)" in reply
    assert "120" in reply  # the output it ACTUALLY produced
    # The example that crashed is not shown, and the reply says one was left out.
    assert "loop(n)" not in reply
    assert "left out" in reply


async def test_without_a_sandbox_an_example_is_labelled_not_executed(store: _Store) -> None:  # noqa: F811
    del store
    reply = _reply(await _ask(_RECURSION, _TutorLLM(), None))
    assert "print(fact(5))" in reply
    assert "not executed" in reply
    assert "Output (run in the sandbox)" not in reply


async def test_a_concept_the_corpus_covers_stays_grounded_and_cited(store: _Store) -> None:  # noqa: F811
    del store
    llm = _TutorLLM()
    result = await _ask(_BINARY_SEARCH, llm, _Sandbox())
    reply = _reply(result)

    assert result.state.route == "explain"
    assert result.state.plan is not None
    assert result.state.plan.topic == "binary_search"
    assert "halves a sorted range" in reply
    assert llm.asked("using ONLY the numbered reference excerpts")
    assert not llm.asked("the curated notes do not cover")
    assert "Not from the curated material" not in reply
    generated = result.state.generated_response
    assert generated is not None
    assert generated.citations  # the reference it used is cited
    assert any("Binary Search" in label for label in generated.citations)


def test_the_concept_floor_sits_between_covered_and_uncovered_questions() -> None:
    """The measured scores: covered questions 4.4 to 9.0, uncovered at most 1.0."""
    assert 1.01 < MIN_CONCEPT_TOPIC_SCORE < 4.39
    weak, strong = _hits("dynamic_programming", -1.19), _hits("binary_search", 7.47)
    assert relevant_to_concept(weak) == []
    assert relevant_to_concept(strong)[0] == strong[0]

    profile = LearnerProfileView.empty()
    question = StructuredInput(source="text", question=_RECURSION)
    as_concept = analyze_problem(question, profile, context=weak, concept_question=True)
    assert (as_concept.topic, as_concept.topic_source) == (None, "unknown")
    covered = analyze_problem(
        StructuredInput(source="text", question=_BINARY_SEARCH),
        profile,
        context=strong,
        concept_question=True,
    )
    assert covered.topic == "binary_search"
    # A problem STATEMENT keeps the old, lower floor: statements score far
    # lower than short questions against the same pages.
    statement = StructuredInput(source="text", problem="Given an array ... return the count.")
    assert analyze_problem(statement, profile, context=weak).topic == "dynamic_programming"


def test_a_hit_that_was_never_reranked_is_not_judged_by_the_floor() -> None:
    """With the reranker down the score is a fusion score on another scale."""
    chunk = corpus_chunks_by_pattern()["binary_search"][0]
    fused = [RetrievalHit(chunk=chunk, score=0.03, retrievers=("dense",), reranked=False)]
    assert relevant_to_concept(fused) == fused
