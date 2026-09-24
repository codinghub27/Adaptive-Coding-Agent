"""Live manual tests for `POST /understand` against the real LLM provider.

Opt-in only (`RUN_LIVE_LLM=1`) -- these tests call the real, configured
LLM/vision provider over the network and consume real API credits. Unlike
the rest of the suite, they build the app with the real `Settings` (loaded
from `.env`) and run its real lifespan, mirroring how the `integration`
-marked test in `tests/test_health.py` opts out of the autouse env
isolation: `get_settings()` reads `.env` directly, which
`_isolate_settings_env`'s `monkeypatch.delenv` calls never touch.

`asgi-lifespan` is not installed, so the lifespan is driven directly via
`app.router.lifespan_context(app)` around an `httpx.ASGITransport` client,
per Starlette's own recommended testing pattern.

Run with:

    RUN_LIVE_LLM=1 pytest tests/input/test_manual_live.py -s -q
"""

import os
from uuid import uuid4

import httpx
import pytest

from app.auth.deps import get_current_user
from app.main import create_app
from app.schemas.auth import AuthUser
from tests.input.fixtures.make_fixtures import TWO_SUM_PATH

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("RUN_LIVE_LLM") != "1",
        reason="live LLM tests are opt-in; set RUN_LIVE_LLM=1 to run",
    ),
]

DEBUG_TEXT = """\
def find_max(nums):
    best = nums[0]
    for i in range(len(nums) + 1):
        if nums[i] > best:
            best = nums[i]
    return best

IndexError: list index out of range
"""


async def test_manual_1_debug_text_classified_as_debug_or_error() -> None:
    """Manual Test 1: pasted buggy function + traceback, no question."""
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthUser(
        id=uuid4(), handle="live-test-user", session_id=uuid4()
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/understand", data={"text": DEBUG_TEXT})

    print("\nManual Test 1 response:", response.status_code, response.json())

    assert response.status_code == 200
    body = response.json()
    assert body["input"]["code"]
    assert "def find_max" in body["input"]["code"][0]["content"]
    assert "IndexError" in (body["input"]["error"] or "")
    assert body["intent"]["intent"] in {"CODE_DEBUG", "ERROR_EXPLANATION"}
    assert body["intent"]["confidence"] >= 0.7
    assert body["intent"]["low_confidence"] is False


async def test_manual_2_image_only_classified_as_dsa() -> None:
    """Manual Test 2: LeetCode-style screenshot, no text."""
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthUser(
        id=uuid4(), handle="live-test-user", session_id=uuid4()
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            files = {
                "image": ("leetcode_two_sum.png", TWO_SUM_PATH.read_bytes(), "image/png"),
            }
            response = await client.post("/understand", files=files)

    print("\nManual Test 2 response:", response.status_code, response.json())

    assert response.status_code == 200
    body = response.json()
    assert body["input"]["source"] == "image"
    problem = (body["input"]["problem"] or "").lower()
    assert "two sum" in problem or "target" in problem
    assert len(body["input"]["constraints"]) >= 2
    assert any("nums.length" in c for c in body["input"]["constraints"])
    assert body["input"]["code"] == []
    assert body["intent"]["intent"] in {"DSA_SOLVE", "APPROACH_DISCUSSION"}
