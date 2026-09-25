"""Unit tests for `app.execution.runner.SandboxRunner` + `encode_payload` +
`build_sandbox_runner`, against a hand-written fake `SandboxBackend`. No
real Docker daemon is used here.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Awaitable, Callable, Mapping

import pytest

from app.config import Settings
from app.execution.base import RawRun
from app.execution.runner import (
    MAX_PAYLOAD_B64_BYTES,
    SandboxRunner,
    build_sandbox_runner,
    encode_payload,
)
from app.execution.sandbox import SandboxError
from app.schemas.execution import ExecutionRequest, TestCase, TestSuite


def make_request(**overrides: object) -> ExecutionRequest:
    params: dict[str, object] = {"language": "python", "code": "print('hi')"}
    params.update(overrides)
    return ExecutionRequest(**params)  # type: ignore[arg-type]


def ok_raw(env: Mapping[str, str]) -> RawRun:
    """A `RawRun` carrying a well-formed, well-framed "script" report for
    whatever marker the runner generated for this call."""
    marker = env["ACA_MARKER"]
    report: dict[str, object] = {
        "version": 1,
        "phase": "script",
        "error": None,
        "stdout": "",
        "stderr": "",
        "cases": [],
    }
    stdout = f"{marker}{json.dumps(report)}{marker}\n".encode()
    return RawRun(
        exit_code=0,
        stdout=stdout,
        stderr=b"",
        duration_ms=1.0,
        timed_out=False,
        oom_killed=False,
        output_truncated=False,
    )


class FakeBackend:
    """A configurable stand-in for `app.execution.base.SandboxBackend`."""

    def __init__(
        self,
        *,
        error: Exception | None = None,
        on_run: Callable[[Mapping[str, str], float], Awaitable[RawRun]] | None = None,
    ) -> None:
        self.calls: list[tuple[dict[str, str], float]] = []
        self._error = error
        self._on_run = on_run

    async def run(self, env: Mapping[str, str], timeout_s: float) -> RawRun:
        self.calls.append((dict(env), timeout_s))
        if self._error is not None:
            raise self._error
        if self._on_run is not None:
            return await self._on_run(env, timeout_s)
        return ok_raw(env)


# --------------------------------------------------------------------------
# encode_payload
# --------------------------------------------------------------------------


def test_encode_payload_round_trips_script_mode() -> None:
    request = make_request(code="print(1)")

    decoded = json.loads(base64.b64decode(encode_payload(request)))

    assert decoded == {"version": 1, "language": "python", "code": "print(1)", "tests": None}


def test_encode_payload_round_trips_with_tests() -> None:
    request = make_request(
        code="def add(a, b): return a + b",
        tests=TestSuite(
            entrypoint="add",
            cases=[TestCase(name="c1", args=[1, 2], kwargs={}, expected=3)],
        ),
    )

    decoded = json.loads(base64.b64decode(encode_payload(request)))

    assert decoded == {
        "version": 1,
        "language": "python",
        "code": "def add(a, b): return a + b",
        "tests": {
            "entrypoint": "add",
            "cases": [{"name": "c1", "args": [1, 2], "kwargs": {}, "expected": 3}],
        },
    }


# --------------------------------------------------------------------------
# Rejections (never call the backend)
# --------------------------------------------------------------------------


async def test_payload_too_large_is_rejected_without_calling_backend() -> None:
    backend = FakeBackend()
    runner = SandboxRunner({"python": backend}, max_concurrent=4, queue_timeout_s=5.0)
    # Multibyte chars inflate both the json-escaped length and the base64
    # blow-up (~4/3x), comfortably pushing the encoded payload over 120_000
    # bytes while staying under `MAX_CODE_CHARS` (50_000).
    huge_code = "# " + "é" * 49_000
    request = make_request(code=huge_code)
    assert len(encode_payload(request)) > MAX_PAYLOAD_B64_BYTES

    result = await runner.run(request)

    assert result.status == "rejected"
    assert result.error is not None
    assert result.error.type == "PayloadTooLarge"
    assert backend.calls == []


async def test_unsupported_language_is_rejected_without_calling_backend() -> None:
    runner = SandboxRunner({}, max_concurrent=4, queue_timeout_s=5.0)

    result = await runner.run(make_request())

    assert result.status == "rejected"
    assert result.error is not None
    assert result.error.type == "UnsupportedLanguage"


# --------------------------------------------------------------------------
# env passed to the backend
# --------------------------------------------------------------------------


async def test_env_contains_only_payload_and_marker() -> None:
    backend = FakeBackend()
    runner = SandboxRunner({"python": backend}, max_concurrent=4, queue_timeout_s=5.0)

    result = await runner.run(make_request())

    assert result.status == "completed"
    assert len(backend.calls) == 1
    env, timeout_s = backend.calls[0]
    assert set(env.keys()) == {"ACA_PAYLOAD", "ACA_MARKER"}
    marker = env["ACA_MARKER"]
    assert len(marker) == 32
    int(marker, 16)  # must be valid hex
    assert timeout_s == make_request().timeout_s


# --------------------------------------------------------------------------
# SandboxError -> sandbox_error
# --------------------------------------------------------------------------


async def test_backend_sandbox_error_maps_to_sandbox_error_status() -> None:
    backend = FakeBackend(error=SandboxError("docker unreachable"))
    runner = SandboxRunner({"python": backend}, max_concurrent=4, queue_timeout_s=5.0)

    result = await runner.run(make_request())

    assert result.status == "sandbox_error"
    assert result.error is not None
    assert result.error.type == "SandboxUnavailable"


# --------------------------------------------------------------------------
# Concurrency cap: queue, don't reject
# --------------------------------------------------------------------------


async def test_runs_beyond_cap_queue_and_all_complete() -> None:
    release = asyncio.Event()
    entered = 0
    entered_two = asyncio.Event()

    async def on_run(env: Mapping[str, str], timeout_s: float) -> RawRun:
        nonlocal entered
        entered += 1
        if entered == 2:
            entered_two.set()
        await release.wait()
        return ok_raw(env)

    backend = FakeBackend(on_run=on_run)
    runner = SandboxRunner({"python": backend}, max_concurrent=2, queue_timeout_s=10.0)

    tasks = [asyncio.create_task(runner.run(make_request())) for _ in range(6)]
    await asyncio.wait_for(entered_two.wait(), timeout=5.0)

    assert runner.peak_in_flight == 2
    assert entered == 2  # the other 4 are queued behind the semaphore, not rejected

    release.set()
    results = await asyncio.gather(*tasks)

    assert len(results) == 6
    assert all(r.status == "completed" for r in results)
    assert runner.in_flight == 0


async def test_queue_wait_exceeding_timeout_is_sandbox_busy() -> None:
    release = asyncio.Event()

    async def on_run(env: Mapping[str, str], timeout_s: float) -> RawRun:
        await release.wait()
        return ok_raw(env)

    backend = FakeBackend(on_run=on_run)
    runner = SandboxRunner({"python": backend}, max_concurrent=1, queue_timeout_s=0.1)

    blocking_task = asyncio.create_task(runner.run(make_request()))
    await asyncio.sleep(0.02)  # let it acquire the single slot

    result = await runner.run(make_request())

    assert result.status == "sandbox_error"
    assert result.error is not None
    assert result.error.type == "SandboxBusy"

    release.set()
    await blocking_task


async def test_cancellation_releases_semaphore() -> None:
    release = asyncio.Event()
    entered = asyncio.Event()
    release_immediately = False

    async def on_run(env: Mapping[str, str], timeout_s: float) -> RawRun:
        entered.set()
        if not release_immediately:
            await release.wait()
        return ok_raw(env)

    backend = FakeBackend(on_run=on_run)
    runner = SandboxRunner({"python": backend}, max_concurrent=1, queue_timeout_s=2.0)

    task = asyncio.create_task(runner.run(make_request()))
    await asyncio.wait_for(entered.wait(), timeout=5.0)
    assert runner.in_flight == 1

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert runner.in_flight == 0

    release_immediately = True
    result = await asyncio.wait_for(runner.run(make_request()), timeout=2.0)
    assert result.status == "completed"


# --------------------------------------------------------------------------
# build_sandbox_runner: fail-soft
# --------------------------------------------------------------------------


async def test_build_sandbox_runner_disabled_returns_none(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(sandbox_enabled=False)

    runner, close = await build_sandbox_runner(settings)
    close()

    assert runner is None


async def test_build_sandbox_runner_docker_unavailable_returns_none(
    make_settings: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings(sandbox_enabled=True)

    def _raise() -> None:
        raise SandboxError("unable to connect to the docker engine")

    monkeypatch.setattr("app.execution.runner.make_docker_client", _raise)

    runner, close = await build_sandbox_runner(settings)
    close()

    assert runner is None


async def test_build_sandbox_runner_generic_exception_from_ping_returns_none(
    make_settings: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stopped Docker Desktop can surface on Windows as a non-docker-py
    exception (e.g. from requests/pywin32); it must still fail-soft."""
    settings = make_settings(sandbox_enabled=True)

    class _FakeClient:
        def ping(self) -> None:
            raise OSError("winerror surfaced from requests/pywin32")

        def close(self) -> None:
            return None

    monkeypatch.setattr("app.execution.runner.make_docker_client", lambda: _FakeClient())

    runner, close = await build_sandbox_runner(settings)
    close()

    assert runner is None


async def test_build_sandbox_runner_startup_timeout_returns_none(
    make_settings: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings(sandbox_enabled=True, sandbox_startup_timeout_s=0.05)

    def _slow_make_docker_client() -> object:
        import time

        time.sleep(1.0)
        raise AssertionError("should not reach ping")

    monkeypatch.setattr("app.execution.runner.make_docker_client", _slow_make_docker_client)

    runner, close = await build_sandbox_runner(settings)
    close()

    assert runner is None


async def test_build_sandbox_runner_closes_client_on_probe_failure(
    make_settings: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings(sandbox_enabled=True)
    closed = False

    class _FakeClient:
        def ping(self) -> None:
            raise SandboxError("ping failed")

        def close(self) -> None:
            nonlocal closed
            closed = True

    monkeypatch.setattr("app.execution.runner.make_docker_client", lambda: _FakeClient())

    runner, close = await build_sandbox_runner(settings)
    close()

    assert runner is None
    assert closed is True
