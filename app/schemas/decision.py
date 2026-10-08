"""The one tutoring decision made for a turn (target behaviour section 26).

`TurnDecision` is written once, by `app.graph.nodes.decide_turn`, after the
conversation is loaded and the message is classified. Everything downstream
(retrieval, the planner, the agents, the response layer, the learner model)
reads it; nothing downstream works out again what the turn is about or what
the learner asked for.

`evidence` is the only field that is refined later in the turn: a grader, the
solver or the sandbox may know more about what the learner just showed than
the first reading did (`with_evidence`). It is still one field on one record.

Every value is from a closed vocabulary. The record never carries learner
text, so it is safe to put in a prompt as trusted tutor state.
"""

from typing import Final, Literal

from pydantic import Field

from app.schemas.base import APIModel

__all__ = [
    "EVIDENCE_LABELS",
    "Evidence",
    "Move",
    "Scaffold",
    "Subject",
    "TurnDecision",
    "scaffold_for",
]

#: What this turn is about.
#: `active`: the conversation's stored problem or code. `last_reply`: the
#: tutor's last reply (a study plan or an explanation). `earlier`: the subject
#: before the active one, which this turn goes back to. `new`: something the
#: message brings or names itself. `conversation`: the chat itself ("what was
#: that problem called?"). `none`: nothing to anchor on.
Subject = Literal["active", "last_reply", "earlier", "new", "conversation", "none"]

#: What the learner just showed (target behaviour section 2.2), or `none` when
#: the message is a request rather than a response.
Evidence = Literal[
    "correct",
    "terminology_error",
    "partially_correct",
    "conceptual_misconception",
    "implementation_error",
    "incomplete",
    "incorrect",
    "stuck",
    "none",
]

EVIDENCE_LABELS: Final[frozenset[str]] = frozenset(
    {
        "correct",
        "terminology_error",
        "partially_correct",
        "conceptual_misconception",
        "implementation_error",
        "incomplete",
        "incorrect",
        "stuck",
    }
)

#: The smallest useful intervention. `step` is one tutoring step on a problem
#: (how much it reveals is the hint ladder's level, moved by `scaffold`);
#: `code` is the full solution or the fix, because the learner asked for it.
Move = Literal[
    "step",
    "code",
    "debug",
    "explain",
    "review",
    "plan",
    "practice",
    "grade",
    "meta",
    "greet",
    "clarify",
]

#: Whether help should increase, stay or decrease this turn (section 26, Q9).
Scaffold = Literal["up", "same", "down"]


def scaffold_for(evidence: Evidence) -> Scaffold:
    """More help after a wrong or stuck reply, less after a right one."""
    if evidence in ("stuck", "incorrect", "conceptual_misconception"):
        return "up"
    if evidence in ("correct", "terminology_error"):
        return "down"
    return "same"


class TurnDecision(APIModel):
    """What this turn is about, what the learner showed, and what to do."""

    subject: Subject
    evidence: Evidence = "none"
    move: Move
    scaffold: Scaffold = "same"
    #: The learner asked to be given the code (or the fix) now.
    wants_code: bool = False
    #: The message was read with confidence (a model's confident answer or a
    #: deterministic rule). `False` means the conversation decided, not the
    #: wording.
    confident: bool = True
    #: Why, as fixed tags, for the trace. Never learner text.
    reasons: list[str] = Field(default_factory=list[str])

    def with_evidence(self, evidence: Evidence) -> "TurnDecision":
        """This decision with a firmer reading of what the learner showed."""
        return self.model_copy(update={"evidence": evidence, "scaffold": scaffold_for(evidence)})
