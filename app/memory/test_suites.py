"""Validated test-suite cache: the same subject is judged by the same cases.

`subject_key` names what the cases belong to: a problem title when the
statement leads with one ("678. Valid Parenthesis String"), otherwise a hash
of the statement or of the shared code. Rows are per user (see
`app.db.models.test_suite.CachedTestSuite` for why).

Neither function commits the session -- callers own the transaction.
"""

import hashlib
import re
import uuid
from typing import Final, Literal

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.test_suite import CachedTestSuite
from app.schemas.execution import TestCase
from app.schemas.input import StructuredInput

__all__ = ["CachedSource", "get_cached_cases", "save_cached_cases", "subject_key", "title_slug"]

CachedSource = Literal["extracted", "synthesised"]

_CASES: Final = TypeAdapter(list[TestCase])
_NUMBER_RE: Final = re.compile(r"^\s*(?:problem\s*)?#?\d+\s*[.):-]\s*", re.IGNORECASE)
_SLUG_RE: Final = re.compile(r"[^a-z0-9]+")
_MAX_TITLE_WORDS: Final = 8
#: A first line that opens like this is the statement itself, not a title.
_PROSE_STARTS: Final = (
    "given",
    "you",
    "write",
    "return",
    "implement",
    "find",
    "design",
    "there",
    "a ",
    "an ",
    "the ",
    "we ",
    "i ",
    "in ",
)


def title_slug(statement: str | None) -> str | None:
    """The problem's title as a slug, when the statement leads with one.

    "678. Valid Parenthesis String" -> "valid-parenthesis-string". A first
    line that reads as prose ("Given an array of integers ...") is not a
    title: two different problems open with that sentence.
    """
    if not statement:
        return None
    first = next((line.strip() for line in statement.splitlines() if line.strip()), "")
    name = _NUMBER_RE.sub("", first).strip(" *#`_:")
    lowered = name.lower()
    words = lowered.split()
    if not 1 < len(words) <= _MAX_TITLE_WORDS or name.endswith((".", "?", ":")):
        return None
    if lowered.startswith(_PROSE_STARTS):
        return None
    slug = _SLUG_RE.sub("-", lowered).strip("-")
    return slug or None


def subject_key(problem: StructuredInput | None) -> str | None:
    """Cache key for what `problem` is about, or `None` when it has no subject."""
    if problem is None:
        return None
    if problem.problem:
        slug = title_slug(problem.problem)
        if slug is not None:
            return f"t:{slug}"[:96]
        normalized = " ".join(problem.problem.lower().split())
        return "s:" + hashlib.sha256(normalized.encode()).hexdigest()[:32]
    if problem.code:
        joined = " ".join("\n".join(block.content for block in problem.code).split())
        return "c:" + hashlib.sha256(joined.encode()).hexdigest()[:32]
    return None


async def get_cached_cases(
    session: AsyncSession, user_id: uuid.UUID, key: str
) -> tuple[list[TestCase], CachedSource] | None:
    """The cached cases for this learner+subject, or `None`.

    A stored payload that no longer validates reads as a miss, never an error.
    """
    stmt = select(CachedTestSuite.cases, CachedTestSuite.source).where(
        CachedTestSuite.user_id == user_id, CachedTestSuite.subject_key == key
    )
    row = (await session.execute(stmt)).one_or_none()
    if row is None:
        return None
    try:
        cases = _CASES.validate_python(row[0])
    except ValidationError:
        return None
    if not cases:
        return None
    source: CachedSource = "extracted" if row[1] == "extracted" else "synthesised"
    return cases, source


async def save_cached_cases(
    session: AsyncSession,
    user_id: uuid.UUID,
    key: str,
    cases: list[TestCase],
    source: CachedSource,
) -> None:
    """Upsert the validated `cases` for this learner+subject."""
    payload = _CASES.dump_python(cases, mode="json")
    stmt = pg_insert(CachedTestSuite).values(
        user_id=user_id, subject_key=key, cases=payload, source=source
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[CachedTestSuite.user_id, CachedTestSuite.subject_key],
        set_={"cases": payload, "source": source},
    )
    await session.execute(stmt)
