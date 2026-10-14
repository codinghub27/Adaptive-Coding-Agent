"""What a new conversation knows about the learner, from the PROFILE only.

Owner decision OD-1 (`docs/features/ADAPTIVE-journey-eval.md`): a session
reads its own conversation and nothing else; whatever a later session needs
must already be on the profile. This module turns profile facts into the
lines the tutor is shown on the first message of a conversation, and names
each fact it used (`memory_used` in the eval record), so recall is never
claimed without the fact behind it.

Built only from the tutor's own records: catalog misconception ids and their
catalog text, and topic slugs with their levels. Nothing the learner wrote.
"""

from dataclasses import dataclass, field
from typing import Final

from app.memory.profile import PRIOR
from app.schemas.profile import LearnerProfileView
from app.tutoring.misconceptions import get_misconception

__all__ = ["Recall", "recall_for_opening"]

#: A skill this far above the prior has real evidence behind it.
_SHOWN: Final = 0.05
_TOPICS: Final = 3


@dataclass(frozen=True, slots=True)
class Recall:
    """Lines for the tutor's trusted learner record, and the facts they name."""

    lines: list[str] = field(default_factory=list[str])
    facts: list[str] = field(default_factory=list[str])

    def note(self) -> str | None:
        return "\n".join(self.lines) if self.lines else None

    def used_in(self, reply: str) -> list[str]:
        """The facts a reply visibly draws on: a recalled topic or mistake it
        names. On an opening every fact steered the turn; later, only one the
        reply talks about counts as recall."""
        said = reply.lower().replace("-", " ")
        used: list[str] = []
        for fact in self.facts:
            kind, _, key = fact.partition(".")
            if kind == "skills":
                named = key.replace("_", " ") in said
            else:
                item = get_misconception(key)
                named = item is not None and item.name.lower() in said
            if named:
                used.append(fact)
        return used


def recall_for_opening(profile: LearnerProfileView | None) -> Recall:
    """The recorded mistake to revisit and the progress the record shows."""
    if profile is None:
        return Recall()
    lines: list[str] = []
    facts: list[str] = []
    for error_id in profile.common_errors:
        item = get_misconception(error_id)
        if item is None:
            continue
        seen = profile.common_error_counts.get(error_id, 1)
        lines.append(
            f"recorded mistake from an earlier session (seen {seen}x): {item.name}. "
            f"What is right: {item.correct_model}"
        )
        facts.append(f"common_errors.{error_id}")
        break  # one mistake to revisit, the most frequent
    levels = profile.skill_levels
    above = [(topic, level) for topic, level in levels.items() if level >= PRIOR + _SHOWN]
    shown = sorted(above, key=lambda pair: -pair[1])[:_TOPICS]
    if shown:
        named = ", ".join(f"{topic} ({level:.2f}, above the 0.50 start)" for topic, level in shown)
        lines.append(f"progress the record shows (sandbox-checked or graded): {named}")
        facts += [f"skills.{topic}" for topic, _ in shown]
    return Recall(lines=lines, facts=facts)
