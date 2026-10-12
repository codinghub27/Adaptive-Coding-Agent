"""A question about the learner's code is answered, with the code run first.

Owner's conversation, 2026-10-12:

    what is the output of this code.        -> "Structure: module (lines 1-3)",
    a=b=[]                                     "Space: O(n)", "Execution (your
    a.append(1)                                code): not executed"
    print(b)
    why b as contains the same value as a   -> the outline and the cost again

The output was never stated and the code was never run.
"""

import json
from collections.abc import Sequence
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.code_answer import CodeRun, run_block, with_run
from app.graph.build import GraphRunResult, run_graph
from app.graph.state import RawInput
from app.llm.base import ChatMessage, ChatResult, LLMError
from app.schemas.agent_results import ExplainResult
from app.schemas.execution import ExecutionRequest, ExecutionResult
from tests.graph import test_conversation_regression as reg
from tests.graph.test_conversation_regression import (
    _Store,  # pyright: ignore[reportPrivateUsage]
    store,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)

_CODE = "a=b=[]\na.append(1)\nprint(b)"
_ASK = "what is the output of this code.\n" + _CODE
_WHY = "why b as contains the same value as a"
_ANSWER = "It prints [1]: `a=b=[]` binds both names to one list."
_WHY_ANSWER = "Because both names refer to the same list object."

#: Per message: what the classifier says. Each case sets its own.
_Labels = dict[str, dict[str, object]]


class _Runner:
    """Runs nothing: reports the script's output as the sandbox would."""

    def __init__(self, stdout: str = "[1]\n", status: str = "completed") -> None:
        self.requests: list[ExecutionRequest] = []
        self._stdout = stdout
        self._status = status

    async def run(self, request: ExecutionRequest) -> ExecutionResult:
        self.requests.append(request)
        return ExecutionResult(status=cast("object", self._status), stdout=self._stdout)  # type: ignore[arg-type]


class _LLM(reg._ScriptedLLM):  # pyright: ignore[reportPrivateUsage]
    def __init__(self, labels: _Labels) -> None:
        super().__init__()
        self.labels = labels
        self.code_users: list[str] = []
        self.classifier_users: list[str] = []
        self.classifier_down = False

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        system, user = messages[0].content, messages[-1].content
        content: str | None = None
        if system.startswith("You are the intent classifier"):
            self.classifier_users.append(user)
            if self.classifier_down:
                raise LLMError("rate limited")
            question = str(json.loads(user.split("\n")[1]).get("question", ""))
            for phrase, label in self.labels.items():
                if phrase in question:
                    content = json.dumps({"confidence": 0.9, **label})
        elif "asked something about it" in system:
            self.code_users.append(user)
            content = _WHY_ANSWER if _WHY in user.split("<learner_question>")[-1] else _ANSWER
        if content is None:
            return await super().chat(messages, temperature=temperature, max_tokens=max_tokens)
        self.systems.append(system)
        return ChatResult(content=content, provider="fake", model="fake")


class _Chat:
    def __init__(self, labels: _Labels, runner: _Runner | None = None) -> None:
        self.llm = _LLM(labels)
        self.runner = runner or _Runner()
        self.user_id: UUID = uuid4()
        self.conversation_id: UUID = uuid4()

    async def turn(self, text: str) -> GraphRunResult:
        return await run_graph(
            RawInput(text=text),
            llm=self.llm,
            session=cast("AsyncSession", reg._Session()),  # pyright: ignore[reportPrivateUsage]
            user_id=self.user_id,
            conversation_id=self.conversation_id,
            runner=self.runner,
        )


def _reply(result: GraphRunResult) -> str:
    assert result.state.response is not None
    return result.state.response


async def test_the_code_is_run_and_the_question_answered(store: _Store) -> None:  # noqa: F811
    chat = _Chat({"what is the output": {"intent": "CODE_EXPLAIN"}})
    asked = await chat.turn(_ASK)

    assert asked.state.route == "explain"
    # The learner's code went to the sandbox, as a script.
    assert [request.code for request in chat.runner.requests] == [_CODE]
    reply = _reply(asked)
    assert reply.startswith(_ANSWER)
    # The sandbox's own output, verbatim, under the answer.
    assert "Output (run in the sandbox):" in reply
    # Not the report, and no verdict invented for code nobody tested.
    for absent in ("Structure", "Cost", "O(n)", "not executed", "Execution"):
        assert absent not in reply
    # The model was given the real output to answer from.
    prompt = chat.llm.code_users[-1]
    assert "<sandbox_run>\nstatus: ran to the end\nstdout:\n[1]" in prompt
    assert f"<learner_code>\n{_CODE}\n</learner_code>" in prompt
    assert isinstance(asked.state.agent_result, ExplainResult)
    assert asked.state.agent_result.about_code
    assert store.progress.last_thread == "code"
    # A question about Python lists is not recorded as a DSA topic.
    assert asked.state.plan is not None
    assert asked.state.plan.topic is None
    assert not asked.state.events


async def test_a_followup_is_about_that_code(store: _Store) -> None:  # noqa: F811
    del store
    # As the live classifier returned it: a concept question, tied to the
    # last reply rather than to the code.
    chat = _Chat(
        {
            "what is the output": {"intent": "CODE_EXPLAIN"},
            _WHY: {"intent": "CONCEPT_EXPLANATION", "continues_last_reply": True},
        }
    )
    await chat.turn(_ASK)
    why = await chat.turn(_WHY)

    assert why.state.route == "explain"
    assert why.state.problem_relation == "followup"
    reply = _reply(why)
    assert reply.startswith(_WHY_ANSWER)
    # Answered with the code and the exchange in front of it...
    prompt = chat.llm.code_users[-1]
    assert f"<learner_code>\n{_CODE}\n</learner_code>" in prompt
    assert "<conversation_so_far>" in prompt
    assert "learner: what is the output of this code." in prompt
    # ...and the output is not printed a second time.
    assert "Output (run in the sandbox)" not in reply
    for absent in ("Structure", "Cost", "not executed"):
        assert absent not in reply
    # The classifier was told what the last reply was.
    assert (
        "last_reply: an answer to the learner's question about the code"
        in (chat.llm.classifier_users[-1])
    )


async def test_the_followup_under_a_hint_label_is_still_about_the_code(store: _Store) -> None:  # noqa: F811
    del store
    chat = _Chat(
        {
            "what is the output": {"intent": "CODE_EXPLAIN"},
            _WHY: {"intent": "DSA_HINT", "refers_to_previous": True},
        }
    )
    await chat.turn(_ASK)
    why = await chat.turn(_WHY)

    # Not a bug hunt on three lines that work.
    assert why.state.route == "explain"
    assert _reply(why).startswith(_WHY_ANSWER)


async def test_a_new_concept_question_after_a_code_answer_stands_alone(store: _Store) -> None:  # noqa: F811
    del store
    chat = _Chat(
        {
            "what is the output": {"intent": "CODE_EXPLAIN"},
            "what is a trie": {"intent": "CONCEPT_EXPLANATION", "refers_to_previous": False},
        }
    )
    await chat.turn(_ASK)
    asked = len(chat.llm.code_users)
    trie = await chat.turn("what is a trie")

    assert trie.state.problem_relation != "followup"
    assert len(chat.llm.code_users) == asked


async def test_an_unread_code_turn_that_runs_is_answered_not_debugged(store: _Store) -> None:  # noqa: F811
    """With the classifier unavailable the turn is a debug turn by rule. The
    code runs and nothing was reported wrong: the question is answered."""
    del store
    chat = _Chat({})
    chat.llm.classifier_down = True
    asked = await chat.turn(_ASK)

    assert asked.state.route == "debug"
    reply = _reply(asked)
    assert reply.startswith(_ANSWER)
    assert "Output (run in the sandbox):" in reply
    # The record of the turn says what was done.
    assert asked.state.decision is not None
    assert asked.state.decision.move == "explain"
    assert "ran_clean_answered" in asked.state.decision.reasons
    for absent in ("inconclusive", "Not verified", "Correctness could not be checked"):
        assert absent not in reply


async def test_a_bug_report_still_goes_to_the_debugger(store: _Store) -> None:  # noqa: F811
    del store
    chat = _Chat({"find bugs": {"intent": "CODE_DEBUG"}})
    await chat.turn("find bugs in this code.\n" + _CODE)

    # A confident debug ask is debugged, clean run or not.
    assert not chat.llm.code_users


async def test_code_that_crashes_is_debugged(store: _Store) -> None:  # noqa: F811
    del store
    chat = _Chat({}, runner=_Runner(stdout="", status="runtime_error"))
    chat.llm.classifier_down = True
    await chat.turn(_ASK)

    assert not chat.llm.code_users


# --- the pieces ---------------------------------------------------------------


def test_the_run_goes_before_the_closing_question() -> None:
    run = CodeRun(ran=True, completed=True, stdout="7")
    said = with_run("It adds them.\n\nWhat would it print for 2 and 3?", run)

    assert said.split("\n\n")[0] == "It adds them."
    assert said.endswith("What would it print for 2 and 3?")
    assert "Output (run in the sandbox):" in said


def test_output_the_answer_already_quotes_is_not_printed_twice() -> None:
    run = CodeRun(ran=True, completed=True, stdout="[1]")
    said = with_run("The code prints:\n\n```\n[1]\n```\n\nBoth names share one list.", run)

    assert said.count("[1]") == 1
    assert "I ran your code in the sandbox" in said


def test_what_the_sandbox_did_is_said_in_its_own_words() -> None:
    assert "prints nothing" in run_block(CodeRun(ran=True, completed=True))
    crashed = run_block(CodeRun(ran=True, completed=False, error="NameError: name 'x'"))
    assert "stopped with an error" in crashed
    assert "NameError" in crashed
    assert run_block(CodeRun(ran=False, note="the code sandbox is not available")).startswith(
        "_Not executed"
    )


def test_a_number_in_the_answer_is_not_the_output_being_quoted() -> None:
    # The code prints 1; the answer mentions "line 1" and never states the
    # output. The real output must still be shown.
    run = CodeRun(ran=True, completed=True, stdout="1")
    said = with_run("Line 1 builds the list and `a.append(1)` adds to it.", run)

    assert "Output (run in the sandbox):" in said
    assert "I ran your code" not in said


def test_one_line_of_output_in_backticks_counts_as_quoted() -> None:
    run = CodeRun(ran=True, completed=True, stdout="[1]")
    said = with_run("It prints `[1]`, because both names share one list.", run)

    assert "Output (run in the sandbox):" not in said
    assert "I ran your code in the sandbox" in said


async def test_code_that_only_defines_functions_is_debugged(store: _Store) -> None:  # noqa: F811
    """It runs cleanly and prints nothing: there is no output to answer from,
    and "this gives the wrong answer" about a bare function needs test cases."""
    del store
    chat = _Chat({}, runner=_Runner(stdout=""))
    chat.llm.classifier_down = True
    await chat.turn("this gives the wrong answer for [3, 3]\n" + _CODE)

    assert not chat.llm.code_users
