"""Every agent answers with the conversation and the turn decision in front of it.

Before this only the hint-ladder agent (and, on a follow-up, the concept
agent) saw the conversation. The debugger, the reviewer and the code explainer
answered every message as if it were the first; the explainer was not even
shown what the learner asked about their code.
"""

import json

from app.agents.debugger import read_code
from app.agents.explainer import generate_line_explanations
from app.schemas.decision import TurnDecision
from app.schemas.input import CodeBlock, StructuredInput
from app.tutoring.adaptation import (
    TURN_CONTEXT_RULE,
    adapt,
    conversation_block,
    turn_context,
)
from tests.input.fakes import FakeLLMClient

_CODE = "def two_sum(nums, target):\n    seen = {}\n    return [seen[target], 0]\n"
_EXCHANGE = [
    ("user", "I'm getting a KeyError. Can you debug it?"),
    ("assistant", "You check `complement in seen` but read `seen[num]`."),
]


def _context() -> str:
    decision = TurnDecision(subject="active", evidence="partially_correct", move="debug")
    adaptation = adapt(decision=decision, pitch="advanced", topic="hashing", evidence_log=[])
    return turn_context(decision, adaptation, _EXCHANGE)


def test_the_blocks_are_delimited_and_cut_short() -> None:
    block = conversation_block([("user", "x " * 600), ("assistant", "ok")])
    assert block.startswith("<conversation_so_far>\nlearner: x x")
    assert "...[truncated]" in block
    assert block.endswith("tutor: ok\n</conversation_so_far>")
    assert conversation_block([]) == ""
    assert turn_context(None, None, []) == ""


def test_the_context_has_the_decision_the_pitch_and_the_exchange() -> None:
    context = _context()
    assert "<tutor_state>" in context
    assert "learner_showed: partially_correct" in context
    assert "pitch: advanced" in context
    assert "tutor: You check `complement in seen`" in context


async def test_the_debugger_is_shown_the_conversation() -> None:
    llm = FakeLLMClient(chat_content=json.dumps({"bug_explanation": "Exactly.", "used": []}))
    problem = StructuredInput(
        source="text",
        question="So I was checking complement but accessing num?",
        code=[CodeBlock(content=_CODE, language="python")],
    )
    reading = await read_code(
        problem,
        static_findings=[],
        failing_case=None,
        bug_location=None,
        llm=llm,
        turn_context=_context(),
    )
    assert reading.explanation == "Exactly."
    system, user = llm.chat_calls[0][0].content, llm.chat_calls[0][1].content
    assert TURN_CONTEXT_RULE.strip() in system
    assert "instead of repeating what you said before" in system
    assert user.index("<conversation_so_far>") < user.index("<user_input>")
    assert "tutor: You check `complement in seen`" in user


async def test_the_code_explainer_is_shown_what_was_asked() -> None:
    reply = json.dumps({"explanations": [{"lineno": 2, "explanation": "An empty dict."}]})
    llm = FakeLLMClient(chat_content=reply)
    context = turn_context(None, None, [("user", "what does the seen dict do here?")])
    await generate_line_explanations(_CODE, llm, context)
    user = llm.chat_calls[0][1].content
    assert "learner: what does the seen dict do here?" in user
    assert TURN_CONTEXT_RULE.strip() in llm.chat_calls[0][0].content
