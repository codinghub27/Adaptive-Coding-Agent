"""Journey-eval packets (`docs/features/ADAPTIVE-journey-eval.md`, Phase B).

Read on the 10-session journey replay:

- a reply to the tutor's own question ("dfs?", "two pointers, one at the start
  one at the end") was taught as a new topic, with code, and nothing was
  graded;
- an opening that described a struggle ("I freeze on mediums", "bad day")
  got a multi-week study plan;
- "test went ok! 2 out of 3 ... drill me" was read as "ask me 2 questions",
  and the drill came as one block.
"""

import json
from datetime import UTC, datetime
from uuid import uuid4

from app.agents.assess import mark_one, names_a_count, render_quiz, requested_count
from app.agents.dialogue import discuss
from app.graph.nodes import (
    _decide,  # pyright: ignore[reportPrivateUsage]
    tutor_asked_last,
)
from app.graph.routing import asks_for_a_study_plan
from app.graph.state import AgentState
from app.schemas.conversation import MessageView
from app.schemas.intent import Intent
from app.schemas.tutoring import QuizItem, QuizState
from tests.graph.test_turn_decision import (
    _state,  # pyright: ignore[reportPrivateUsage]
    _sure,  # pyright: ignore[reportPrivateUsage]
)
from tests.input.fakes import FakeLLMClient


def _said(role: str, content: str, seq: int) -> MessageView:
    return MessageView(
        id=uuid4(),
        conversation_id=uuid4(),
        seq=seq,
        role=role,  # type: ignore[arg-type]
        content=content,
        intent=None,
        created_at=datetime.now(UTC),
    )


def _after_question(state: AgentState, tutor: str) -> AgentState:
    return state.model_copy(
        update={
            "recent_context": [
                _said("user", "I want to get better at picking approaches", 1),
                _said("assistant", tutor, 2),
            ]
        }
    )


_ASKED = "Good. Next one.\n\nShortest path in an unweighted grid: which approach would you use?"
_TOLD = "Shortest path in an unweighted grid uses BFS. It visits nodes in distance order."


def test_the_tutor_asked_is_read_from_its_last_reply() -> None:
    lesson = _sure(Intent.CONCEPT_EXPLANATION, wants_lesson=True)
    assert tutor_asked_last(_after_question(_state("dfs?", intent=lesson), _ASKED))
    assert not tutor_asked_last(_after_question(_state("dfs?", intent=lesson), _TOLD))
    assert not tutor_asked_last(_state("dfs?", intent=lesson))


def test_an_answer_to_the_tutors_question_is_talk_not_a_lesson() -> None:
    # The classifier reads a bare technique name as a topic to teach.
    lesson = _sure(Intent.CONCEPT_EXPLANATION, wants_lesson=True)
    for answer in ("dfs?", "two pointers, one at the start one at the end", "yes, faster is fine"):
        update = _decide(_after_question(_state(answer, intent=lesson), _ASKED))
        assert update.get("route") == "discuss", answer
    # With no question from the tutor, the same message is still a lesson.
    assert _decide(_after_question(_state("dfs?", intent=lesson), _TOLD)).get("route") == (
        "explain"
    )


def test_a_request_after_the_tutors_question_is_still_a_request() -> None:
    lesson = _sure(Intent.CONCEPT_EXPLANATION, wants_lesson=True)
    for ask in ("explain dfs", "what is a deque?", "can you show me bfs first"):
        update = _decide(_after_question(_state(ask, intent=lesson), _ASKED))
        assert update.get("route") == "explain", ask


def test_a_plan_only_on_an_ask_for_one() -> None:
    assert not asks_for_a_study_plan(
        "placements start in like 10 weeks. I freeze on mediums. kinda stressed"
    )
    assert not asks_for_a_study_plan("bad day, couldn't even start one question")
    assert asks_for_a_study_plan("give roadmap to master stack,queue")
    assert asks_for_a_study_plan("what should I study first?")
    assert asks_for_a_study_plan("can you make me a plan for 6 weeks")
    guidance = _sure(Intent.GENERAL_GUIDANCE)
    opening = "placements start in 10 weeks and I freeze on mediums, kinda stressed honestly"
    assert _decide(_state(opening, intent=guidance)).get("route") == "discuss"
    assert _decide(_state("give me a study plan", intent=guidance)).get("route") == "explain"


def test_a_count_is_a_number_of_questions() -> None:
    assert not names_a_count("test went ok! 2 out of 3. can you drill me on patterns?")
    assert names_a_count("ask me 10 basic questions")
    assert requested_count("give me 3 questions on arrays") == 3
    assert requested_count("I got 2 out of 3, drill me", default=3) == 3


async def test_a_drill_asks_one_question_at_a_time() -> None:
    quiz = QuizState(
        items=[
            QuizItem(question="Next warmer day for each day?", topic="stack", expected="stack"),
            QuizItem(question="Max of every window of size k?", topic="queue", expected="deque"),
        ],
        one_at_a_time=True,
    )
    shown = render_quiz(quiz)
    assert "**#1:** Next warmer day" in shown and "Max of every window" not in shown

    from app.agents.assess import mark_quiz

    llm = FakeLLMClient(
        chat_content=json.dumps({"marks": [{"n": 1, "verdict": "incorrect", "feedback": "No."}]})
    )
    current = quiz.model_copy(update={"items": [quiz.items[0]], "one_at_a_time": False})
    marked = await mark_quiz(current, "two pointers", llm)
    assert marked is not None
    moved, text, grades = mark_one(quiz, marked)
    assert moved is not None and moved.current == 1 and moved.verdicts == ["incorrect"]
    assert "**#2:** Max of every window" in text
    assert [g.grade for g in grades] == ["incorrect"]

    llm = FakeLLMClient(
        chat_content=json.dumps({"marks": [{"n": 1, "verdict": "correct", "feedback": ""}]})
    )
    last = moved.model_copy(update={"items": [moved.items[1]], "one_at_a_time": False})
    marked = await mark_quiz(last.model_copy(update={"current": 0}), "a deque", llm)
    assert marked is not None
    done, text, _ = mark_one(moved, marked)
    assert done is None
    assert "**1 of 2**" in text


async def test_the_dialogue_names_a_topic_only_from_the_closed_list() -> None:
    reply = {"reply": "Not BFS here: DFS goes deep first.", "learner_showed": "incorrect"}
    llm = FakeLLMClient(chat_content=json.dumps({**reply, "topic": "bfs"}))
    got = await discuss("dfs?", llm, topics=("bfs", "dfs"))
    assert got is not None and got.topic == "bfs" and got.learner_showed == "incorrect"
    assert "<topics>" in llm.chat_calls[0][-1].content

    llm = FakeLLMClient(chat_content=json.dumps({**reply, "topic": "made_up_topic"}))
    got = await discuss("dfs?", llm, topics=("bfs", "dfs"))
    assert got is not None and got.topic is None


# --- later packets ------------------------------------------------------------


def test_recall_comes_from_the_profile_and_names_its_facts() -> None:
    from app.schemas.profile import LearnerProfileView
    from app.tutoring.recall import recall_for_opening

    profile = LearnerProfileView(
        skill_levels={"sliding_window": 0.62, "graphs": 0.48},
        learning_preferences={},
        common_errors=["sliding_window.left_pointer_moves_backward", "not.a.catalog.id"],
        common_error_counts={"sliding_window.left_pointer_moves_backward": 1},
    )
    recall = recall_for_opening(profile)
    assert recall.facts == [
        "common_errors.sliding_window.left_pointer_moves_backward",
        "skills.sliding_window",
    ]
    note = recall.note()
    assert note is not None and "Moving the left edge backward" in note
    assert "graphs" not in note  # no evidence above the start
    assert recall.used_in("Remember the sliding window from last time?") == [
        "skills.sliding_window"
    ]
    assert recall_for_opening(None).facts == []


async def test_the_dialogue_names_a_misconception_only_from_the_catalog() -> None:
    body = {"reply": "BFS fits better here.", "learner_showed": "incorrect", "topic": "bfs"}
    catalog = (("bfs.dfs_for_unweighted_shortest_path", "DFS for shortest paths"),)
    llm = FakeLLMClient(
        chat_content=json.dumps({**body, "misconception": "bfs.dfs_for_unweighted_shortest_path"})
    )
    got = await discuss("depth first?", llm, topics=("bfs",), misconceptions=catalog)
    assert got is not None and got.misconception == "bfs.dfs_for_unweighted_shortest_path"
    llm = FakeLLMClient(chat_content=json.dumps({**body, "misconception": "invented.id"}))
    got = await discuss("depth first?", llm, topics=("bfs",), misconceptions=catalog)
    assert got is not None and got.misconception is None


def test_a_first_encounter_with_a_topic_starts_at_concept() -> None:
    from app.agents.planner import ProblemAnalysis, build_plan
    from app.schemas.profile import LearnerProfileView

    profile = LearnerProfileView(
        skill_levels={"trees": 0.55}, learning_preferences={}, common_errors=[]
    )
    hint = _sure(Intent.DSA_HINT)
    new = ProblemAnalysis(
        topic="binary_search_on_answer", skill_level=0.5, topic_source="retrieval"
    )
    seen = ProblemAnalysis(topic="trees", skill_level=0.55, topic_source="profile_match")
    assert build_plan(hint, profile, new).assistance_level == "concept"
    assert "first_encounter" in build_plan(hint, profile, new).rationale
    assert build_plan(hint, profile, seen).assistance_level == "hint"


def test_code_with_no_words_is_the_attempt_at_the_open_problem() -> None:
    from tests.graph.test_turn_decision import _active  # pyright: ignore[reportPrivateUsage]

    code = "def f(s):\n    best = 0\n    for ch in s:\n        best += 1\n    return best\n"
    unread = _sure(Intent.CODE_DEBUG, refers_to_previous=False)
    update = _decide(_state("", intent=unread, active=_active(), code=code))
    assert update.get("problem_relation") == "same"
    inherited = update.get("structured_input")
    assert inherited is not None and inherited.problem  # the statement came with it


def test_bare_code_on_an_open_problem_is_checked_whatever_its_label() -> None:
    from tests.graph.test_turn_decision import _active  # pyright: ignore[reportPrivateUsage]

    code = "def f(s):\n    best = 0\n    for ch in s:\n        best += 1\n    return best\n"
    explain = _sure(Intent.CODE_EXPLAIN, refers_to_previous=True)
    update = _decide(_state("", intent=explain, active=_active(), code=code))
    assert update.get("route") == "debug"


# --- non-DSA turns and reply shape (owner's live test, 2026-10-14) -------------


def test_code_for_a_framework_is_read_not_failed() -> None:
    from app.execution.imports import missing_packages

    framework = "from fastapi import FastAPI\nimport os\napp = FastAPI()\n"
    assert missing_packages(framework) == ["fastapi"]
    assert missing_packages("import collections, math\nfrom heapq import heappush\n") == []
    assert missing_packages("def broken(:\n") == []  # the run reports a syntax error


def test_a_one_block_reply_is_broken_into_paragraphs() -> None:
    from app.agents.dialogue import in_paragraphs

    block = (
        "Yes, I can help with topics beyond algorithms. We can look at language features, "
        "testing, clean code, performance and how frameworks such as web servers are put "
        "together, e.g. routing. Which one would you like to start with today, the language "
        "or a framework?"
    )
    parts = in_paragraphs(block).split("\n\n")
    assert len(parts) == 3 and parts[-1].endswith("?")
    short = "Yes. That is right."
    assert in_paragraphs(short) == short
    listed = "Two things:\n- one\n- two"
    assert in_paragraphs(listed) == listed


def test_explain_the_approach_on_the_open_problem_is_taught_in_full() -> None:
    from tests.graph.test_turn_decision import _active  # pyright: ignore[reportPrivateUsage]

    ask = _sure(Intent.DSA_HINT, refers_to_previous=True)
    update = _decide(
        _state("Explain clearly how to solve this problem.", intent=ask, active=_active())
    )
    assert update.get("route") == "discuss"
    # A hint is still the ladder's.
    assert _decide(_state("give me a hint", intent=ask, active=_active())).get("route") == "dsa"


async def test_a_reply_loses_markdown_hard_break_backslashes() -> None:
    body = {"reply": "**Idea**: scan once.\\\n\\\n**Example**: IV is 4.", "learner_showed": "none"}
    got = await discuss("explain it", FakeLLMClient(chat_content=json.dumps(body)))
    assert got is not None and "\\" not in got.text
    assert "**Idea**: scan once.\n\n**Example**" in got.text


def test_owner_live_turns_reach_the_agent_that_can_answer_them() -> None:
    from tests.graph.test_turn_decision import (
        _active,  # pyright: ignore[reportPrivateUsage]
        _unsure,  # pyright: ignore[reportPrivateUsage]
    )

    # A whole first request is answered even when nobody could read it.
    assert (
        _decide(
            _state("Can you help me to improve my communication skills?", intent=_unsure())
        ).get("route")
        == "discuss"
    )
    assert _decide(_state("hmm can you look at this thing", intent=_unsure())).get("route") == (
        "clarify"
    )
    # An optimisation question with no new code is answered, not linted.
    optimise = _sure(Intent.OPTIMIZATION, refers_to_previous=True)
    assert (
        _decide(
            _state("Any other techniques to optimize this?", intent=optimise, active=_active())
        ).get("route")
        == "discuss"
    )
