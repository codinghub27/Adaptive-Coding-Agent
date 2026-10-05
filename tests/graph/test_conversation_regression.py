"""Multi-turn regressions replayed on ONE conversation id through the real graph.

The failing session (LeetCode 678 from a screenshot): turn 2 "tell me name of
that problem" got "I'm not sure which problem", turns 3-4 got a study plan,
turn 5 got a generic template hint, and `'*'` rendered as `''`. Root cause was
routing, not storage -- the problem WAS stored after turn 1 -- so these tests
run every turn through `run_graph` against an in-memory stand-in for the
conversation store and assert on what each turn was routed to and said.

No database, no network: the store functions `app.graph.nodes` imports are
replaced, and the LLM is a scripted double keyed on each call's system prompt.
"""

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.hint_engine import HintProgress
from app.graph import nodes
from app.graph.build import GraphRunResult, run_graph
from app.graph.state import RawInput
from app.graph.subgraphs.dsa import corpus_chunks_by_pattern, resolved_topic
from app.knowledge.base import Retriever
from app.llm.base import ChatMessage, ChatResult
from app.response.format import protect_symbols
from app.schemas.agent_results import HintLevel
from app.schemas.conversation import MessageView
from app.schemas.input import ActiveProblem
from app.schemas.intent import Intent
from app.schemas.knowledge import RetrievalHit
from app.schemas.profile import LearnerProfileView
from app.schemas.tutoring import PendingCheck, SessionProgress

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

_PROBLEM_678 = (
    "678. Valid Parenthesis String\n\n"
    "Given a string s containing only three types of characters: '(', ')' and '*', "
    "return true if s is valid. '*' could be treated as a single right parenthesis ')' "
    "or a single left parenthesis '(' or an empty string.\n\n"
    'Example 1:\nInput: s = "(*)"\nOutput: true'
)

_STEP_L0 = (
    "The tricky part is the '*': it can stand for '(' , ')' or nothing at all. "
    "Take the string \"(*)\" -- what could the '*' be so the string stays valid?"
)
_STEP_L1 = (
    "Since a '*' has three choices, don't track one open count -- track the range of "
    "possible open counts, a low and a high, greedy from left to right. "
    'After reading "(*", what are the lowest and highest possible open counts?'
)

_INTENTS: dict[str, Intent] = {
    "how to solve this prob": Intent.DSA_SOLVE,
    "tell me name of that problem": Intent.GENERAL_GUIDANCE,
    "the screenshot i have shared problem name": Intent.GENERAL_GUIDANCE,
    "so you can't read previous conversations?": Intent.GENERAL_GUIDANCE,
    "give me step by step to solve": Intent.DSA_SOLVE,
    "Can you help me solve Two Sum": Intent.DSA_SOLVE,
    "Can you debug it": Intent.CODE_DEBUG,
    # What the live classifier does with a technique name on its own.
    "two pointers?": Intent.CONCEPT_EXPLANATION,
    "what is a trie": Intent.CONCEPT_EXPLANATION,
}


class _ScriptedLLM:
    """Answers by which prompt is calling; records which prompts were used."""

    def __init__(self) -> None:
        self.systems: list[str] = []
        self.solver_users: list[str] = []
        self.solver_calls = 0

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
        if system.startswith("You are the intent classifier"):
            # The classifier's payload is JSON, so match on its decoded question.
            question = str(json.loads(user.split("\n")[1]).get("question", ""))
            intent = next((v for k, v in _INTENTS.items() if k in question), Intent.DSA_HINT)
            content = json.dumps({"intent": intent.value, "confidence": 0.9, "rationale": "x"})
        elif system.startswith("You are the DSA-problem analysis engine"):
            self.solver_users.append(user)
            step = _STEP_L0 if self.solver_calls == 0 else _STEP_L1
            self.solver_calls += 1
            content = json.dumps(
                {
                    "pattern_slug": "greedy" if "partition" in user else "none",
                    "reply_verdict": (
                        "right_idea_wrong_name" if '"two pointers?"' in user else "none"
                    ),
                    "understanding": "Decide if s can be valid.",
                    "guided_step": step,
                }
            )
        elif system.startswith("You are a DSA mentor"):
            content = json.dumps({"answer": "## 6-Week DSA Study Plan", "used": [1]})
        else:
            content = "{}"
        return ChatResult(content=content, provider="fake", model="fake")

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise NotImplementedError

    async def vision(
        self, image: bytes, prompt: str, *, mime_type: str = "image/png"
    ) -> ChatResult:
        del image, prompt, mime_type
        content = json.dumps({"problem": _PROBLEM_678, "constraints": ["1 <= s.length <= 100"]})
        return ChatResult(content=content, provider="fake", model="fake-vision")

    def asked_for_a_study_plan(self) -> bool:
        return any(system.startswith("You are a DSA mentor") for system in self.systems)


class _Savepoint:
    async def __aenter__(self) -> "_Savepoint":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _Session:
    def begin_nested(self) -> _Savepoint:
        return _Savepoint()


class _Store:
    """What one conversation remembers between turns (the `thread_id`'s state)."""

    def __init__(self) -> None:
        self.messages: list[MessageView] = []
        self.active: ActiveProblem | None = None
        self.pending: PendingCheck | None = None
        self.progress = SessionProgress.empty()
        self.hints: dict[str, HintProgress] = {}


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> _Store:
    memory = _Store()

    async def get_profile(session: Any, user_id: UUID) -> LearnerProfileView:
        return LearnerProfileView.empty()

    async def get_recent_context(session: Any, user_id: UUID, cid: UUID) -> list[MessageView]:
        return list(memory.messages[-10:])

    async def add_turn(
        session: Any, user_id: UUID, cid: UUID, role: str, content: str, intent: Any = None
    ) -> MessageView:
        view = MessageView(
            id=uuid4(),
            conversation_id=cid,
            seq=len(memory.messages) + 1,
            role=cast("Any", role),
            content=content,
            intent=intent,
            created_at=datetime.now(UTC),
        )
        memory.messages.append(view)
        return view

    async def get_active_problem(session: Any, user_id: UUID, cid: UUID) -> ActiveProblem | None:
        return memory.active

    async def set_active_problem(session: Any, user_id: UUID, cid: UUID, active: Any) -> None:
        memory.active = active

    async def get_tutoring_state(session: Any, user_id: UUID, cid: UUID) -> Any:
        return memory.pending, memory.progress

    async def set_tutoring_state(
        session: Any, user_id: UUID, cid: UUID, pending: Any, progress: Any
    ) -> None:
        memory.pending, memory.progress = pending, progress

    async def get_hint_progress(session: Any, user_id: UUID, cid: UUID, topic: str) -> HintProgress:
        return memory.hints.get(topic, HintProgress())

    async def get_latest_hint_progress(session: Any, user_id: UUID, cid: UUID) -> None:
        return None

    async def save_hint_progress(
        session: Any, user_id: UUID, cid: UUID, topic: str, level: int, solved: bool, **kwargs: Any
    ) -> None:
        ceiling = kwargs.get("ceiling")
        memory.hints[topic] = HintProgress(
            last_level=HintLevel(level),
            solved=solved,
            ceiling=HintLevel(ceiling) if ceiling is not None else None,
        )

    async def record_event(session: Any, user_id: UUID, event: Any) -> Any:
        raise RuntimeError("no event store in this test")

    fakes: dict[str, object] = {
        "get_profile": get_profile,
        "get_recent_context": get_recent_context,
        "add_turn": add_turn,
        "get_active_problem": get_active_problem,
        "set_active_problem": set_active_problem,
        "get_tutoring_state": get_tutoring_state,
        "set_tutoring_state": set_tutoring_state,
        "get_hint_progress": get_hint_progress,
        "get_latest_hint_progress": get_latest_hint_progress,
        "save_hint_progress": save_hint_progress,
        "record_event": record_event,
    }
    for name, fake in fakes.items():
        monkeypatch.setattr(nodes, name, fake)
    return memory


class _Conversation:
    """One conversation: every turn runs the graph with the same ids."""

    def __init__(self, retriever: Retriever | None = None) -> None:
        self.llm = _ScriptedLLM()
        self.user_id = uuid4()
        self.conversation_id = uuid4()
        self.retriever = retriever

    async def turn(self, text: str, *, image: bytes | None = None) -> GraphRunResult:
        raw = RawInput(text=text, image=image, image_mime="image/png" if image else None)
        return await run_graph(
            raw,
            llm=self.llm,
            session=cast("AsyncSession", _Session()),
            user_id=self.user_id,
            conversation_id=self.conversation_id,
            retriever=self.retriever,
        )


def _reply(result: GraphRunResult) -> str:
    assert result.state.response is not None
    return result.state.response


# --- the failing five-turn conversation ---------------------------------------


async def test_screenshot_problem_stays_the_subject_for_five_turns(store: _Store) -> None:
    chat = _Conversation()

    # T1: a screenshot of LeetCode 678 + "how to solve this prob".
    t1 = await chat.turn("how to solve this prob", image=_PNG)
    assert t1.state.route == "dsa"
    assert store.active is not None
    assert store.active.problem.source == "image"
    first = _reply(t1)
    assert "pin down the inputs" not in first  # the generic template rung
    assert "`*`" in first  # the symbol survives markdown ...
    assert "`(*)`" in first
    assert "''" not in first  # ... instead of collapsing to empty quotes
    assert "*" not in first.replace("`*`", "").replace("`(*)`", "")  # none left bare
    assert first.count("?") == 1  # one step, ONE question
    assert "## " not in first  # conversational: no forced section headers
    assert "```" not in first

    # T2: asks for the problem's name -> answered from stored state.
    t2 = await chat.turn("tell me name of that problem")
    assert t2.state.route == "meta"
    assert "Valid Parenthesis String" in _reply(t2)
    assert "screenshot" in _reply(t2)

    # T3: same question, phrased around the screenshot -> never a study plan.
    t3 = await chat.turn("the screenshot i have shared problem name.")
    assert t3.state.route == "meta"
    assert "Valid Parenthesis String" in _reply(t3)

    # T4: "can't you read previous conversations?" -> confirms history access.
    t4 = await chat.turn("so you can't read previous conversations?")
    assert t4.state.route == "meta"
    assert "I can see this whole conversation" in _reply(t4)
    assert "Valid Parenthesis String" in _reply(t4)

    for result in (t2, t3, t4):
        assert result.state.route != "explain"
        assert "study plan" not in _reply(result).lower()
        assert result.state.events == []  # a meta question is not learning evidence
    assert not chat.llm.asked_for_a_study_plan()
    assert chat.llm.solver_calls == 1  # meta turns cost no solver call

    # T5: names the problem by its title -> the NEXT rung on the same problem,
    # worded about this problem's mechanics, with the chat in the prompt.
    t5 = await chat.turn("give me step by step to solve the valid parenthesis string problem")
    assert t5.state.route == "dsa"
    assert t5.state.problem_relation == "followup"
    assert t5.state.structured_input is not None
    assert t5.state.structured_input.problem == _PROBLEM_678
    generated = t5.state.generated_response
    assert generated is not None
    assert generated.hint_level == HintLevel.L1_WHAT_TO_TRACK
    fifth = _reply(t5)
    assert "range of possible open counts" in fifth
    assert "greedy" in fifth
    assert "`*`" in fifth
    assert "Think about what state you need to track" not in fifth  # template rung
    assert "<conversation_so_far>" in chat.llm.solver_users[-1]
    assert "tell me name of that problem" in chat.llm.solver_users[-1]

    # The whole exchange is in the store under the one conversation id.
    assert [m.role for m in store.messages] == ["user", "assistant"] * 5
    assert {m.conversation_id for m in store.messages} == {chat.conversation_id}


async def test_meta_question_without_a_problem_says_so(store: _Store) -> None:
    chat = _Conversation()
    result = await chat.turn("so you can't read previous conversations?")
    assert result.state.route == "meta"
    assert "no problem has been shared" in _reply(result)
    assert not chat.llm.asked_for_a_study_plan()


async def test_dont_know_raises_guidance_one_rung(store: _Store) -> None:
    chat = _Conversation()
    await chat.turn("how to solve this prob", image=_PNG)
    again = await chat.turn("I don't know")
    assert again.state.route == "dsa"
    generated = again.state.generated_response
    assert generated is not None
    assert generated.hint_level == HintLevel.L1_WHAT_TO_TRACK
    assert "range of possible open counts" in _reply(again)


async def test_a_technique_named_in_reply_to_the_tutor_is_an_answer(store: _Store) -> None:
    """The tutor asked a question about the problem; "two pointers?" answers it.

    Classified CONCEPT_EXPLANATION, it used to leave the problem and return a
    lecture on two pointers. It is weighed on the problem instead: the solver
    gets the reply with the conversation and takes the next step.
    """
    chat = _Conversation()
    await chat.turn("how to solve this prob", image=_PNG)
    answer = await chat.turn("two pointers?")
    assert answer.state.route == "dsa"
    assert answer.state.problem_relation == "followup"
    assert "two pointers?" in chat.llm.solver_users[-1]
    assert "<conversation_so_far>" in chat.llm.solver_users[-1]
    # The solver's verdict on their reasoning picks the opening line: a right
    # idea under the wrong name is credited, never "Not quite".
    assert _reply(answer).startswith("Your reasoning is right -- only the name is different.")
    assert "Not quite" not in _reply(answer)

    # An actual question about another concept is still its own question.
    other = await chat.turn("what is a trie?")
    assert other.state.problem_relation == "none"
    assert other.state.route == "explain"


# --- target behaviour (eval/behavior/adaptive_examples.jsonl) ------------------

_TWO_SUM_BEGINNER = (
    "I'm new to DSA. Can you help me solve Two Sum?\n\n"
    "Given:\nnums = [2, 7, 11, 15]\ntarget = 9\n\n"
    "I don't understand how to start."
)

_KEYERROR_DEBUG = (
    "I know hash maps and two pointers already.\n\n"
    "I tried solving Two Sum:\n\n"
    "```python\n"
    "def two_sum(nums, target):\n"
    "    seen = {}\n\n"
    "    for i, num in enumerate(nums):\n"
    "        complement = target - num\n\n"
    "        if complement in seen:\n"
    "            return [seen[num], i]\n\n"
    "        seen[num] = i\n\n"
    "    return []\n"
    "```\n\n"
    "But I'm getting a KeyError.\n\nCan you debug it?"
)


async def test_beginner_two_sum_opens_with_a_question_not_code(store: _Store) -> None:
    """Example 1, turn 1: a small step and a question to answer; no solution."""
    chat = _Conversation()
    result = await chat.turn(_TWO_SUM_BEGINNER)
    reply = _reply(result)
    assert result.state.route == "dsa"
    assert "?" in reply
    assert "```" not in reply
    assert "def " not in reply
    generated = result.state.generated_response
    assert generated is not None
    assert not generated.reveals_code
    assert store.pending is not None  # it is waiting for the learner's answer
    assert store.pending.kind == "question"


async def test_keyerror_debug_names_the_gap_and_asks_before_any_fix(store: _Store) -> None:
    """Example 2, turn 1: the learner's own bug, a question about it, no rewrite."""
    chat = _Conversation()
    result = await chat.turn(_KEYERROR_DEBUG)
    reply = _reply(result)
    assert result.state.route == "debug"
    assert "?" in reply
    assert "def two_sum" not in reply  # the corrected function is not dumped
    generated = result.state.generated_response
    assert generated is not None
    assert not generated.reveals_code
    assert result.state.tutoring is not None
    assert result.state.tutoring.pending is not None


# --- rendering ----------------------------------------------------------------


def test_protect_symbols_wraps_quoted_symbols_and_leaves_code_alone() -> None:
    text = "A '*' can be '(' or ')'. Compare 2 * 3.\n\n* a bullet\n\n```py\nx = '*'\n```"
    out = protect_symbols(text)
    assert "A `*` can be `(` or `)`." in out
    assert "2 `*` 3" in out
    assert "\n* a bullet" in out
    assert "x = '*'" in out
    assert protect_symbols("already `*` and **bold**") == "already `*` and **bold**"
    assert protect_symbols('Try "(*)" then "(*" next.') == "Try `(*)` then `(*` next."
    assert protect_symbols("it's a *good* idea, isn't it") == "it's a *good* idea, isn't it"


# --- the pattern label comes from the problem, not from a retrieval guess -------

_PARTITION_LABELS = (
    "You are given a string s. We want to partition the string into as many parts as "
    "possible so that each letter appears in at most one part, and return a list of the "
    "lengths of these parts.\n\n"
    'Example 1:\nInput: s = "ababcbacadefegdehijhklij"\nOutput: [9,7,8]'
)


class _TrieRetriever:
    """What the live retriever did for this statement: `trie`, barely above the floor."""

    async def retrieve(self, query: str, top_k: int) -> list[RetrievalHit]:
        del query
        chunks = corpus_chunks_by_pattern()["trie"][:top_k]
        return [
            RetrievalHit(chunk=chunk, score=-4.5 - index, retrievers=(), reranked=True)
            for index, chunk in enumerate(chunks)
        ]


def test_resolved_topic_lets_the_solver_correct_a_guess_only() -> None:
    assert resolved_topic("trie", "retrieval", "greedy") == "greedy"
    assert resolved_topic(None, "unknown", "greedy") == "greedy"
    assert resolved_topic("trie", "retrieval", "none") is None
    # No usable answer leaves the topic alone.
    assert resolved_topic("trie", "retrieval", None) == "trie"
    assert resolved_topic("trie", "retrieval", "made_up_pattern") == "trie"
    # Same family: retrieval's more specific pattern stands.
    assert resolved_topic("bfs", "retrieval", "graphs") == "bfs"
    # A title match, the conversation's topic or an explicit hint is not a guess.
    for source in ("title", "conversation", "hint", "profile_match"):
        assert resolved_topic("trie", source, "greedy") == "trie"  # pyright: ignore[reportArgumentType]


async def test_partition_labels_is_not_labelled_trie(store: _Store) -> None:
    chat = _Conversation(retriever=_TrieRetriever())
    result = await chat.turn(_PARTITION_LABELS)
    state = result.state
    assert state.route == "dsa"
    assert state.plan is not None
    assert state.plan.topic == "greedy"
    generated = state.generated_response
    assert generated is not None
    assert not any("Trie" in label for label in generated.citations)
    assert "trie" not in _reply(result).lower()
    # The corrected topic is what the conversation remembers and what the
    # learner's skill is recorded under -- never the wrong guess.
    assert store.active is not None
    assert store.active.topic == "greedy"
    assert [event.topic for event in state.events] == ["greedy"]


async def test_asking_for_the_code_after_one_step_is_granted_not_rationed(store: _Store) -> None:
    """CASE 4: "give full code" one hint in is honoured -- no "N more hints" quota.

    The reveal itself is only ever a sandbox-verified reference; this test has
    no sandbox, so the plan is granted and the reply says none could be
    verified, rather than showing unverified code.
    """
    chat = _Conversation()
    await chat.turn(_PARTITION_LABELS)
    result = await chat.turn("give full code")
    assert result.state.plan is not None
    assert result.state.plan.assistance_level == "full"
    assert "escalated" in result.state.plan.rationale
    reply = _reply(result)
    assert "more hint" not in reply
    assert "been through the hints" not in reply
    generated = result.state.generated_response
    assert generated is not None
    assert not generated.reveals_code  # nothing verified, nothing shown
    assert "did not return usable code" in reply
