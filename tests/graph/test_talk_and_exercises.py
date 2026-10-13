"""Talk, debugging exercises and what an image showed (2026-10-13).

Read live against the owner's target conversations (`docs/target_behavior.md`,
`docs/target_behavior2.md`, `docs/current-behavior.md`):

- a question about the step just taken got a lesson on another example, a
  clarifying question, or the corrected code;
- "give me code with a bug and I will fix it" got a practice problem, then the
  correct solution;
- "why can the answer use both sides?" was taken as an ask for the code;
- the end of a function was run as a whole program and its crash reported as
  the learner's bug;
- a message sent with an image came back from history as "[image]".
"""

import json
from datetime import UTC, datetime
from uuid import uuid4

from app.agents.bug_exercise import plant_bug, render_bug_exercise
from app.agents.concept import strip_markers
from app.agents.dialogue import discuss, writes_the_solution
from app.agents.planner import declines_the_answer, explicit_ask_phrase, wants_the_code
from app.agents.practice import PracticeProblem, render_practice_problem
from app.graph.nodes import (
    _decide,  # pyright: ignore[reportPrivateUsage]
    _image_text,  # pyright: ignore[reportPrivateUsage]
)
from app.input.intent import asks_for_buggy_code, opens_as_a_lesson_ask
from app.input.snippet import is_partial_fragment, repair_snippet
from app.memory.conversation import (
    IMAGE_ONLY_CONTENT,
    IMAGE_TEXT_LEAD,
    _with_image_text,  # pyright: ignore[reportPrivateUsage]
)
from app.schemas.conversation import MessageView
from app.schemas.input import CodeBlock, StructuredInput
from app.schemas.intent import Intent
from app.schemas.tutoring import SessionProgress
from app.tutoring.grader import asks_for_a_hint, is_dont_know
from tests.graph.test_execute_verify_nodes import (
    _REQUEST,  # pyright: ignore[reportPrivateUsage]
    FakeRunner,
    _failed_result,  # pyright: ignore[reportPrivateUsage]
    _passed_result,  # pyright: ignore[reportPrivateUsage]
)
from tests.graph.test_turn_decision import (
    _active,  # pyright: ignore[reportPrivateUsage]
    _code_subject,  # pyright: ignore[reportPrivateUsage]
    _state,  # pyright: ignore[reportPrivateUsage]
    _sure,  # pyright: ignore[reportPrivateUsage]
    _unsure,  # pyright: ignore[reportPrivateUsage]
)
from tests.input.fakes import FakeLLMClient

# --- talk ---------------------------------------------------------------------


def _route(question: str, **kwargs: object) -> object:
    return _decide(_state(question, **kwargs)).get("route")  # type: ignore[arg-type]


def test_a_question_about_the_open_problem_is_answered_not_lectured() -> None:
    asked = _sure(Intent.CONCEPT_EXPLANATION, refers_to_previous=True, wants_lesson=False)
    update = _decide(_state("so is the pattern just two pointers?", intent=asked, active=_active()))
    assert update.get("route") == "discuss"
    assert update.get("problem_relation") == "followup"
    decision = update.get("decision")
    assert decision is not None
    assert (decision.subject, decision.move, decision.wants_code) == ("active", "discuss", False)


def test_their_reasoning_is_talk_and_a_hint_is_still_the_ladders() -> None:
    reasoning = _sure(Intent.DSA_HINT, refers_to_previous=True, learner_showed="partially_correct")
    assert _route("I move left until the repeat is gone", intent=reasoning, active=_active()) == (
        "discuss"
    )
    hint = _sure(Intent.DSA_HINT, refers_to_previous=True)
    assert _route("give me a hint", intent=hint, active=_active()) == "dsa"
    assert _route("I don't know", intent=hint, active=_active()) == "dsa"
    # An ask for the code is never talk.
    code = _sure(Intent.DSA_SOLVE, refers_to_previous=True, asks_for_code=True)
    assert _route("give me the code", intent=code, active=_active()) == "dsa"


def test_an_unread_message_inside_a_conversation_is_not_asked_what_it_refers_to() -> None:
    """Whenever the classifier was rate-limited, a message in the middle of a
    conversation got "there is no problem or code from you in this
    conversation yet"."""
    state = _state("why do we need while instead of if?", intent=_unsure(), active=_active())
    update = _decide(state)
    assert update.get("route") == "discuss"
    # A first message nobody could read is still asked about.
    assert _route("hmm can you look at this thing", intent=_unsure()) == "clarify"


def test_a_specific_question_is_answered_and_a_topic_is_taught() -> None:
    specific = _sure(Intent.CONCEPT_EXPLANATION, wants_lesson=False)
    assert _route("I usually use DFS for shortest paths. Is that fine?", intent=specific) == (
        "discuss"
    )
    lesson = _sure(Intent.CONCEPT_EXPLANATION, wants_lesson=True)
    assert _route("explain binary search", intent=lesson) == "explain"
    # With no model reading, how the message opens decides.
    assert opens_as_a_lesson_ask("what is a trie?")
    assert not opens_as_a_lesson_ask("I usually use DFS. Is that fine?")


def test_talk_about_shared_code_does_not_rerun_the_debugger() -> None:
    restated = _sure(Intent.CODE_DEBUG, refers_to_previous=True, learner_showed="correct")
    assert (
        _route(
            "so return one side, but update the answer with both sides?",
            intent=restated,
            active=_code_subject(),
        )
        == "discuss"
    )
    assert (
        _route(
            "where is the bug?",
            intent=_sure(Intent.CODE_DEBUG, refers_to_previous=True),
            active=_code_subject(),
        )
        == "debug"
    )


def test_the_answer_as_a_word_is_not_an_ask_for_the_code() -> None:
    for said in (
        "why can the answer use both sides but the return can't?",
        "so return one side, but update the answer with both sides?",
    ):
        assert not explicit_ask_phrase(said), said
    assert explicit_ask_phrase("just give me the answer")
    assert explicit_ask_phrase("what is the answer")


def test_offering_to_write_the_code_is_not_asking_for_it() -> None:
    said_yes = _sure(Intent.DSA_HINT, refers_to_previous=True, asks_for_code=True)
    assert not wants_the_code(said_yes, "Can I try the code and you review it?")
    assert not wants_the_code(said_yes, "let me write it first")
    assert wants_the_code(said_yes, "write it out")


def test_a_phone_keyboards_apostrophe_is_still_i_dont_know() -> None:
    assert is_dont_know("I don’t know where to start")
    assert declines_the_answer("Don’t tell me the technique unless I ask for help")
    assert declines_the_answer("I want to write the solution myself")
    assert asks_for_a_hint("can I get a hint?")
    assert not asks_for_a_hint("why does that work?")


async def test_the_dialogue_reply_carries_no_solution_code() -> None:
    wordy = (
        "Yes, that is the right condition, and it keeps the window valid after every step "
        "of the scan.\n\n```python\ndef f(s):\n    left = 0\n    return left\n```\n\n"
        "Now test it on a string with a repeat at the very end."
    )
    assert writes_the_solution(wordy)
    llm = FakeLLMClient(chat_content=json.dumps({"reply": wordy, "learner_showed": "correct"}))
    reply = await discuss("so I shrink while it repeats?", llm, subject="Longest Substring")
    assert reply is not None
    assert "def f" not in reply.text and "```" not in reply.text
    assert reply.text.startswith("Yes, that is the right condition")
    assert reply.learner_showed == "correct"
    assert len(llm.chat_calls) == 2  # asked again once, then the words were kept
    prompt = llm.chat_calls[0][-1].content
    assert "<subject>" in prompt and "<user_input>" in prompt

    silent = await discuss("x", FakeLLMClient(raise_chat=True))
    assert silent is None


# --- debugging exercises ---------------------------------------------------------


def test_a_request_for_buggy_code_is_an_exercise_not_a_solution() -> None:
    for said in (
        "give 2 problems with bugs and to fix it myself,start with easy",
        "give a code with bug and i will fix it",
        "i asked you to give code with bugs and i will fix it to test myself."
        "give bug code of twosum",
    ):
        assert asks_for_buggy_code(said), said
        update = _decide(_state(said, intent=_unsure(Intent.DSA_SOLVE), active=_active()))
        assert update.get("route") == "practice", said
        assert update.get("bug_exercise") is True
        decision = update.get("decision")
        assert decision is not None
        assert decision.wants_code is False
        assert decision.withhold is True  # the fix is theirs to find
    assert not asks_for_buggy_code("my code has a bug, give me the fix")
    # The model's own reading wins when it read the turn.
    model_no = _sure(Intent.PRACTICE_REQUEST, wants_buggy_code=False)
    assert (
        _decide(_state("give me a problem on bugs in graphs", intent=model_no)).get("bug_exercise")
        is None
    )
    # Their own code is theirs to debug, never an exercise to set.
    own = _state("this code has a bug, give the fix", intent=_unsure(), code="def f():\n    pass")
    assert _decide(own).get("bug_exercise") is None


_REFERENCE = (
    "def two_sum(nums, target):\n    seen = {}\n    for i, num in enumerate(nums):\n"
    "        if target - num in seen:\n            return [seen[target - num], i]\n"
    "        seen[num] = i\n    return []\n"
)
_BUGGY = _REFERENCE.replace("if target - num in seen", "if num in seen")


async def test_a_planted_bug_is_handed_out_only_when_the_sandbox_fails_it() -> None:
    llm = FakeLLMClient(chat_content=json.dumps({"statement": "Find two.", "code": _BUGGY}))
    caught = await plant_bug(
        _REFERENCE, _REQUEST, llm, FakeRunner(_failed_result()), problem="Two Sum"
    )
    assert caught is not None
    assert caught.code.strip() == _BUGGY.strip()
    assert caught.verdict.status == "fail"
    text = render_bug_exercise(caught, title="Two Sum", difficulty="easy", statement="Find two.")
    assert "one bug" in text and "```python" in text
    assert "two_sum([2, 7, 11, 15], 9)" in text  # the sandbox's own failing case
    assert "if num in seen" in text

    # The "bug" passes every case: not an exercise, nothing is handed out.
    missed = await plant_bug(
        _REFERENCE, _REQUEST, llm, FakeRunner(_passed_result()), problem="Two Sum"
    )
    assert missed is None
    # No sandbox, no exercise: a model's word that code is wrong is not enough.
    assert await plant_bug(_REFERENCE, _REQUEST, llm, None, problem="Two Sum") is None
    # The solution handed back unchanged is not an exercise either.
    same = FakeLLMClient(chat_content=json.dumps({"code": _REFERENCE}))
    runner = FakeRunner(_failed_result())
    assert await plant_bug(_REFERENCE, _REQUEST, same, runner, problem="Two Sum") is None
    assert runner.calls == []  # never run


def test_a_challenge_names_no_technique() -> None:
    problem = PracticeProblem(
        topic="hashing",
        title="Contains Duplicate",
        url="https://leetcode.com/problems/contains-duplicate/",
        difficulty="easy",
        cue="have I seen this before",
    )
    challenge = render_practice_problem(problem, challenge=True)
    assert "hashing" not in challenge and "have I seen this before" not in challenge
    assert "Contains Duplicate" in challenge
    assert "hashing" in render_practice_problem(problem)


# --- code that is only part of a function ---------------------------------------


def test_the_end_of_a_function_is_read_not_run() -> None:
    fragment = (
        "while left <= right:\n    mid = (left + right) // 2\n    if nums[mid] == target:\n"
        "        return mid\n    elif nums[mid] < target:\n        left = mid + 1\n"
        "    else:\n        right = mid - 1\nreturn left"
    )
    assert is_partial_fragment(repair_snippet(fragment))
    whole_body = (
        "seen = {}\nfor i, n in enumerate(nums):\n    if target - n in seen:\n"
        "        return [seen[target - n], i]\n    seen[n] = i\nreturn []"
    )
    assert not is_partial_fragment(repair_snippet(whole_body))
    comprehension = "squares = [x * x for x in nums]\nreturn max(squares)"
    assert not is_partial_fragment(repair_snippet(comprehension))
    assert not is_partial_fragment("def f(nums):\n    total = 0\n    return total")
    assert is_partial_fragment(repair_snippet("count += 1\nreturn count"))


# --- no reference list ------------------------------------------------------------


def test_reference_lines_and_ref_marks_are_not_printed() -> None:
    plan = (
        "1. **Stacks** - LIFO. *(ref 14)*\nReference: 11\n2. Queues (ref 15).\nReferences: 4, own"
    )
    cleaned = strip_markers(plan)
    assert "ref" not in cleaned.lower()
    assert "Stacks" in cleaned and "Queues" in cleaned
    # A list literal and a sentence that uses the word are untouched.
    assert strip_markers("Take nums[0] and [2, 1, 5].") == "Take nums[0] and [2, 1, 5]."
    assert "reference" in strip_markers("A weak reference does not keep it alive.")


# --- what an image showed ----------------------------------------------------------


def _message(content: str, image_text: str | None) -> MessageView:
    return MessageView(
        id=uuid4(),
        conversation_id=uuid4(),
        seq=1,
        role="user",
        content=content,
        intent=None,
        image_mime="image/png" if image_text else None,
        image_text=image_text,
        created_at=datetime.now(UTC),
    )


def test_history_remembers_what_an_image_showed() -> None:
    read = StructuredInput(
        source="image",
        problem="217. Contains Duplicate\n\nReturn true if any value appears twice.",
        code=[CodeBlock(content="def f(nums):\n    return False", language="python")],
    )
    shown = _image_text(read)
    assert shown is not None
    assert "217. Contains Duplicate" in shown and "def f(nums):" in shown
    assert _image_text(StructuredInput(source="image")) is None

    only_image = _with_image_text(_message(IMAGE_ONLY_CONTENT, shown))
    assert IMAGE_ONLY_CONTENT not in only_image.content
    assert only_image.content.startswith(IMAGE_TEXT_LEAD)
    assert "Contains Duplicate" in only_image.content
    with_words = _with_image_text(_message("how to solve this prob", shown))
    assert with_words.content.startswith("how to solve this prob")
    assert "Contains Duplicate" in with_words.content
    plain = _message("hello", None)
    assert _with_image_text(plain) is plain
    assert plain.adapted is False


def test_the_exercise_key_is_remembered_with_the_conversation() -> None:
    progress = SessionProgress.empty().model_copy(update={"bug_exercise_key": "_pabc"})
    assert SessionProgress.model_validate(progress.model_dump()).bug_exercise_key == "_pabc"
