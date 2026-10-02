"""ADAPTIVE-upgrade P5: honest progress steps, honest "adapted" flag, deterministic
evidence path with provenance."""

from collections.abc import Sequence

from app.agents.planner import plan_adapted
from app.execution.synth import SYNTH_ATTEMPTS, synthesize_test_suite
from app.graph.build import GraphStageEvent, stream_graph
from app.graph.build import _stage_did_work as stage_did_work  # pyright: ignore[reportPrivateUsage]
from app.graph.state import RawInput
from app.llm.base import ChatMessage, ChatResult
from app.schemas.input import CodeBlock, StructuredInput
from app.schemas.plan import TeachingPlan
from tests.input.fakes import FakeLLMClient


def _plan(difficulty: str = "medium", rationale: list[str] | None = None) -> TeachingPlan:
    return TeachingPlan(
        difficulty=difficulty,  # pyright: ignore[reportArgumentType]
        assistance_level="concept",
        solution_strategy="socratic_hints",
        topic="trees",
        skill_level=0.5,
        rationale=rationale or [],
    )


def test_sandbox_stages_count_only_when_they_ran() -> None:
    assert not stage_did_work("execute_code", None)
    assert not stage_did_work("execute_code", {})
    assert stage_did_work("execute_code", {"execution_result": object()})
    assert not stage_did_work("verify", {"verification": None})
    assert stage_did_work("verify", {"verification": object()})
    assert stage_did_work("plan_teaching", None)  # every other node always counts


async def test_a_hint_turn_streams_no_sandbox_steps() -> None:
    """F9: "Running your code in the sandbox" was shown when nothing ran."""
    nodes = [
        event.node
        async for event in stream_graph(RawInput(text="what is a trie?"), llm=FakeLLMClient())
        if isinstance(event, GraphStageEvent)
    ]
    assert "execute_code" not in nodes
    assert "verify" not in nodes
    assert nodes[-1] == "update_learner_model"


def test_adapted_only_when_profile_evidence_changed_the_plan() -> None:
    assert not plan_adapted(None)
    assert not plan_adapted(_plan())  # fresh account: PRIOR -> medium, no profile rule
    assert not plan_adapted(_plan(rationale=["escalation_denied_no_explicit_ask"]))
    assert plan_adapted(_plan(difficulty="easy"))
    assert plan_adapted(_plan(rationale=["weak_skill"]))
    assert plan_adapted(_plan(rationale=["prefers_hints"]))


class _Runner:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, request: object) -> object:
        del request
        self.calls += 1
        raise AssertionError("not reached: every proposal here fails validation first")


class _SequencedLLM(FakeLLMClient):
    def __init__(self, replies: Sequence[str]) -> None:
        super().__init__(chat_content=replies[0])
        self._replies = list(replies)

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        self.chat_calls.append(messages)
        assert temperature == 0.0  # B2: synthesis is deterministic
        content = self._replies[min(len(self.chat_calls) - 1, len(self._replies) - 1)]
        return ChatResult(content=content, provider="fake", model="fake")


async def test_synthesis_is_retried_a_bounded_number_of_times() -> None:
    problem = StructuredInput(
        source="text",
        problem="Return the sum.",
        code=[CodeBlock(content="def total(nums):\n    return 0\n")],
    )
    llm = _SequencedLLM(["not json"])
    suite = await synthesize_test_suite(problem, llm, _Runner())  # pyright: ignore[reportArgumentType]
    assert suite is None
    assert len(llm.chat_calls) == SYNTH_ATTEMPTS


async def test_an_llm_error_is_not_retried() -> None:
    """P5 review: only a proposal that failed validation is retried."""
    problem = StructuredInput(
        source="text",
        problem="Return the sum.",
        code=[CodeBlock(content="def total(nums):\n    return 0\n")],
    )
    llm = FakeLLMClient(raise_chat=True)
    assert await synthesize_test_suite(problem, llm, _Runner()) is None  # pyright: ignore[reportArgumentType]
    assert len(llm.chat_calls) == 1


def test_a_fallback_response_cites_nothing() -> None:
    from app.response.generate import generate_response
    from app.schemas.agent_results import ExplainResult

    generated = generate_response(
        result=ExplainResult(citations=["Trie - Overview"]), plan=None, fallback_text="sorry"
    )
    assert generated.text == "sorry"
    assert generated.citations == []
