"""ADAPTIVE-upgrade P4: the escalation policy per teaching mode (AD-4), the
verified-only reveal, and rung quality (no truncation, no generic L0)."""

import hashlib
import json

import pytest

from app.agents.hint_engine import HintProgress
from app.agents.hint_engine import (
    _first_clause as first_clause,  # pyright: ignore[reportPrivateUsage]
)
from app.agents.planner import ProblemAnalysis, build_plan
from app.execution.synth import verified_reference
from app.execution.verification import canonical
from app.graph.nodes import _refused_ask_at_ceiling  # pyright: ignore[reportPrivateUsage]
from app.graph.state import AgentState, RawInput
from app.schemas.agent_results import HintLevel
from app.schemas.execution import CaseResult, ExecutionRequest, ExecutionResult
from app.schemas.input import StructuredInput
from app.schemas.intent import Intent, IntentResult
from app.schemas.plan import TeachingMode
from app.schemas.profile import LearnerProfileView
from tests.input.fakes import FakeLLMClient

_ANALYSIS = ProblemAnalysis(topic="trees", skill_level=0.5, topic_source="title")
_ASK = StructuredInput(source="text", question="give full answer", problem="A tree problem.")
_AT_CEILING = HintProgress(
    last_level=HintLevel.L3_CONCRETE_IDEA, ceiling=HintLevel.L3_CONCRETE_IDEA
)


def _plan(
    mode: TeachingMode,
    progress: HintProgress,
    inp: StructuredInput = _ASK,
    intent: Intent = Intent.DSA_SOLVE,
) -> list[str]:
    plan = build_plan(
        IntentResult(intent=intent, confidence=0.9, source="llm"),
        LearnerProfileView.empty(),
        _ANALYSIS,
        hint_progress=progress,
        structured_input=inp,
        teaching_mode=mode,
    )
    return [plan.assistance_level, *plan.rationale]


@pytest.mark.parametrize(
    "text",
    [
        "give full answer",
        "give full code",
        "give code for that",
        "show me the solution",
        "just give me it",
    ],
)
def test_explicit_asks_from_the_live_session_are_recognised(text: str) -> None:
    """F4: the asks from the real UI session never matched the old regex."""
    inp = StructuredInput(source="text", question=text, problem="A tree problem.")
    assert "escalation_denied_no_explicit_ask" not in _plan("guidance", _AT_CEILING, inp)


def test_guidance_reveals_on_an_explicit_ask_at_the_ceiling() -> None:
    assert _plan("guidance", _AT_CEILING)[0] == "full"


def test_no_mode_reveals_before_the_ceiling() -> None:
    below = HintProgress(last_level=HintLevel.L1_WHAT_TO_TRACK, ceiling=HintLevel.L3_CONCRETE_IDEA)
    for mode in ("guidance", "balanced", "challenge"):
        assert _plan(mode, below)[0] != "full"


def test_balanced_needs_a_second_ask_or_a_verified_attempt() -> None:
    first = _plan("balanced", _AT_CEILING)
    assert first[0] != "full"
    assert "escalation_needs_second_ask" in first
    second = _AT_CEILING.model_copy(update={"asks_at_ceiling": 1})
    assert _plan("balanced", second)[0] == "full"
    attempted = _AT_CEILING.model_copy(update={"has_verified_attempt": True})
    assert _plan("balanced", attempted)[0] == "full"


def test_challenge_still_requires_a_verified_attempt() -> None:
    assert _plan("challenge", _AT_CEILING.model_copy(update={"asks_at_ceiling": 5}))[0] != "full"
    attempted = _AT_CEILING.model_copy(update={"has_verified_attempt": True})
    assert _plan("challenge", attempted)[0] == "full"


def test_a_follow_up_ask_classified_as_a_hint_still_counts() -> None:
    assert _plan("guidance", _AT_CEILING, intent=Intent.DSA_HINT)[0] == "full"


def test_refused_asks_at_the_ceiling_are_counted_only_when_refused_for_effort() -> None:
    def state(rationale: list[str]) -> AgentState:
        from app.schemas.plan import TeachingPlan

        plan = TeachingPlan(
            difficulty="medium",
            assistance_level="concept",
            solution_strategy="socratic_hints",
            topic="trees",
            skill_level=0.5,
            rationale=rationale,
        )
        return AgentState(input=RawInput(text="x"), plan=plan)

    assert _refused_ask_at_ceiling(state(["escalation_denied_no_verified_attempt"])) == 1
    assert (
        _refused_ask_at_ceiling(
            state(
                ["escalation_denied_ceiling_not_reached", "escalation_denied_no_verified_attempt"]
            )
        )
        == 0
    )
    assert _refused_ask_at_ceiling(state(["escalated"])) == 0


# --- rung quality ----------------------------------------------------------


def test_a_sentence_is_never_cut() -> None:
    text = "Short one. " + "word " * 100 + "end."
    assert first_clause(text, 50) == "Short one."
    assert first_clause("word " * 100 + "end.", 50) == ""
    assert "..." not in first_clause("A whole sentence that fits.", 200)


# --- verified reference ----------------------------------------------------


class _Runner:
    def __init__(self, passed: bool) -> None:
        self.passed = passed
        self.calls: list[ExecutionRequest] = []

    async def run(self, request: ExecutionRequest) -> ExecutionResult:
        self.calls.append(request)
        actual = 2 if self.passed else 3
        digest = hashlib.sha256(canonical(actual).encode("utf-8")).hexdigest()
        return ExecutionResult(
            status="passed" if self.passed else "failed",
            phase="tests",
            cases=[
                CaseResult(
                    name="c1",
                    passed=self.passed,
                    actual=actual,
                    actual_repr=str(actual),
                    actual_sha256=digest,
                    duration_ms=1.0,
                )
            ],
        )


_PROPOSAL = json.dumps(
    {
        "entrypoint": "add",
        "reference_solution": "def add(a, b):\n    return a + b\n",
        "cases": [{"name": "c1", "args": [1, 1], "kwargs": {}, "expected": 2}],
    }
)
_STATEMENT = StructuredInput(source="text", problem="Return a + b.", question="give full code")


async def test_a_reference_is_revealed_only_when_the_sandbox_passes_it() -> None:
    runner = _Runner(passed=True)
    solution = await verified_reference(_STATEMENT, FakeLLMClient(chat_content=_PROPOSAL), runner)
    assert solution is not None
    assert solution.verdict.status == "pass"
    assert solution.request.code == solution.code
    assert len(runner.calls) == 1


async def test_a_failing_reference_is_never_revealed() -> None:
    runner = _Runner(passed=False)
    llm = FakeLLMClient(chat_content=_PROPOSAL)
    assert await verified_reference(_STATEMENT, llm, runner) is None


async def test_no_statement_or_no_sandbox_means_no_reveal() -> None:
    llm = FakeLLMClient(chat_content=_PROPOSAL)
    no_statement = StructuredInput(source="text", question="give full code")
    assert await verified_reference(no_statement, llm, _Runner(passed=True)) is None
    assert await verified_reference(_STATEMENT, llm, None) is None
    assert llm.chat_calls == []


def test_the_problem_statement_is_never_read_as_an_ask() -> None:
    """Code review P4: "return the answer modulo 10^9+7" is not a request."""
    inp = StructuredInput(
        source="text", question="next hint", problem="Return the answer modulo 10^9+7."
    )
    assert "escalation_denied_no_explicit_ask" in _plan("guidance", _AT_CEILING, inp)


@pytest.mark.parametrize(
    "text", ["I'll write code myself, just a hint", "can you explain the code for this?"]
)
def test_ordinary_sentences_are_not_asks(text: str) -> None:
    inp = StructuredInput(source="text", question=text, problem="A tree problem.")
    assert _plan("guidance", _AT_CEILING, inp)[0] != "full"
