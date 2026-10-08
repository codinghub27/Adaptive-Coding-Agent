"""How a reply is spoken: a tutor talking, not a report with headers.

The response layer used to join every section as `## Title` + body, so a
debug turn read "## What your code is trying to do ... ## What the sandbox
found ..." and an explanation opened "## Explanation" above its own heading
(docs/BEHAVIOR_GAP.md, RC-3). The target is one step in plain speech, the
sandbox's lines exactly as the verifier produced them, and one question.

`speak` changes presentation only. It never adds, drops or reorders a section
and never touches a body: which sections a turn may carry (no code below the
allowed level, Challenge mode, the verdict lines) is decided before it, in
code, by `app.response.generate` and `app.graph.nodes._with_tutoring`.
"""

from collections.abc import Mapping, Sequence
from typing import Final

from app.schemas.response import ResponseSection, ResponseSectionKind

__all__ = ["speak", "speak_section"]

#: Said as they are: the tutor's own sentences, the code, the verifier's lines
#: and the one question.
_BARE: Final[frozenset[ResponseSectionKind]] = frozenset(
    {
        "next_hint",
        "explanation",
        "answer_feedback",
        "lead",
        "inferred_approach",
        "bug_explanation",
        "misconception",
        "verification",
        "execution",
        "check_question",
        "practice_problem",
        "code",
    }
)

#: A sentence that introduces the body.
_LEAD_INS: Final[Mapping[ResponseSectionKind, str]] = {
    "static_findings": "Before running it, a static check flagged this:",
    "failing_case": "It fails on this case:",
    "patch": "Here is the fix:",
    "pseudocode": "In steps:",
}

#: A short inline label, for the parts a learner scans for.
_LABELS: Final[Mapping[ResponseSectionKind, str]] = {
    "key_insight": "The idea",
    "complexity": "Cost",
    "lesson": "Takeaway",
    "watch_out": "Watch out",
    "understanding": "The problem",
    "constraints": "Constraints",
    "brute_force": "Brute force",
    "why_slow": "Why that is too slow",
    "common_mistakes": "Common mistakes",
    "recognition": "How to recognise it",
    "intuition": "The intuition",
    "structure": "Structure",
    "line_by_line": "Line by line",
    "findings": "Review",
    "correctness": "Correctness",
    "next_steps": "Next",
    "citations": "References",
}


_YOUR_CODE_TITLE: Final = "Your code"


def speak_section(section: ResponseSection) -> str:
    """One section as the tutor would say it."""
    body = section.body.strip()
    if section.kind == "code" and section.title == _YOUR_CODE_TITLE:
        # The learner's own code handed back, not a solution written for them.
        return f"Here is your code:\n\n{body}"
    if section.kind in _BARE:
        return body
    lead_in = _LEAD_INS.get(section.kind)
    if lead_in is not None:
        return f"{lead_in}\n\n{body}"
    label = _LABELS.get(section.kind, section.title)
    if "\n" in body:
        return f"**{label}**\n\n{body}"
    return f"**{label}:** {body}"


def speak(sections: Sequence[ResponseSection]) -> tuple[list[ResponseSection], str]:
    """`sections` with their spoken form filled in, and the whole reply."""
    spoken = [s.model_copy(update={"spoken": speak_section(s)}) for s in sections]
    return spoken, "\n\n".join(s.spoken or s.body for s in spoken)
