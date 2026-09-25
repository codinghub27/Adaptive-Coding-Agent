"""Unit tests for `app.execution.sandbox` container lifecycle management.

No real Docker daemon is used here: `DockerSandbox` is exercised against a
hand-written, typed fake client (see `tests/execution/fakes.py`). The one
test that touches a real Docker engine lives in `test_sandbox_docker.py`
and is marked `@pytest.mark.sandbox`.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from typing import Any, cast

import pytest
import requests.exceptions
from docker.client import DockerClient
from docker.errors import APIError
from docker.types import LogConfig, Ulimit

from app.execution.sandbox import (
    INSTANCE_LABEL,
    SANDBOX_LABEL,
    DockerSandbox,
    SandboxError,
    SandboxLimits,
    container_kwargs,
)
from tests.execution.fakes import FakeContainer, FakeContainerCollection, FakeDockerClient


@pytest.fixture
def executor() -> Generator[ThreadPoolExecutor, None, None]:
    pool = ThreadPoolExecutor(max_workers=8)
    yield pool
    pool.shutdown(wait=True)


def make_sandbox(
    client: FakeDockerClient,
    executor: ThreadPoolExecutor,
    limits: SandboxLimits | None = None,
    *,
    remove_retry_delay_s: float = 0.5,
) -> DockerSandbox:
    return DockerSandbox(
        cast("DockerClient", client),
        limits or SandboxLimits(),
        executor,
        remove_retry_delay_s=remove_retry_delay_s,
    )


# --------------------------------------------------------------------------
# container_kwargs: pure isolation-settings assertions
# --------------------------------------------------------------------------


def test_container_kwargs_isolation_settings() -> None:
    limits = SandboxLimits()
    kwargs = container_kwargs(limits, {"ACA_MARKER": "abc", "ACA_PAYLOAD": "xyz"}, "instance-1")

    assert kwargs["image"] == limits.image
    assert kwargs["user"] == limits.user
    assert kwargs["environment"] == {"ACA_MARKER": "abc", "ACA_PAYLOAD": "xyz"}
    assert kwargs["labels"] == {SANDBOX_LABEL: "1", INSTANCE_LABEL: "instance-1"}
    assert kwargs["network_mode"] == "none"
    assert kwargs["network_disabled"] is True
    assert kwargs["read_only"] is True
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["security_opt"] == ["no-new-privileges:true"]
    assert kwargs["privileged"] is False
    assert kwargs["mem_limit"] == f"{limits.memory_mb}m"
    assert kwargs["memswap_limit"] == f"{limits.memory_mb}m"
    assert kwargs["nano_cpus"] == limits.nano_cpus
    assert kwargs["pids_limit"] == limits.pids_limit
    assert kwargs["tmpfs"] == {"/tmp": f"rw,noexec,nosuid,nodev,size={limits.tmpfs_mb}m"}
    assert kwargs["init"] is True
    assert kwargs["ipc_mode"] == "none"
    assert kwargs["working_dir"] == "/tmp"
    assert kwargs["hostname"] == "sandbox"

    ulimits = cast("list[Ulimit]", kwargs["ulimits"])
    assert len(ulimits) == 1
    assert ulimits[0].name == "nofile"
    assert ulimits[0].soft == limits.nofile
    assert ulimits[0].hard == limits.nofile

    log_config = cast("LogConfig", kwargs["log_config"])
    assert log_config.type == "json-file"
    assert log_config.config == {"max-size": limits.log_max_size, "max-file": "1"}


def test_container_kwargs_has_no_host_filesystem_or_privilege_escape() -> None:
    kwargs = container_kwargs(SandboxLimits(), {})
    forbidden_keys = {
        "volumes",
        "mounts",
        "binds",
        "devices",
        "ports",
        "cap_add",
        "entrypoint",
        "command",
    }
    assert forbidden_keys.isdisjoint(kwargs.keys())
    assert kwargs.get("privileged") is not True


# --------------------------------------------------------------------------
# Success path
# --------------------------------------------------------------------------


async def test_run_success_returns_raw_run_and_removes_container(
    executor: ThreadPoolExecutor,
) -> None:
    container = FakeContainer(exit_code=0, stdout=b"hello", stderr=b"")
    client = FakeDockerClient(containers=FakeContainerCollection(create_result=container))
    sandbox = make_sandbox(client, executor)

    result = await sandbox.run({}, timeout_s=5.0)

    assert result.exit_code == 0
    assert result.stdout == b"hello"
    assert result.stderr == b""
    assert result.timed_out is False
    assert result.oom_killed is False
    assert result.output_truncated is False
    assert container.remove_calls == 1
    assert container.removed is True


# --------------------------------------------------------------------------
# Timeout path
# --------------------------------------------------------------------------


async def test_run_timeout_kills_and_removes_container(executor: ThreadPoolExecutor) -> None:
    killed_event = threading.Event()

    def on_wait(_timeout: float | None) -> dict[str, object]:
        killed_event.wait(timeout=5)
        return {"StatusCode": 137}

    def on_kill() -> None:
        killed_event.set()

    container = FakeContainer(exit_code=137, on_wait=on_wait, on_kill=on_kill)
    client = FakeDockerClient(containers=FakeContainerCollection(create_result=container))
    sandbox = make_sandbox(client, executor, SandboxLimits(kill_grace_s=2.0))

    start = asyncio.get_event_loop().time()
    result = await asyncio.wait_for(sandbox.run({}, timeout_s=0.2), timeout=5.0)
    elapsed = asyncio.get_event_loop().time() - start

    assert result.timed_out is True
    assert container.kill_calls == 1
    assert container.remove_calls == 1
    assert elapsed < 3.0


# --------------------------------------------------------------------------
# Exceptions
# --------------------------------------------------------------------------


async def test_run_start_failure_raises_sandbox_error_and_removes_container(
    executor: ThreadPoolExecutor,
) -> None:
    def on_start() -> None:
        raise APIError("boom")

    container = FakeContainer(on_start=on_start)
    client = FakeDockerClient(containers=FakeContainerCollection(create_result=container))
    sandbox = make_sandbox(client, executor)

    with pytest.raises(SandboxError):
        await sandbox.run({}, timeout_s=5.0)

    assert container.remove_calls == 1


async def test_run_create_failure_raises_sandbox_error(executor: ThreadPoolExecutor) -> None:
    collection = FakeContainerCollection(create_error=APIError("no such image"))
    client = FakeDockerClient(containers=collection)
    sandbox = make_sandbox(client, executor)

    with pytest.raises(SandboxError):
        await sandbox.run({}, timeout_s=5.0)

    assert len(collection.create_calls) == 1


# --------------------------------------------------------------------------
# Cancellation
# --------------------------------------------------------------------------


async def test_run_cancellation_removes_container(executor: ThreadPoolExecutor) -> None:
    release_event = threading.Event()
    started_waiting = threading.Event()

    def on_wait(_timeout: float | None) -> dict[str, object]:
        started_waiting.set()
        release_event.wait()
        return {"StatusCode": 0}

    container = FakeContainer(on_wait=on_wait)
    client = FakeDockerClient(containers=FakeContainerCollection(create_result=container))
    sandbox = make_sandbox(client, executor)

    task = asyncio.create_task(sandbox.run({}, timeout_s=30.0))
    await asyncio.get_event_loop().run_in_executor(None, started_waiting.wait, 5)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    for _ in range(20):
        if container.remove_calls >= 1:
            break
        await asyncio.sleep(0.05)

    assert container.remove_calls == 1
    release_event.set()  # free the fake's background thread


# --------------------------------------------------------------------------
# OOM + truncation
# --------------------------------------------------------------------------


async def test_run_maps_oom_killed_flag(executor: ThreadPoolExecutor) -> None:
    container = FakeContainer(exit_code=137, oom_killed=True)
    client = FakeDockerClient(containers=FakeContainerCollection(create_result=container))
    sandbox = make_sandbox(client, executor)

    result = await sandbox.run({}, timeout_s=5.0)

    assert result.oom_killed is True


async def test_run_truncates_stdout_tail_and_stderr_head(executor: ThreadPoolExecutor) -> None:
    stdout = b"0123456789ABCDEF"  # 16 bytes
    stderr = b"abcdefghijklmnop"  # 16 bytes
    container = FakeContainer(exit_code=0, stdout=stdout, stderr=stderr)
    client = FakeDockerClient(containers=FakeContainerCollection(create_result=container))
    limits = SandboxLimits(max_output_bytes=10)
    sandbox = make_sandbox(client, executor, limits)

    result = await sandbox.run({}, timeout_s=5.0)

    assert result.stdout == b"6789ABCDEF"  # last 10 bytes
    assert result.stderr == b"abcdefghij"  # first 10 bytes
    assert result.output_truncated is True


# --------------------------------------------------------------------------
# sweep_orphans
# --------------------------------------------------------------------------


async def test_sweep_orphans_removes_labelled_containers_and_counts(
    executor: ThreadPoolExecutor,
) -> None:
    c1 = FakeContainer(name="a")
    c2 = FakeContainer(name="b")
    collection = FakeContainerCollection(list_result=[c1, c2])
    client = FakeDockerClient(containers=collection)
    sandbox = make_sandbox(client, executor)

    removed = await sandbox.sweep_orphans()

    assert removed == 2
    assert c1.remove_calls == 1
    assert c2.remove_calls == 1
    assert collection.list_calls == [{"all": True, "filters": {"label": f"{SANDBOX_LABEL}=1"}}]


# --------------------------------------------------------------------------
# F5 -- sweep_orphans must never kill another live process's containers
# --------------------------------------------------------------------------


async def test_sweep_orphans_does_not_remove_running_fresh_container(
    executor: ThreadPoolExecutor,
) -> None:
    from datetime import UTC, datetime, timedelta

    fresh = (datetime.now(UTC) - timedelta(seconds=5)).isoformat().replace("+00:00", "Z")
    running = FakeContainer(name="live", state_status="running", started_at=fresh)
    collection = FakeContainerCollection(list_result=[running])
    client = FakeDockerClient(containers=collection)
    sandbox = make_sandbox(client, executor)

    removed = await sandbox.sweep_orphans()

    assert removed == 0
    assert running.remove_calls == 0


async def test_sweep_orphans_removes_running_but_stale_container(
    executor: ThreadPoolExecutor,
) -> None:
    from datetime import UTC, datetime, timedelta

    stale = (datetime.now(UTC) - timedelta(seconds=600)).isoformat().replace("+00:00", "Z")
    running = FakeContainer(name="stale", state_status="running", started_at=stale)
    collection = FakeContainerCollection(list_result=[running])
    client = FakeDockerClient(containers=collection)
    sandbox = make_sandbox(client, executor)

    removed = await sandbox.sweep_orphans(stale_after_s=300.0)

    assert removed == 1
    assert running.remove_calls == 1


async def test_sweep_orphans_removes_non_running_regardless_of_age(
    executor: ThreadPoolExecutor,
) -> None:
    exited = FakeContainer(name="exited", state_status="exited", started_at=None)
    collection = FakeContainerCollection(list_result=[exited])
    client = FakeDockerClient(containers=collection)
    sandbox = make_sandbox(client, executor)

    removed = await sandbox.sweep_orphans()

    assert removed == 1


# --------------------------------------------------------------------------
# F6 -- no leak on cancellation during create; retry removal
# --------------------------------------------------------------------------


async def test_cancel_during_slow_create_still_removes_container(
    executor: ThreadPoolExecutor,
) -> None:
    created_container = FakeContainer(name="slow-create")

    def slow_create(**_kwargs: Any) -> FakeContainer:
        time.sleep(0.3)
        return created_container

    collection = FakeContainerCollection(create_result=slow_create)
    client = FakeDockerClient(containers=collection)
    sandbox = make_sandbox(client, executor)

    task = asyncio.create_task(sandbox.run({}, timeout_s=5.0))
    await asyncio.sleep(0.05)  # let the create call start running in the executor
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    for _ in range(40):
        if created_container.remove_calls >= 1:
            break
        await asyncio.sleep(0.05)

    assert created_container.remove_calls == 1
    assert created_container.removed is True


async def test_remove_retries_once_then_succeeds(executor: ThreadPoolExecutor) -> None:
    container = FakeContainer(exit_code=0, remove_side_effects=[APIError("transient"), None])
    client = FakeDockerClient(containers=FakeContainerCollection(create_result=container))
    sandbox = make_sandbox(client, executor, remove_retry_delay_s=0.01)

    result = await sandbox.run({}, timeout_s=5.0)

    assert result.exit_code == 0
    assert container.remove_calls == 2
    assert container.removed is True


async def test_remove_gives_up_after_second_failure(executor: ThreadPoolExecutor) -> None:
    container = FakeContainer(
        exit_code=0,
        remove_side_effects=[APIError("transient"), APIError("still broken")],
    )
    client = FakeDockerClient(containers=FakeContainerCollection(create_result=container))
    sandbox = make_sandbox(client, executor, remove_retry_delay_s=0.01)

    result = await sandbox.run({}, timeout_s=5.0)

    assert result.exit_code == 0
    assert container.remove_calls == 2
    assert container.removed is False


# --------------------------------------------------------------------------
# F7 -- requests exceptions raised directly by docker-py's transport
# --------------------------------------------------------------------------


async def test_wait_raising_connection_error_maps_to_sandbox_error(
    executor: ThreadPoolExecutor,
) -> None:
    def on_wait(_timeout: float | None) -> dict[str, object]:
        raise requests.exceptions.ConnectionError("connection reset")

    container = FakeContainer(on_wait=on_wait)
    client = FakeDockerClient(containers=FakeContainerCollection(create_result=container))
    sandbox = make_sandbox(client, executor)

    with pytest.raises(SandboxError):
        await sandbox.run({}, timeout_s=5.0)

    assert container.remove_calls == 1


async def test_remove_raising_connection_error_does_not_propagate(
    executor: ThreadPoolExecutor,
) -> None:
    container = FakeContainer(
        exit_code=0,
        remove_side_effects=[
            requests.exceptions.ConnectionError("boom"),
            requests.exceptions.ConnectionError("boom again"),
        ],
    )
    client = FakeDockerClient(containers=FakeContainerCollection(create_result=container))
    sandbox = make_sandbox(client, executor, remove_retry_delay_s=0.01)

    result = await sandbox.run({}, timeout_s=5.0)

    assert result.exit_code == 0
    assert container.remove_calls == 2
