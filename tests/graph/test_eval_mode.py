"""`EVAL_MODE`: the decision record and the clock override on `/chat` and `/profile`.

It is evaluation instrumentation (`docs/features/ADAPTIVE-journey-eval.md`):
off by default, refused in production, and when on it must add a `decision`
object that carries no learner text and let one request run on a given date.
"""

from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from app import clock
from app.auth.deps import get_current_user
from app.config import Settings
from app.db.session import get_session
from app.graph.eval_view import build_eval_decision
from app.graph.state import AgentState, RawInput
from app.input.api import get_llm
from app.main import create_app
from app.schemas.auth import AuthUser
from app.schemas.decision import TurnDecision
from app.schemas.event import LearningEventCreate
from app.schemas.execution import Verdict
from app.schemas.plan import TeachingPlan
from app.schemas.tutoring import ExecutionView, TutoringView
from tests.graph.test_chat_api import _stub_session  # pyright: ignore[reportPrivateUsage]
from tests.input.fakes import FakeLLMClient

MakeSettings = Callable[..., Settings]

_CODE_TURN = (
    "```python\ndef get_item(items, idx):\n    return items[idx]\n```\n\nIndexError: boom\n"
)
_MOMENT = "2026-03-01T09:30:00+00:00"


@asynccontextmanager
async def _client(
    make_settings: MakeSettings, *, eval_mode: bool
) -> AsyncGenerator[httpx.AsyncClient]:
    settings = make_settings(eval_mode=eval_mode)
    app = create_app(settings)
    # The lifespan is not run in these tests; it is what sets this.
    app.state.settings = settings
    app.dependency_overrides[get_llm] = lambda: FakeLLMClient(chat_content="unused")
    session: Callable[..., AsyncIterator[Any]] = _stub_session
    app.dependency_overrides[get_session] = session
    app.dependency_overrides[get_current_user] = lambda: AuthUser(
        id=uuid4(), handle="eval-user", session_id=uuid4()
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def test_eval_mode_is_off_by_default(make_settings: MakeSettings) -> None:
    assert make_settings().eval_mode is False


def test_eval_mode_is_refused_in_production(make_settings: MakeSettings) -> None:
    with pytest.raises(ValidationError):
        make_settings(eval_mode=True, app_env="prod")
    assert make_settings(eval_mode=False, app_env="prod").eval_mode is False


async def test_chat_returns_no_decision_when_eval_mode_is_off(
    make_settings: MakeSettings,
) -> None:
    async with _client(make_settings, eval_mode=False) as client:
        response = await client.post("/chat", data={"text": _CODE_TURN})
    assert response.status_code == 200
    assert response.json()["decision"] is None


async def test_clock_override_is_refused_when_eval_mode_is_off(
    make_settings: MakeSettings,
) -> None:
    async with _client(make_settings, eval_mode=False) as client:
        chat = await client.post("/chat", data={"text": _CODE_TURN, "eval_now": _MOMENT})
        profile = await client.get("/profile", params={"eval_now": _MOMENT})
    assert chat.status_code == 403
    assert profile.status_code == 403


async def test_chat_returns_the_decision_on_the_overridden_clock(
    make_settings: MakeSettings,
) -> None:
    async with _client(make_settings, eval_mode=True) as client:
        response = await client.post("/chat", data={"text": _CODE_TURN, "eval_now": _MOMENT})
    assert response.status_code == 200
    decision = response.json()["decision"]
    assert decision["route"] == "debug"
    assert decision["teaching_mode"] == "balanced"
    assert datetime.fromisoformat(decision["clock"]) == datetime.fromisoformat(_MOMENT)
    # Labels, ids and counts only: nothing the learner typed.
    assert "get_item" not in response.text.split('"decision"', 1)[1]
    assert "IndexError" not in response.text.split('"decision"', 1)[1]


def test_clock_override_applies_only_inside_its_block() -> None:
    moment = datetime(2026, 3, 1, 9, 30, tzinfo=UTC)
    with clock.override(moment):
        assert clock.now() == moment
    assert abs((clock.now() - datetime.now(UTC)).total_seconds()) < 5
    with clock.override(None):
        assert abs((clock.now() - datetime.now(UTC)).total_seconds()) < 5


def test_decision_reports_the_learner_verdict_not_the_patch() -> None:
    """`sandbox` is the verdict on the learner's own code (`initial_verdict`);
    a passing patch is reported beside it, never in its place."""
    failed = Verdict(
        status="fail",
        category="wrong_answer",
        first_failing_case="case_2",
        expected=2,
        actual=3,
        summary="1 of 2 failed",
        cases_passed=1,
        cases_total=2,
    )
    patched = Verdict(status="pass", summary="ok", cases_passed=2, cases_total=2)
    state = AgentState(
        input=RawInput(text="untrusted learner words"),
        route="debug",
        decision=TurnDecision(subject="active", move="debug", evidence="implementation_error"),
        plan=TeachingPlan(
            difficulty="medium",
            assistance_level="hint",
            solution_strategy="guided_debugging",
            topic="sliding_window",
            skill_level=0.5,
        ),
        suite_source="extracted",
        tutoring=TutoringView(execution=ExecutionView(learner=failed, final=patched)),
        events=[
            LearningEventCreate(topic="sliding_window", evidence_source="extracted", solved=False)
        ],
    )

    decision = build_eval_decision(state)

    assert decision.sandbox is not None
    assert (decision.sandbox.status, decision.sandbox.subject) == ("fail", "learner")
    assert (decision.sandbox.passed, decision.sandbox.total) == (1, 2)
    assert decision.sandbox.final_status == "pass"
    assert decision.sandbox.first_failing is not None
    assert decision.sandbox.first_failing.actual == 3
    assert decision.grade is not None
    assert (decision.grade.grade, decision.grade.source) == ("incorrect", "reading")
    assert decision.evidence[0].solved is False
    assert "untrusted learner words" not in decision.model_dump_json()
