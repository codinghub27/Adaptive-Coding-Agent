"""The Longest Palindromic Substring conversation (owner, 2026-10-09).

What the tutor did, turn by turn, and the test that pins the correction:

A. "could not understand" -> the same abstract explanation and question again.
B. "how do I find the pattern?" -> a catalogue of techniques.
C. A SyntaxError with no code -> "it is the indentation", stated as fact.
D. The code plus the same SyntaxError -> a wrong-answer report, the
   SyntaxError never mentioned again. (The cause: type hints under Python 2.)
E. "give correct code in the given LeetCode way" -> a standalone function.
F. "explain with code" -> an even-centres-only program titled "solution",
   beside a claim of O(n) time.
G. "passed 8/8" with nothing said about what was not run.
H. Getting stuck on the pattern, again and again, and the profile not knowing.

These are deterministic: they check the decision, the prompts each agent is
given and the text built in code. Whether a model then follows a prompt is
measured live (`eval.behavior.live_scenarios`, scenario 21), not here.
"""

import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

from app.agents.concept import (
    _OPEN_SYSTEM,  # pyright: ignore[reportPrivateUsage]
    _SYSTEM,  # pyright: ignore[reportPrivateUsage]
    answer_concept,
)
from app.agents.debugger import _TRACEBACK_SYSTEM  # pyright: ignore[reportPrivateUsage]
from app.agents.planner import wants_the_code
from app.execution.base import canonical
from app.execution.synth import (
    _REFERENCE_SYSTEM,  # pyright: ignore[reportPrivateUsage]
    verified_reference,
)
from app.graph.nodes import (
    GAP_PATTERN,
    _build_learning_event,  # pyright: ignore[reportPrivateUsage]
    _is_the_tutors_code,  # pyright: ignore[reportPrivateUsage]
    _submission_interface,  # pyright: ignore[reportPrivateUsage]
)
from app.graph.state import AgentOutcome, AgentState, GraphContext, RawInput
from app.graph.subgraphs.debug import (
    _environment_note,  # pyright: ignore[reportPrivateUsage]
    _in_their_style,  # pyright: ignore[reportPrivateUsage]
)
from app.graph.subgraphs.dsa import _VERIFIED_REVEAL_TEXT  # pyright: ignore[reportPrivateUsage]
from app.input.intent import INTENT_SYSTEM_PROMPT
from app.input.snippet import submission_interface
from app.memory.profile import PRIOR, UNSOLVED_SCORE, smooth
from app.schemas.conversation import MessageView
from app.schemas.decision import TurnDecision
from app.schemas.execution import CaseResult, ExecutionRequest, ExecutionResult, Verdict
from app.schemas.input import CodeBlock, StructuredInput
from app.schemas.intent import Intent, IntentResult
from app.schemas.plan import TeachingPlan
from app.schemas.tutoring import SessionProgress
from app.tutoring.adaptation import adapt, tutor_state_block
from tests.input.fakes import FakeLLMClient

_STATEMENT = (
    "Longest Palindromic Substring\n\nGiven a string s, return the longest palindromic "
    'substring in s.\n\nExample 1:\nInput: s = "cbbd"\nOutput: "bb"\n'
)
#: What the learner pasted: LeetCode's Python 2 template around the tutor's
#: own example, type hints and all.
_PASTED = '''class Solution(object):
    def longestPalindrome(self, s):
        """
        :type s: str
        :rtype: str
        """
        def expand(l: int, r: int) -> str:
            while l >= 0 and r < len(s) and s[l] == s[r]:
                l -= 1
                r += 1
            return s[l + 1:r]

        best = ""
        for i in range(len(s) - 1):
            # only even centres to show the missed case if odd is ignored
            cand = expand(i, i + 1)
            if len(cand) > len(best):
                best = cand
        return best
'''
_ERROR = (
    "SyntaxError: invalid syntax\n    def expand(l: int, r: int) -> str:\nLine 7  (Solution.py)"
)
_TUTORS_EXAMPLE = """Expand-around-center solution

```python
def longest_palindrome(s: str) -> str:
    def expand(l: int, r: int) -> str:
        while l >= 0 and r < len(s) and s[l] == s[r]:
            l -= 1
            r += 1
        return s[l + 1:r]

    best = ""
    for i in range(len(s) - 1):
        cand = expand(i, i + 1)
        if len(cand) > len(best):
            best = cand
    return best
```
"""


def _step(evidence: str = "none") -> TurnDecision:
    return TurnDecision.model_validate({"subject": "active", "move": "step", "evidence": evidence})


def _plan(topic: str | None = "two_pointers") -> TeachingPlan:
    return TeachingPlan(
        difficulty="medium",
        assistance_level="concept",
        solution_strategy="socratic_hints",
        topic=topic,
        skill_level=PRIOR,
    )


def _said(role: str, content: str) -> MessageView:
    return MessageView.model_validate(
        {
            "id": uuid4(),
            "conversation_id": uuid4(),
            "seq": 1,
            "role": role,
            "content": content,
            "intent": None,
            "created_at": datetime.now(UTC),
        }
    )


# --- A: a confused learner ----------------------------------------------------


def test_a_the_tutor_is_told_what_to_switch_to_before_it_reads_the_confusion() -> None:
    """The first reading did not see "could not understand" as stuck, so the
    solver got no instruction and paraphrased itself. It is now told, on every
    tutoring step, what to do if this message turns out to be a miss."""
    adaptation = adapt(decision=_step(), pitch="intermediate", topic="x", evidence_log=[])
    assert adaptation.representation == "plain"
    block = tutor_state_block(_step(), adaptation)
    assert "if_they_did_not_follow" in block
    assert '"could not understand"' in block
    assert "do NOT ask the same question again" in block
    assert "ONE simple sentence" in block
    assert "with its indices" in block
    assert "one move at a time" in block
    assert "Every index and value must agree with that input" in block


def test_a_a_miss_the_agent_found_is_carried_out_and_said() -> None:
    adaptation = adapt(decision=_step(), pitch="intermediate", topic="x", evidence_log=[])
    switched = adaptation.after_a_miss()
    assert switched.representation == "worked_example"
    assert switched.adapted is True
    # A second miss in a row moves on to the next representation, not the same one.
    again = adapt(decision=_step(), pitch="intermediate", topic="x", evidence_log=["stuck"])
    assert again.if_stuck == "diagram"


def test_a_the_classifier_is_told_how_confusion_is_worded() -> None:
    assert '"could not understand"' in INTENT_SYSTEM_PROMPT
    assert '"I\'m confused"' in INTENT_SYSTEM_PROMPT


# --- B: pattern recognition as a method -----------------------------------------


def test_b_the_explainer_teaches_the_decision_process_not_a_catalogue() -> None:
    for prompt in (_SYSTEM, _OPEN_SYSTEM):
        assert "do NOT list techniques and their definitions" in prompt
        assert "(1) the input and the required output" in prompt
        assert "(4) the constraints and the complexity they allow" in prompt
        assert "(7) why the chosen one fits THIS problem" in prompt
        assert "ask them to do step 1 and step 2 on a problem of their own" in prompt


async def test_b_asking_how_to_find_the_pattern_is_recorded_as_that() -> None:
    reply = json.dumps({"answer": "Start from the input.", "about_pattern_recognition": True})
    answer = await answer_concept(
        StructuredInput(source="text", question="how do I find the pattern in a new problem?"),
        (),
        FakeLLMClient(chat_content=reply),
    )
    assert answer.pattern_recognition is True


# --- C and D: a syntax error is not a logic error -------------------------------


def test_c_a_bare_syntax_error_is_read_as_hypotheses_and_as_a_version_question() -> None:
    assert "never as fact" in _TRACEBACK_SYSTEM
    assert "never say you inspected code that was not shown" in _TRACEBACK_SYSTEM
    assert 'the language option "Python" is Python 2' in _TRACEBACK_SYSTEM
    assert "before indentation" in _TRACEBACK_SYSTEM
    assert "do not discuss the algorithm's logic in the same breath" in _TRACEBACK_SYSTEM


def _pasted() -> StructuredInput:
    return StructuredInput(
        source="text",
        question="full code and the error",
        code=[CodeBlock(content=_PASTED, language="python")],
        error=_ERROR,
    )


def _wrong_answer() -> Verdict:
    return Verdict(
        status="fail", category="wrong_answer", summary="3/8", cases_passed=3, cases_total=8
    )


def test_d_the_reported_syntax_error_is_answered_first_and_by_itself() -> None:
    """The sandbox parsed the code (Python 3) and found a wrong answer. The
    learner's SyntaxError is a different failure, in their environment."""
    note = _environment_note(_pasted(), _wrong_answer())
    assert note is not None
    assert "does not happen in the sandbox" in note
    assert "type hints, which Python 2 cannot read" in note
    assert '"Python3"' in note
    assert "def expand(l, r):" in note  # the minimal correction, as one line
    assert "wrong" not in note.lower()  # nothing about the logic in it


def test_d_no_such_note_when_it_does_not_apply() -> None:
    silent = _pasted().model_copy(update={"error": None})
    assert _environment_note(silent, _wrong_answer()) is None
    here_too = Verdict(status="fail", category="syntax_error", summary="x")
    assert _environment_note(_pasted(), here_too) is None  # it really is a syntax error
    other = _pasted().model_copy(update={"error": "KeyError: 3"})
    assert _environment_note(other, _wrong_answer()) is None


def test_d_the_fix_for_a_python_2_site_carries_no_hints() -> None:
    fix = _PASTED.replace("range(len(s) - 1)", "range(len(s))")
    plain = _in_their_style(fix, _pasted(), plain=True)
    assert plain is not None
    assert "def expand(l, r):" in plain
    assert "->" not in plain
    # Otherwise hints the learner wrote themselves are kept.
    kept = _in_their_style(fix, _pasted())
    assert kept is not None and "def expand(l: int, r: int) -> str:" in kept


def test_d_code_the_tutor_wrote_is_not_evidence_about_the_learner() -> None:
    """The pasted code was the tutor's own partial example. It failed its
    tests and the learner's skill dropped 0.12."""
    state = AgentState(
        input=RawInput(text="x"),
        structured_input=_pasted().model_copy(update={"problem": _STATEMENT}),
        recent_context=[_said("assistant", _TUTORS_EXAMPLE)],
        intent=IntentResult(intent=Intent.CODE_DEBUG, confidence=0.9, source="llm"),
        plan=_plan(),
        route="debug",
        suite_source="extracted",
        agent_output=AgentOutcome(text="x", topic="two_pointers", solved=False),
    )
    assert _is_the_tutors_code(state) is True
    event = _build_learning_event(state, GraphContext(llm=FakeLLMClient()), "two_pointers")
    assert event.solved is None

    their_own = state.model_copy(update={"recent_context": []})
    assert _is_the_tutors_code(their_own) is False
    assert (
        _build_learning_event(their_own, GraphContext(llm=FakeLLMClient()), "two_pointers").solved
        is False
    )


# --- E: the submission interface --------------------------------------------------


def test_e_the_interface_is_read_from_the_learners_own_code() -> None:
    assert submission_interface(_PASTED) == (
        "class Solution with the method longestPalindrome(self, s)"
    )
    assert submission_interface("def two_sum(nums, target):\n    return []\n") == (
        "a function two_sum(nums, target)"
    )
    assert submission_interface("x = 1\n") is None
    assert submission_interface("def broken(:\n") is None


def test_e_it_is_still_known_on_a_later_turn_that_brings_no_code() -> None:
    progress = SessionProgress.empty().model_copy(
        update={
            "last_attempt": [CodeBlock(content=_PASTED, language="python")],
            "last_attempt_key": "_p1",
        }
    )
    later = AgentState(
        input=RawInput(text="x"),
        structured_input=StructuredInput(
            source="text", problem=_STATEMENT, question="don't use nonlocal"
        ),
        session_progress=progress,
        problem_key="_p1",
    )
    assert (
        _submission_interface(later) == "class Solution with the method longestPalindrome(self, s)"
    )
    assert _submission_interface(later.model_copy(update={"problem_key": "_p2"})) is None


class _PassingRunner:
    """Stands in for the sandbox: every case passes; nothing is executed."""

    def __init__(self) -> None:
        self.calls: list[ExecutionRequest] = []

    async def run(self, request: ExecutionRequest) -> ExecutionResult:
        self.calls.append(request)
        assert request.tests is not None
        cases = [
            CaseResult(
                name=case.name,
                passed=True,
                actual=case.expected,
                actual_repr=str(case.expected),
                actual_sha256=hashlib.sha256(canonical(case.expected).encode()).hexdigest(),
                duration_ms=1.0,
            )
            for case in request.tests.cases
        ]
        return ExecutionResult(status="passed", phase="tests", cases=cases)


_CLASS_SOLUTION = (
    "class Solution:\n"
    "    def longestPalindrome(self, s):\n"
    "        def expand(left, right):\n"
    "            while left >= 0 and right < len(s) and s[left] == s[right]:\n"
    "                left -= 1\n"
    "                right += 1\n"
    "            return left + 1, right - 1\n\n"
    "        start, end = 0, 0\n"
    "        for i in range(len(s)):\n"
    "            for lo, hi in (expand(i, i), expand(i, i + 1)):\n"
    "                if hi - lo > end - start:\n"
    "                    start, end = lo, hi\n"
    "        return s[start:end + 1]\n"
)


async def test_e_a_class_solution_is_asked_for_checked_and_shown_as_a_class() -> None:
    proposal = json.dumps(
        {
            "entrypoint": "longestPalindrome",
            "reference_solution": _CLASS_SOLUTION,
            "cases": [{"name": "single", "args": ["a"], "kwargs": {}, "expected": "a"}],
            "approach": "Expand around every centre, odd and even.",
            "complexity_time": "O(n^2)",
            "complexity_space": "O(1)",
        }
    )
    llm = FakeLLMClient(chat_content=proposal)
    runner = _PassingRunner()
    solution = await verified_reference(
        StructuredInput(source="text", problem=_STATEMENT, question="give code, no nonlocal"),
        llm,
        runner,
        interface="class Solution with the method longestPalindrome(self, s)",
        conversation="<conversation_so_far>\nlearner: in the leetcode way\n</conversation_so_far>",
    )
    asked = llm.chat_calls[0][1].content
    assert (
        "<required_interface>\nclass Solution with the method longestPalindrome(self, s)" in asked
    )
    assert "learner: in the leetcode way" in asked

    assert solution is not None and solution.verified is True
    # Shown: the class, exactly as it would be submitted.
    assert solution.code == _CLASS_SOLUTION
    assert "nonlocal" not in solution.code
    # Run: the same class, plus an entry point the test harness can call.
    assert solution.request is not None
    assert solution.request.code.startswith(_CLASS_SOLUTION.rstrip("\n"))
    assert "def longestPalindrome(s):" in solution.request.code
    assert solution.request.tests is not None
    assert solution.request.tests.entrypoint == "longestPalindrome"
    assert (solution.complexity_time, solution.complexity_space) == ("O(n^2)", "O(1)")


def test_e_the_code_writer_is_told_about_formats_restrictions_and_earlier_asks() -> None:
    assert "'don't use nonlocal'" in _REFERENCE_SYSTEM
    assert "'in LeetCode format' means `class Solution:`" in _REFERENCE_SYSTEM
    assert "MUST define exactly that class and method" in _REFERENCE_SYSTEM
    assert "they asked for earlier still applies now" in _REFERENCE_SYSTEM
    assert "NO type hints" in _REFERENCE_SYSTEM


def test_e_explain_with_code_on_an_open_problem_is_an_ask_for_the_code() -> None:
    read = IntentResult(
        intent=Intent.CONCEPT_EXPLANATION, confidence=0.9, source="llm", asks_for_code=True
    )
    assert wants_the_code(read, "explain with code") is True
    # A learning ask that does not name the code is still not a demand for it.
    assert wants_the_code(read, "explain how to start") is False
    not_asked = read.model_copy(update={"asks_for_code": False})
    assert wants_the_code(not_asked, "explain without code please") is False


# --- F: complete, or labelled partial; and costs that add up ----------------------


def test_f_a_partial_program_must_say_so_and_costs_must_be_counted() -> None:
    for prompt in (_SYSTEM, _OPEN_SYSTEM):
        assert 'must have a title that starts with "Partial:"' in prompt
        assert "never present part of a solution as the solution" in prompt
        assert "is O(n^2), never O(n) because there is one outer loop" in prompt
    assert "both odd and even length" in _REFERENCE_SYSTEM
    assert "COMPLETE for the problem as stated" in _REFERENCE_SYSTEM
    assert "is O(n^2), not O(n)" in _REFERENCE_SYSTEM


# --- G: what was verified, and what was not ----------------------------------------


def test_g_a_reveal_says_what_was_not_run_and_ties_do_not_fail_good_code() -> None:
    assert "checked, not proven" in _VERIFIED_REVEAL_TEXT
    assert "hidden tests were not run" in _VERIFIED_REVEAL_TEXT
    assert "Choose test cases whose correct answer is UNIQUE" in _REFERENCE_SYSTEM
    assert "return the one the example shows" in _REFERENCE_SYSTEM


# --- H: skill at the level of the gap -----------------------------------------------


def test_h_one_failed_run_is_one_bug_not_a_weak_learner() -> None:
    from app.agents.planner import WEAK_SKILL

    once = smooth(PRIOR, UNSOLVED_SCORE)
    assert once >= WEAK_SKILL
    assert smooth(once, UNSOLVED_SCORE) < WEAK_SKILL


def test_h_not_knowing_how_to_start_is_recorded_as_the_pattern_gap() -> None:
    state = AgentState(
        input=RawInput(text="x"),
        structured_input=StructuredInput(
            source="text", problem=_STATEMENT, question="I don't know how to start"
        ),
        intent=IntentResult(intent=Intent.DSA_SOLVE, confidence=0.9, source="llm"),
        plan=_plan(),
        route="dsa",
        problem_relation="new",
        decision=_step("stuck"),
        agent_output=AgentOutcome(text="x", topic="two_pointers"),
    )
    event = _build_learning_event(state, GraphContext(llm=FakeLLMClient()), "two_pointers")
    assert GAP_PATTERN in event.errors


def test_h_a_recurring_pattern_gap_changes_the_first_step_and_says_so() -> None:
    known = adapt(
        decision=_step(),
        pitch="intermediate",
        topic="two_pointers",
        evidence_log=[],
        first_turn=True,
        pattern_gap=True,
    )
    assert known.adapted is True
    assert "finding the pattern is the hard part" in known.notes[0]
    block = tutor_state_block(_step(), known)
    assert "recurring_gap: pattern recognition" in block
    assert "Do not name the technique first" in block
    unknown = adapt(
        decision=_step(),
        pitch="intermediate",
        topic="two_pointers",
        evidence_log=[],
        first_turn=True,
    )
    assert unknown.adapted is False


def test_h_the_kind_of_gap_picks_the_kind_of_help() -> None:
    plain = adapt(decision=_step(), pitch="intermediate", topic="x", evidence_log=[])
    coding = tutor_state_block(_step("implementation_error"), plain)
    assert "gap: implementation. They have the idea; do not re-teach the algorithm" in coding
    concept = tutor_state_block(_step("conceptual_misconception"), plain)
    assert "gap: concept." in concept
    named = tutor_state_block(_step("terminology_error"), plain)
    assert "The reasoning is right" in named


def test_a_an_opening_message_gets_no_instruction_about_a_missed_explanation() -> None:
    """Measured live: given the conditional line on a first message, the model
    applied it and turned a first explanation into a stopped worked example."""
    opening = adapt(
        decision=_step(), pitch="intermediate", topic="x", evidence_log=[], first_turn=True
    )
    assert "if_they_did_not_follow" not in tutor_state_block(_step(), opening)
    later = adapt(decision=_step(), pitch="intermediate", topic="x", evidence_log=[])
    assert "otherwise ignore this line" in tutor_state_block(_step(), later)


def test_d_a_judges_message_left_in_the_question_is_still_the_reported_error() -> None:
    """LeetCode's "SyntaxError: invalid syntax ... Line 7" is not a Python
    traceback, so the normaliser leaves it in the question, not in `error`."""
    as_typed = _pasted().model_copy(update={"error": None, "question": f"full code\n{_ERROR}"})
    note = _environment_note(as_typed, _wrong_answer())
    assert note is not None and "Python 2" in note
