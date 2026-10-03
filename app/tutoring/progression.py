"""In-session progression (ADAPTIVE-tutoring G5).

The next practice problem's difficulty is decided from three things, in order:

1. what the learner explicitly asked for ("an easy problem", "a hard graph
   problem", "something harder");
2. this conversation's graded results since the last problem was handed out
   (success -> one level up, struggle -> stay, with scaffolding raised);
3. the planner's skill-based default (`difficulty_for` on the family-aware
   skill estimate).

The request text is matched against fixed phrases only; it is never stored or
echoed.
"""

from __future__ import annotations

import re
from typing import Final, Literal

from app.schemas.event import Difficulty
from app.schemas.tutoring import SessionProgress
from app.tutoring.turn import step_difficulty

__all__ = ["DifficultyRequest", "requested_difficulty", "session_difficulty"]

DifficultyRequest = Literal["easy", "medium", "hard", "harder", "easier"]

_HARDER_RE: Final = re.compile(
    r"\b(harder|more difficult|tougher|more challenging|next level|level up|step up|"
    r"something more advanced|bigger challenge)\b",
    re.IGNORECASE,
)
_EASIER_RE: Final = re.compile(r"\b(easier|simpler|less difficult|not so hard)\b", re.IGNORECASE)
_HARD_RE: Final = re.compile(r"\b(hard|difficult|challenging|advanced|tough)\b", re.IGNORECASE)
_MEDIUM_RE: Final = re.compile(r"\b(medium|intermediate|moderate)\b", re.IGNORECASE)
_EASY_RE: Final = re.compile(
    r"\b(easy|simple|beginner|basic|warm[- ]?up|starter|for beginners)\b", re.IGNORECASE
)


def requested_difficulty(text: str | None) -> DifficultyRequest | None:
    """The difficulty a practice request names, if any (fixed phrases only)."""
    if not text:
        return None
    if _HARDER_RE.search(text):
        return "harder"
    if _EASIER_RE.search(text):
        return "easier"
    if _HARD_RE.search(text):
        return "hard"
    if _MEDIUM_RE.search(text):
        return "medium"
    if _EASY_RE.search(text):
        return "easy"
    return None


def _since_last_practice(progress: SessionProgress) -> list[str]:
    return [g.grade for g in progress.grades[progress.grades_at_practice :]]


def session_difficulty(
    base: Difficulty, progress: SessionProgress, request: DifficultyRequest | None
) -> tuple[Difficulty, str]:
    """The next problem's difficulty and a short machine-readable reason."""
    if request in ("easy", "medium", "hard"):
        return request, "requested"
    last = progress.last_practice.difficulty if progress.last_practice is not None else None
    recent = _since_last_practice(progress)
    struggled = any(g in ("incorrect", "dont_know") for g in recent[-2:])
    succeeded = "correct" in recent and not struggled
    if request == "harder":
        anchor = last or base
        if struggled:
            return anchor, "harder_denied_struggle"
        return step_difficulty(anchor, +1), "harder_after_success" if succeeded else "harder"
    if request == "easier":
        return step_difficulty(last or base, -1), "easier"
    if last is not None:
        if succeeded:
            return step_difficulty(last, +1), "up_after_success"
        if struggled:
            return last, "same_after_struggle"
        return last, "same"
    return base, "skill"
