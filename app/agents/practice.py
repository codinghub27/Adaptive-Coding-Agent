"""Practice-problem selection, grounded in the curated knowledge corpus.

"Give me a 2-pointer problem for a beginner" used to fall through the hint
ladder and produce a hint about a problem the learner never stated. This module
answers it instead, by surfacing a real problem from the corpus's own
`representative_problems` section for that pattern, chosen at the difficulty
the learner's skill warrants.

Two properties matter here:

- **Nothing is invented.** Every field of a `PracticeProblem` is parsed out of
  the bundled corpus files, which are curated, checked-in, trusted content.
  There is no LLM call, so there is nothing to hallucinate a problem that does
  not exist or a difficulty that was never assigned.
- **The result is DISPLAY DATA, never instructions.** A `PracticeProblem`
  carries no learner-authored text -- it cannot, since its only input is the
  corpus -- and the node that renders it (`app.graph.nodes.practice_agent`)
  puts it in `AgentOutcome.text` and nowhere else. It is never fed back into a
  prompt as an instruction, never used to pick a tool, and never influences
  `assistance_level`. On a later turn it reaches the model only the way any
  other assistant message does, as conversation history the prompts already
  frame as data.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Final

from app.schemas.base import APIModel
from app.schemas.knowledge import KnowledgeChunk
from app.schemas.plan import Difficulty

__all__ = [
    "PracticeProblem",
    "render_practice_problem",
    "select_practice_problem",
    "select_session_problem",
]

_SECTION_KEY: Final = "section"
_PROBLEMS_SECTION: Final = "representative_problems"
_SIGNALS_SECTION: Final = "identification_signals"

#: `- [Title](url) — Difficulty`, as the corpus writes it. The dash before the
#: difficulty is an em dash in every corpus file; a plain hyphen is accepted
#: too so a future file does not silently stop parsing.
_PROBLEM_RE: Final = re.compile(
    r"^\s*[-*]\s*\[(?P<title>[^\]]+)\]\((?P<url>[^)]+)\)\s*[—-]\s*(?P<difficulty>Easy|Medium|Hard)\b",
    re.IGNORECASE | re.MULTILINE,
)

_DIFFICULTY_ORDER: Final[tuple[Difficulty, ...]] = ("easy", "medium", "hard")


class PracticeProblem(APIModel):
    """One practice problem surfaced from the corpus. Display data only."""

    topic: str
    title: str
    url: str
    difficulty: Difficulty
    cue: str | None = None
    """A recognition signal for the pattern, from the corpus, when available."""


def _section(chunk: KnowledgeChunk) -> str:
    return chunk.metadata.get(_SECTION_KEY, "")


def _candidates(chunks: Sequence[KnowledgeChunk], topic: str) -> list[PracticeProblem]:
    problems: list[PracticeProblem] = []
    seen: set[str] = set()
    for chunk in chunks:
        if _section(chunk) != _PROBLEMS_SECTION:
            continue
        for match in _PROBLEM_RE.finditer(chunk.text):
            title = match.group("title").strip()
            if not title or title in seen:
                continue
            seen.add(title)
            problems.append(
                PracticeProblem(
                    topic=topic,
                    title=title,
                    url=match.group("url").strip(),
                    difficulty=match.group("difficulty").strip().lower(),  # pyright: ignore[reportArgumentType]
                )
            )
    return problems


def _cue(chunks: Sequence[KnowledgeChunk]) -> str | None:
    """The pattern's first recognition signal, as one short line."""
    for chunk in chunks:
        if _section(chunk) != _SIGNALS_SECTION:
            continue
        for line in chunk.text.splitlines():
            stripped = line.strip().lstrip("-*").strip()
            # Skip the "{title} — {heading}" prefix chunking adds, and blanks.
            if len(stripped) > 15 and not stripped.startswith("#") and "—" not in stripped:
                return stripped[:200]
    return None


def _nearest(problems: Sequence[PracticeProblem], wanted: Difficulty) -> PracticeProblem:
    """The problem closest to `wanted`, preferring not to over-shoot.

    A learner asking for practice at "easy" is better served a medium problem
    than nothing at all, but should never be handed a hard one while an easier
    candidate exists -- so candidates are ranked by distance from `wanted`,
    ties going to the easier side.
    """
    target = _DIFFICULTY_ORDER.index(wanted)
    return min(
        problems,
        key=lambda p: (
            abs(_DIFFICULTY_ORDER.index(p.difficulty) - target),
            _DIFFICULTY_ORDER.index(p.difficulty),
        ),
    )


def select_practice_problem(
    topic: str | None, difficulty: Difficulty, chunks: Sequence[KnowledgeChunk]
) -> PracticeProblem | None:
    """A corpus problem for `topic` at (or nearest) `difficulty`, or `None`.

    `None` whenever there is no topic to practise or the corpus offers no
    parseable problem for it -- the caller says so plainly rather than
    inventing a problem, which is the whole point of grounding this in the
    corpus (see the module docstring).
    """
    if not topic:
        return None
    problems = _candidates(chunks, topic)
    if not problems:
        return None
    chosen = _nearest(problems, difficulty)
    return chosen.model_copy(update={"cue": _cue(chunks)})


def select_session_problem(
    topic: str,
    difficulty: Difficulty,
    chunks_by_pattern: Mapping[str, Sequence[KnowledgeChunk]],
    *,
    patterns: Sequence[str],
    preferred_titles: frozenset[str] = frozenset(),
    exclude_titles: frozenset[str] = frozenset(),
) -> PracticeProblem | None:
    """A corpus problem across `patterns` (the topic, or a whole family) for a session.

    Like `select_practice_problem`, but (ADAPTIVE-tutoring G5) it draws from
    every pattern in `patterns` -- "a hard graph problem" is any hard problem of
    the graphs family -- skips problems this conversation already practised,
    and, at equal distance from `difficulty`, prefers a problem in
    `preferred_titles` (those with a curated statement the sandbox can test).
    Ties then break on the title, so the choice is deterministic. Each problem
    keeps its OWN pattern as its topic.
    """
    candidates: list[PracticeProblem] = []
    seen: set[str] = set()
    for pattern in patterns:
        for problem in _candidates(chunks_by_pattern.get(pattern, ()), pattern):
            if problem.title in exclude_titles or problem.title in seen:
                continue
            seen.add(problem.title)
            candidates.append(problem)
    if not candidates:
        return None
    target = _DIFFICULTY_ORDER.index(difficulty)
    chosen = min(
        candidates,
        key=lambda p: (
            abs(_DIFFICULTY_ORDER.index(p.difficulty) - target),
            p.title not in preferred_titles,
            _DIFFICULTY_ORDER.index(p.difficulty),
            p.topic != topic,
            p.title,
        ),
    )
    return chosen.model_copy(update={"cue": _cue(chunks_by_pattern.get(chosen.topic, ()))})


def render_practice_problem(problem: PracticeProblem, statement: str | None = None) -> str:
    """Render `problem` as the learner-facing text of a practice turn.

    `statement` is a curated problem statement (trusted bank text) when one
    exists. The corpus cue is a signal of the PATTERN and is worded that way:
    it used to read as if it described this specific problem.
    """
    lines = [
        f"Here is a **{problem.difficulty}** problem to practise `{problem.topic}`:",
        "",
        f"**{problem.title}** — {problem.url}",
    ]
    if statement:
        body = (
            statement.split("\n", 1)[1].strip() if statement.startswith("Problem:") else statement
        )
        lines += ["", body]
    if problem.cue:
        lines += ["", f"A general signal of this pattern: {problem.cue}"]
    lines += [
        "",
        "Work it through yourself first -- don't write code yet. Answer the question "
        "below, paste your attempt when you want it checked, or ask for a hint if you get stuck.",
    ]
    return "\n".join(lines)
