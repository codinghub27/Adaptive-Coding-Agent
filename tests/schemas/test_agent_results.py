"""`DebugResult.to_outcome()` derives `solved` from learner evidence.

`initial_verdict` is the verdict on the code the learner actually submitted;
`final_verdict` is the verdict AFTER the debugger patched it. Only the former
is evidence about the learner -- see the docstring on `to_outcome` (P1b).
"""

from app.schemas.agent_results import DebugResult
from app.schemas.execution import Verdict

_PASS = Verdict(status="pass", summary="all tests pass", cases_passed=2, cases_total=2)
_FAIL = Verdict(status="fail", category="wrong_answer", summary="still failing")


def test_to_outcome_solved_reflects_initial_verdict_even_when_agent_fixed_it() -> None:
    """The agent patching a bug (final_verdict pass) is evidence the AGENT
    can fix bugs, not evidence the LEARNER's submitted code worked. Before
    P1b this read `final_verdict` here, which would have reported
    `solved=True` for code the learner submitted broken."""
    result = DebugResult(
        initial_verdict=_FAIL,
        final_verdict=_PASS,
        fixed=True,
        patched_code="def f(): return 1",
    )

    outcome = result.to_outcome()

    assert outcome.solved is False


def test_to_outcome_solved_true_when_learners_own_code_already_passed() -> None:
    result = DebugResult(initial_verdict=_PASS, final_verdict=_PASS, fixed=False)

    outcome = result.to_outcome()

    assert outcome.solved is True


def test_to_outcome_solved_none_when_initial_verdict_missing() -> None:
    """No initial verdict (nothing ran against the learner's own code) is
    absence of evidence, not evidence of failure, regardless of what the
    final verdict (if any) says."""
    result = DebugResult(initial_verdict=None, final_verdict=None, fixed=False)

    outcome = result.to_outcome()

    assert outcome.solved is None
