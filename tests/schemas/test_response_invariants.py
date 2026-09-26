"""`GeneratedResponse.reveals_code` invariant tests (Packet P3).

Escalation (`app.agents.planner.build_plan`) can only ever raise
`TeachingPlan.assistance_level` to `partial`/`full` -- never grant revealed
code directly. This is the structural backstop that makes that safe: a
`GeneratedResponse` must be verifiably incapable of setting `reveals_code`
at any lower assistance level, regardless of what any node hands it.
"""

import pytest
from pydantic import ValidationError

from app.schemas.plan import AssistanceLevel
from app.schemas.response import GeneratedResponse, ResponseSection


@pytest.mark.parametrize("level", ["hint", "concept", "pseudocode"])
def test_non_code_bearing_assistance_level_rejects_reveals_code(level: AssistanceLevel) -> None:
    """A hint-level (or concept/pseudocode) turn is structurally incapable of
    emitting `reveals_code=True` -- it must fail validation, not just be
    discouraged by convention."""
    with pytest.raises(ValidationError):
        GeneratedResponse(text="a hint", assistance_level=level, reveals_code=True)


@pytest.mark.parametrize("level", ["partial", "full"])
def test_code_bearing_assistance_level_accepts_reveals_code(level: AssistanceLevel) -> None:
    response = GeneratedResponse(text="here is the code", assistance_level=level, reveals_code=True)
    assert response.reveals_code is True


def test_code_bearing_section_without_reveals_code_is_rejected() -> None:
    with pytest.raises(ValidationError):
        GeneratedResponse(
            text="here is the code",
            assistance_level="full",
            sections=[
                ResponseSection(kind="code", title="Solution", body="def solve(): ..."),
            ],
            reveals_code=False,
        )
