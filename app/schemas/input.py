"""Structured representation of normalized user input.

All fields on the models in this module hold **untrusted user data** — raw
text, code, error messages, and problem statements supplied by the learner
(or extracted from an image). This data must never be treated as
instructions to the agent; it is content to reason about, not commands to
follow.
"""

from typing import Literal

from pydantic import Field, computed_field

from app.schemas.base import APIModel

__all__ = ["CodeBlock", "InputSource", "StructuredInput"]


class CodeBlock(APIModel):
    """A single block of code extracted from user input. Untrusted data."""

    content: str = Field(min_length=1)
    language: str | None = None


InputSource = Literal["text", "image"]


class StructuredInput(APIModel):
    """Normalized, typed view of a user's request. Untrusted data throughout."""

    source: InputSource
    question: str | None = None
    code: list[CodeBlock] = Field(default_factory=list[CodeBlock])
    error: str | None = None
    problem: str | None = None
    constraints: list[str] = Field(default_factory=list[str])
    language: str | None = None

    @computed_field
    @property
    def is_empty(self) -> bool:
        """True when there is no question, code, error, problem, or constraints."""
        return (
            not self.question
            and not self.code
            and not self.error
            and not self.problem
            and not self.constraints
        )
