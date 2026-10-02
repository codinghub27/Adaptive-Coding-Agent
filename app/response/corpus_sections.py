"""Pattern-level teaching sections quoted from the curated corpus (ADAPTIVE-upgrade P3).

F6: a learner asked for "the intuition first, then how to recognize which
approach to use, then the complexity" and got "Understanding the problem" plus
one hint. The DSA solver gates problem-specific content by hint rung (the key
insight of THIS problem is an L3 reveal), which is right -- but intuition,
recognition cues and the typical cost of a PATTERN are general knowledge the
corpus already teaches, and they do not hand over this problem's solution.

So, for a turn whose topic is known, this module quotes the topic's own corpus
sections, whole (never cut mid-sentence), each with the citation of the chunk
it came from:

- `recognition` ("When to Recognize It")  -- every assistance level,
- `intuition`   ("Core Intuition")         -- `concept` and above,
- `complexity`  ("Complexity")             -- `concept` and above, when asked,
  and only when the solver has not already produced this problem's own.

Only trusted corpus text is ever quoted; the learner's message is read solely
to detect WHICH of a fixed set of section names they asked for.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from app.schemas.knowledge import KnowledgeChunk
from app.schemas.plan import ASSISTANCE_ORDER, AssistanceLevel

__all__ = [
    "CorpusSection",
    "SectionRequest",
    "corpus_sections",
    "requested_sections",
]

SectionRequest = str  # one of "intuition", "recognition", "complexity", "tests"

_REQUEST_PATTERNS: Final[Mapping[SectionRequest, re.Pattern[str]]] = {
    "intuition": re.compile(r"\b(intuition|intuitive|why (it|this) works|the idea)\b", re.I),
    "recognition": re.compile(
        r"\b(recogni[sz]e|recognition|identify (the|which)|which (approach|pattern)|"
        r"how (do|would|to) (i|you|we) know)\b",
        re.I,
    ),
    "complexity": re.compile(r"\b(complexity|big[- ]?o|time and space|runtime)\b", re.I),
    "tests": re.compile(r"\b(run (the )?tests?|test cases?|run it)\b", re.I),
}


def requested_sections(text: str | None) -> frozenset[SectionRequest]:
    """Which fixed section names the learner's message asks for."""
    if not text:
        return frozenset()
    return frozenset(name for name, pattern in _REQUEST_PATTERNS.items() if pattern.search(text))


@dataclass(frozen=True, slots=True)
class CorpusSection:
    """One quoted corpus section: response kind, body, and its citation label."""

    kind: str
    body: str
    citation: str


#: response kind -> (corpus section slug, lowest assistance level it is shown at)
_SECTION_SOURCES: Final[tuple[tuple[str, str, AssistanceLevel], ...]] = (
    ("recognition", "when_to_recognize_it", "hint"),
    ("intuition", "core_intuition", "concept"),
    ("complexity", "complexity", "concept"),
)


def _body(chunk: KnowledgeChunk) -> str:
    prefix = f"{chunk.title} — {chunk.heading}\n\n"
    text = chunk.text[len(prefix) :] if chunk.text.startswith(prefix) else chunk.text
    return text.strip()


def _label(chunk: KnowledgeChunk) -> str:
    parts = [part for part in (chunk.title.strip(), chunk.heading.strip()) if part]
    return " - ".join(parts) or chunk.source


def corpus_sections(
    chunks: Sequence[KnowledgeChunk],
    assistance: AssistanceLevel,
    requested: frozenset[SectionRequest],
    *,
    has_problem_complexity: bool = False,
    reveal_pattern: bool = True,
) -> list[CorpusSection]:
    """The topic's corpus sections this turn may show, in teaching order.

    `chunks` must be ONE pattern's own corpus chunks. `recognition` and
    `intuition` NAME the pattern, so they are shown only when `reveal_pattern`
    (the caller's judgement that the pattern may be named this turn: past the
    first rung, or a confident topic) or when the learner asked for them by
    name; `complexity` only when asked for and not already covered by the
    solver's problem-specific complexity. A section the corpus stores in more
    than one chunk is skipped rather than quoted in part: these sections are
    promised whole.
    """
    by_section: dict[str | None, KnowledgeChunk] = {}
    split_sections: set[str | None] = set()
    for chunk in chunks:
        section = chunk.metadata.get("section")
        if chunk.metadata.get("part", "0") != "0":
            split_sections.add(section)
        else:
            by_section[section] = chunk
    level = ASSISTANCE_ORDER.index(assistance)
    out: list[CorpusSection] = []
    for kind, section, minimum in _SECTION_SOURCES:
        if level < ASSISTANCE_ORDER.index(minimum) or section in split_sections:
            continue
        if kind == "complexity" and ("complexity" not in requested or has_problem_complexity):
            continue
        if kind != "complexity" and not reveal_pattern and kind not in requested:
            continue
        chunk = by_section.get(section)
        if chunk is None:
            continue
        body = _body(chunk)
        if body:
            out.append(CorpusSection(kind=kind, body=body, citation=_label(chunk)))
    return out
