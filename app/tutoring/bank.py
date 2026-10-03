"""The curated guiding-question bank (ADAPTIVE-tutoring AD-T2).

Questions, rubrics, accepted answers and follow-ups are AGENT-authored data in
`app/knowledge/tutoring/checks.json`, each anchored to a corpus section
(`rubric_ref`). On top of the curated chains, every corpus pattern gets a
generated *recognition* question ("which technique, and why?") whose rubric is
that pattern's own corpus vocabulary (slug, title, aliases). Nothing here is
ever derived from learner text: the learner's reply is only ever GRADED
against a `PendingCheck` built from this module.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Final, Literal

from pydantic import Field

from app.knowledge.ingest import load_corpus
from app.schemas.base import APIModel
from app.schemas.event import Difficulty
from app.schemas.plan import AssistanceLevel
from app.schemas.tutoring import ConceptRubric, OptionRubric, PendingCheck

__all__ = [
    "CuratedProblem",
    "QuestionSpec",
    "RECOGNITION_PREFIX",
    "base_question_id",
    "chain_intro",
    "chain_start",
    "code_submission_check",
    "curated_problem",
    "curated_problems",
    "curated_problem_for_text",
    "family_patterns",
    "get_question",
    "named_pattern",
    "pattern_family",
    "pattern_terms",
    "pending_for",
    "recognition_id",
]

BANK_PATH: Final = Path(__file__).resolve().parent.parent / "knowledge" / "tutoring" / "checks.json"
RECOGNITION_PREFIX: Final = "recognition."
SUBMIT_PREFIX: Final = "submit."
#: Separates a question id from the concept a narrowed follow-up targets.
NARROW_SEP: Final = "~"


class ConceptSpec(APIModel):
    id: str
    keywords: list[str] = Field(default_factory=list[str])
    follow_up: str | None = None


class QuestionSpec(APIModel):
    """One curated (or generated recognition) question and how to react to answers."""

    topic: str
    rubric_ref: str | None = None
    question: str
    accepted_answers: list[str] = Field(default_factory=list[str])
    expected_concepts: list[ConceptSpec] = Field(default_factory=list[ConceptSpec])
    options: list[OptionRubric] = Field(default_factory=list[OptionRubric])
    wrong_answers: dict[str, str] = Field(default_factory=dict[str, str])
    misconception_ids: list[str] = Field(default_factory=list[str])
    next: str | None = None
    then: Literal["code_submission"] | None = None
    then_question: str | None = None
    reframe: str | None = None
    on_correct: str = ""
    on_incorrect: str = ""
    scaffold: str = ""
    #: The reusable takeaway shown when this question closes a chain.
    lesson: str | None = None


class CuratedProblem(APIModel):
    """A practice problem with a curated statement whose worked examples the
    sandbox suite extractor can read (`Example N: a = ... -> result`)."""

    title: str
    topic: str
    difficulty: Difficulty
    statement: str


class _Chain(APIModel):
    id: str
    topic: str
    start: str
    match_any: list[str] = Field(default_factory=list[str])
    match_all: list[list[str]] = Field(default_factory=list[list[str]])
    #: The curated problem this chain teaches, when it is about one problem.
    problem: str | None = None
    #: One or two agent-authored sentences that open the chain, replacing a
    #: long explanation (the spec's "teach the missing piece, not the topic").
    intro: str | None = None


class _Recognition(APIModel):
    question: str
    then_question: str
    reason_keywords: list[str]
    wrong_by_pattern: dict[str, dict[str, str]] = Field(default_factory=dict[str, dict[str, str]])


class _Bank(APIModel):
    questions: dict[str, QuestionSpec]
    chains: list[_Chain]
    recognition: _Recognition
    problems: list[CuratedProblem]


@cache
def _bank() -> _Bank:
    raw = json.loads(BANK_PATH.read_text(encoding="utf-8"))
    raw.pop("_doc", None)
    return _Bank.model_validate(raw)


@cache
def _corpus_index() -> dict[str, tuple[str, str, tuple[str, ...]]]:
    """pattern -> (family, title, aliases), from the trusted corpus front matter."""
    return {
        doc.pattern: (doc.pattern_family or doc.pattern, doc.title, doc.aliases)
        for doc in load_corpus()
    }


def pattern_family(pattern: str | None) -> str | None:
    if pattern is None:
        return None
    entry = _corpus_index().get(pattern)
    return entry[0] if entry is not None else None


def family_patterns(topic: str) -> list[str]:
    """`topic`, or -- when `topic` is a family root ("graphs") -- every pattern of
    that family, root first. Practice requests that name a family draw from all."""
    index = _corpus_index()
    if topic not in index:
        return [topic]
    family = index[topic][0]
    if family != topic:
        return [topic]
    members = sorted(p for p, (f, _t, _a) in index.items() if f == family and p != topic)
    return [topic, *members]


@cache
def pattern_terms() -> dict[str, tuple[str, ...]]:
    """Each corpus pattern's own vocabulary, lowercased: slug, title, aliases.

    A three-plus-word alias also contributes its first two words ("breadth
    first search" -> "breadth first") so a learner's natural shorthand counts.
    """
    terms: dict[str, tuple[str, ...]] = {}
    for pattern, (_family, title, aliases) in _corpus_index().items():
        words: set[str] = {pattern.replace("_", " "), title.lower()}
        for alias in aliases:
            alias_l = alias.lower().strip()
            if not alias_l:
                continue
            words.add(alias_l)
            parts = alias_l.replace("-", " ").split()
            if len(parts) >= 3:
                words.add(" ".join(parts[:2]))
        if pattern.endswith("s") and "_" not in pattern:
            words.add(pattern[:-1])  # "heaps" -> "heap", "graphs" -> "graph"
        terms[pattern] = tuple(sorted(w for w in words if len(w) >= 3))
    return terms


def recognition_id(topic: str) -> str:
    return f"{RECOGNITION_PREFIX}{topic}"


def base_question_id(question_id: str) -> str:
    return question_id.split(NARROW_SEP, 1)[0]


def _recognition_spec(topic: str) -> QuestionSpec | None:
    if topic not in _corpus_index():
        return None
    rec = _bank().recognition
    family = pattern_family(topic) or topic
    # The pattern itself and its family root ("dp_1d" -> "dynamic programming");
    # a family root ("graphs") also accepts any of its members.
    accepted_patterns = [topic]
    if family in _corpus_index():
        accepted_patterns.append(family)
    if family == topic:
        accepted_patterns += [p for p, (f, _t, _a) in _corpus_index().items() if f == family]
    technique_words: list[str] = []
    for pattern in accepted_patterns:
        technique_words += list(pattern_terms().get(pattern, ()))
    title = _corpus_index()[topic][1]
    return QuestionSpec(
        topic=topic,
        rubric_ref=f"corpus:{topic}#identification_signals",
        question=rec.question,
        expected_concepts=[
            ConceptSpec(
                id="technique",
                keywords=sorted(set(technique_words)),
                follow_up=(
                    f"Which technique fits here? Think about the {title.lower()} pattern's signals."
                ),
            ),
            ConceptSpec(
                id="reason",
                keywords=list(rec.reason_keywords),
                follow_up="Right technique. What about this problem tells you so?",
            ),
        ],
        wrong_answers=dict(rec.wrong_by_pattern.get(topic, {})),
        misconception_ids=sorted({m for m in rec.wrong_by_pattern.get(topic, {}).values() if m}),
        then="code_submission",
        then_question=rec.then_question,
        on_correct=f"Yes -- that's the {title} pattern, and you named why.",
        on_incorrect=(
            "That technique doesn't fit as well here. Look again at what the problem "
            f"asks for: it is a {title.lower()} problem."
        ),
        scaffold=(
            f"This is a {title.lower()} problem. Ask yourself what the problem wants you "
            "to find, and what that tells you about the order you should explore things in."
        ),
    )


def get_question(question_id: str) -> QuestionSpec | None:
    """The spec behind a (possibly narrowed) question id, or `None`."""
    base = base_question_id(question_id)
    if base.startswith(RECOGNITION_PREFIX):
        return _recognition_spec(base.removeprefix(RECOGNITION_PREFIX))
    return _bank().questions.get(base)


def pending_for(
    question_id: str,
    *,
    problem_key: str | None = None,
    assistance: AssistanceLevel | None = None,
    narrow_to: str | None = None,
    question_override: str | None = None,
) -> PendingCheck | None:
    """A `PendingCheck` for `question_id`, built only from the bank.

    `narrow_to` keeps a single concept (a partial answer's narrower follow-up):
    the question text becomes that concept's own `follow_up` and only it is
    expected. `question_override` lets the reaction phrase the question
    (e.g. prefixing an acknowledgement); it is agent text, never learner text.
    """
    spec = get_question(question_id)
    if spec is None:
        return None
    concepts = spec.expected_concepts
    qid = base_question_id(question_id)
    question = spec.question
    if narrow_to is None and NARROW_SEP in question_id:
        narrow_to = question_id.split(NARROW_SEP, 1)[1]
    if narrow_to is not None:
        narrowed = [c for c in concepts if c.id == narrow_to]
        if narrowed:
            concepts = narrowed
            qid = f"{qid}{NARROW_SEP}{narrow_to}"
            question = narrowed[0].follow_up or question
    return PendingCheck(
        kind="question",
        question_id=qid,
        question=(question_override or question)[:600],
        topic=spec.topic,
        rubric_ref=spec.rubric_ref,
        expected_concepts=[ConceptRubric(id=c.id, keywords=c.keywords) for c in concepts],
        accepted_answers=list(spec.accepted_answers) if narrow_to is None else [],
        options=list(spec.options) if narrow_to is None else [],
        wrong_answers=dict(spec.wrong_answers),
        misconception_ids=list(spec.misconception_ids),
        problem_key=problem_key,
        assistance_at_ask=assistance,
        created_at=datetime.now(UTC),
    )


def code_submission_check(
    question: str,
    *,
    topic: str | None,
    problem_key: str | None,
    assistance: AssistanceLevel | None,
    lesson: str | None = None,
) -> PendingCheck:
    """A pending request for the learner's implementation (G4)."""
    return PendingCheck(
        kind="code_submission",
        question_id=f"{SUBMIT_PREFIX}{topic or 'code'}",
        question=question[:600],
        topic=topic,
        rubric_ref=f"corpus:{topic}#general_template" if topic else None,
        problem_key=problem_key,
        assistance_at_ask=assistance,
        lesson=lesson,
        created_at=datetime.now(UTC),
    )


def _matches(text: str, chain: _Chain) -> bool:
    if chain.match_any and any(term in text for term in chain.match_any):
        return True
    if chain.match_all:
        return all(
            any(re.search(rf"\b{re.escape(t)}", text) for t in group) for group in chain.match_all
        )
    return False


def chain_intro(text: str | None) -> str | None:
    """The opening line of the curated chain this text is about, if any."""
    if not text:
        return None
    lowered = text.lower()
    for chain in _bank().chains:
        if _matches(lowered, chain):
            return chain.intro
    return None


def chain_start(text: str | None) -> str | None:
    """The first question of the curated chain this text is about, if any.

    `text` is the problem/question being taught. Matching only SELECTS which
    agent-authored chain to ask; nothing from `text` enters the question.
    """
    if not text:
        return None
    lowered = text.lower()
    for chain in _bank().chains:
        if _matches(lowered, chain):
            return chain.start
    return None


def curated_problems() -> list[CuratedProblem]:
    return list(_bank().problems)


def curated_problem(title: str) -> CuratedProblem | None:
    for problem in _bank().problems:
        if problem.title.lower() == title.lower():
            return problem
    return None


def curated_problem_for_text(text: str | None) -> CuratedProblem | None:
    """The curated problem a learner's message is about (via its chain), if any.

    Lets a problem the learner only NAMED ("help me solve Two Sum") become the
    conversation's active problem with the curated statement -- whose worked
    examples the sandbox can test -- instead of no active problem at all.
    """
    if not text:
        return None
    lowered = text.lower()
    for chain in _bank().chains:
        if chain.problem and _matches(lowered, chain):
            return curated_problem(chain.problem)
    return None


def named_pattern(text: str | None) -> str | None:
    """The corpus pattern a request names by its own vocabulary, if any.

    "a hard graph problem" -> "graphs". The longest matching term wins, so
    "binary search tree" names trees rather than binary_search. Fixed corpus
    vocabulary only; the text is never stored.
    """
    if not text:
        return None
    lowered = text.lower().replace("-", " ")

    def found(term: str) -> bool:
        term = term.replace("-", " ")
        return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", lowered) is not None

    # Longest matching term wins; on a tie, the pattern's own NAME beats an
    # alias ("two pointer" -> two_pointers, not the sliding-window alias
    # "two-pointer window"), while "binary search tree" still beats the
    # shorter "binary search".
    best: tuple[int, int, str] | None = None
    for pattern, terms in pattern_terms().items():
        name = pattern.replace("_", " ")
        own = {name, name.removesuffix("s")}
        for term in {*terms, *own}:
            if len(term) < 3 or not found(term):
                continue
            score = (len(term), 1 if term in own else 0, pattern)
            if best is None or score[:2] > best[:2]:
                best = score
    return best[2] if best is not None else None
