"""Container lifecycle for the Adaptive Coding Agent sandbox.

This module is the ONLY place that talks to the Docker daemon to run
untrusted code. It never executes user-supplied code directly — it always
delegates to the locked-down `aca-sandbox` image (see
`docker/sandbox.Dockerfile` and `docker/harness/run.py`), which is started
with no network, a read-only root filesystem, dropped capabilities, and
hard resource limits.

Security invariants (see `CLAUDE.md`):
    - No `subprocess`, `os.system`/`os.exec*`/`os.spawn*`, `pty`, `exec`,
      `eval`, or `compile` anywhere in this package. All code execution
      happens inside the container via the Docker Engine API (docker-py).
    - Every container is created with network disabled, a read-only root
      filesystem (only a small noexec/nosuid/nodev tmpfs at /tmp), all
      capabilities dropped, no new privileges, and CPU/memory/pids/open-file
      limits.
    - Containers are always removed, even on timeout, cancellation, or
      infra failure — `sweep_orphans` is a startup-time backstop for
      anything a crash left behind, and is careful to only ever touch
      containers that are either not running or long stale, so it can never
      kill another live process's in-flight sandbox run (see `sweep_orphans`).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Final, cast

from docker.client import DockerClient
from docker.errors import DockerException, NotFound
from docker.models.containers import Container
from docker.types import LogConfig, Ulimit
from requests.exceptions import RequestException

import docker
from app.execution.base import RawRun

logger = logging.getLogger(__name__)

SANDBOX_LABEL: Final = "aca.sandbox"
INSTANCE_LABEL: Final = "aca.sandbox.instance"

#: Docker/network errors we must never let escape as anything other than a
#: `SandboxError` -- docker-py raises its own `DockerException` hierarchy, but
#: the underlying `requests` transport can also raise directly (ReadTimeout,
#: ConnectionError) without docker-py wrapping it.
_TRANSPORT_ERRORS: Final = (DockerException, RequestException)

#: Default `sweep_orphans` staleness window: comfortably above the maximum
#: per-run timeout (30s) plus kill grace, so a running container younger than
#: this is still presumed to belong to an in-flight run (possibly owned by a
#: different process) and is left alone.
DEFAULT_STALE_AFTER_S: Final = 300.0


@dataclass(frozen=True, slots=True)
class SandboxLimits:
    """Resource + isolation limits applied to every sandbox container."""

    image: str = "aca-sandbox:py3.11-v1"
    memory_mb: int = 256
    nano_cpus: int = 1_000_000_000
    pids_limit: int = 64
    tmpfs_mb: int = 16
    nofile: int = 256
    max_output_bytes: int = 1_048_576
    log_max_size: str = "4m"
    user: str = "10001:10001"
    kill_grace_s: float = 10.0


def container_kwargs(
    limits: SandboxLimits, env: Mapping[str, str], instance_id: str = ""
) -> dict[str, object]:
    """Build the exact `containers.create` kwargs for a sandbox run.

    Pure function (no I/O) so isolation settings can be asserted in tests
    without touching Docker. Deliberately omits `volumes`, `mounts`,
    `binds`, `devices`, `ports`, `cap_add`, and any `privileged=True` —
    the sandbox gets nothing beyond its read-only image and a scratch
    tmpfs. `instance_id` (a per-process random token) is stamped onto every
    container this process creates so `sweep_orphans` can reason about
    which process owns which containers.
    """
    return {
        "image": limits.image,
        "user": limits.user,
        "environment": dict(env),
        "labels": {SANDBOX_LABEL: "1", INSTANCE_LABEL: instance_id},
        "network_mode": "none",
        "network_disabled": True,
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "privileged": False,
        "mem_limit": f"{limits.memory_mb}m",
        "memswap_limit": f"{limits.memory_mb}m",
        "nano_cpus": limits.nano_cpus,
        "pids_limit": limits.pids_limit,
        "tmpfs": {"/tmp": f"rw,noexec,nosuid,nodev,size={limits.tmpfs_mb}m"},
        "init": True,
        "ipc_mode": "none",
        "ulimits": [Ulimit(name="nofile", soft=limits.nofile, hard=limits.nofile)],
        "log_config": LogConfig(
            type="json-file",
            config={"max-size": limits.log_max_size, "max-file": "1"},
        ),
        "working_dir": "/tmp",
        "hostname": "sandbox",
    }


class SandboxError(Exception):
    """Raised on infrastructure failure (docker unreachable, image missing,
    API error). Never carries user code or environment contents."""


def _truncate_tail(data: bytes, max_bytes: int) -> tuple[bytes, bool]:
    """Keep the LAST `max_bytes` of `data` (the harness report line is
    always last in stdout, so this preserves it)."""
    if len(data) <= max_bytes:
        return data, False
    return data[-max_bytes:], True


def _truncate_head(data: bytes, max_bytes: int) -> tuple[bytes, bool]:
    """Keep the FIRST `max_bytes` of `data`."""
    if len(data) <= max_bytes:
        return data, False
    return data[:max_bytes], True


def make_docker_client(timeout_s: int = 30) -> DockerClient:
    """Build a Docker Engine API client from the host's environment."""
    try:
        return docker.from_env(timeout=timeout_s)
    except _TRANSPORT_ERRORS as exc:
        raise SandboxError("unable to connect to the docker engine") from exc


# --------------------------------------------------------------------------
# sweep_orphans helpers: tolerate both the "list" (flat) and "inspect"
# (nested) docker-py attrs shapes, since we can't guarantee which one a given
# `Container` object was last refreshed with.
# --------------------------------------------------------------------------


def _is_running(attrs: Mapping[str, object]) -> bool:
    state = attrs.get("State")
    if isinstance(state, str):
        return state == "running"
    if isinstance(state, Mapping):
        return cast("Mapping[str, object]", state).get("Status") == "running"
    return False


def _parse_docker_timestamp(value: str) -> datetime | None:
    """Parse a docker Engine API timestamp (RFC3339, up to nanosecond
    precision) into a UTC `datetime`. Tolerant of the zero-value docker uses
    for a container that has never started (`"0001-01-01T00:00:00Z"`)."""
    if not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    if "." in text:
        head, _, rest = text.partition(".")
        frac = ""
        tz = ""
        for i, ch in enumerate(rest):
            if ch.isdigit():
                frac += ch
            else:
                tz = rest[i:]
                break
        text = f"{head}.{frac[:6].ljust(6, '0')}{tz}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _container_created_at(attrs: Mapping[str, object]) -> datetime | None:
    created = attrs.get("Created")
    if isinstance(created, bool):
        return None
    if isinstance(created, (int, float)):
        return datetime.fromtimestamp(created, tz=UTC)
    if isinstance(created, str):
        parsed = _parse_docker_timestamp(created)
        if parsed is not None:
            return parsed
    state = attrs.get("State")
    if isinstance(state, Mapping):
        started_at = cast("Mapping[str, object]", state).get("StartedAt")
        if isinstance(started_at, str):
            return _parse_docker_timestamp(started_at)
    return None


class DockerSandbox:
    """Runs a single sandbox container per submission, start to finish."""

    def __init__(
        self,
        client: DockerClient,
        limits: SandboxLimits,
        executor: ThreadPoolExecutor,
        *,
        instance_id: str | None = None,
        remove_retry_delay_s: float = 0.5,
    ) -> None:
        self._client = client
        self._limits = limits
        self._executor = executor
        self._instance_id = instance_id if instance_id is not None else uuid.uuid4().hex
        self._remove_retry_delay_s = remove_retry_delay_s

    async def run(self, env: Mapping[str, str], timeout_s: float) -> RawRun:
        """Create, start, wait for, and always remove a sandbox container."""
        kwargs = container_kwargs(self._limits, env, self._instance_id)
        loop = asyncio.get_running_loop()

        create_future = loop.run_in_executor(
            self._executor,
            lambda: self._client.containers.create(**kwargs),  # pyright: ignore[reportArgumentType]
        )
        try:
            # `asyncio.shield` is essential here, not optional: awaiting
            # `create_future` directly would let a cancellation of THIS
            # coroutine also cancel `create_future` itself (asyncio chains
            # cancellation through `run_in_executor`'s wrapped future even
            # though the underlying thread keeps running regardless) --
            # losing our only handle on the container it may still create.
            # Shielding lets our await be cancelled while `create_future`
            # keeps running to completion in the background, so the `except
            # CancelledError` branch below can still retrieve (and remove)
            # a container that gets created after all.
            container: Container = await asyncio.shield(create_future)
        except asyncio.CancelledError:
            leaked: Container | None = None
            try:
                leaked = await create_future
            except _TRANSPORT_ERRORS:
                leaked = None
            except Exception as exc:  # noqa: BLE001 - best-effort, we're already cancelling
                logger.warning(
                    "sandbox container create raced with cancellation: %s", type(exc).__name__
                )
            if leaked is not None:
                await self._remove(leaked)
            raise
        except _TRANSPORT_ERRORS as exc:
            raise SandboxError("failed to create sandbox container") from exc

        try:
            return await self._run_created_container(container, timeout_s)
        finally:
            await asyncio.shield(self._remove(container))

    async def _run_created_container(self, container: Container, timeout_s: float) -> RawRun:
        loop = asyncio.get_running_loop()
        wait_timeout_s = int(timeout_s) + 30
        timed_out = False

        t0 = perf_counter()
        try:
            await loop.run_in_executor(self._executor, container.start)
            try:
                await asyncio.wait_for(
                    loop.run_in_executor(
                        self._executor,
                        lambda: container.wait(timeout=wait_timeout_s),
                    ),
                    timeout=timeout_s,
                )
            except TimeoutError:
                timed_out = True
                await self._kill(container)
                try:
                    await asyncio.wait_for(
                        loop.run_in_executor(
                            self._executor,
                            lambda: container.wait(timeout=int(self._limits.kill_grace_s) + 5),
                        ),
                        timeout=self._limits.kill_grace_s,
                    )
                except (TimeoutError, *_TRANSPORT_ERRORS) as exc:
                    logger.warning(
                        "sandbox container did not exit after kill: %s", type(exc).__name__
                    )
        except _TRANSPORT_ERRORS as exc:
            raise SandboxError("failed to run sandbox container") from exc

        duration_ms = (perf_counter() - t0) * 1000

        try:
            await loop.run_in_executor(self._executor, container.reload)
        except _TRANSPORT_ERRORS as exc:
            raise SandboxError("failed to inspect sandbox container") from exc

        attrs = container.attrs
        state_raw = attrs.get("State") if attrs else None
        state: dict[str, object] = {}
        if isinstance(state_raw, dict):
            state = cast("dict[str, object]", state_raw)
        oom_killed = bool(state.get("OOMKilled", False))
        exit_code_raw = state.get("ExitCode")
        exit_code = int(exit_code_raw) if isinstance(exit_code_raw, int) else None

        try:
            stdout_raw: bytes = await loop.run_in_executor(
                self._executor, lambda: container.logs(stdout=True, stderr=False)
            )
            stderr_raw: bytes = await loop.run_in_executor(
                self._executor, lambda: container.logs(stdout=False, stderr=True)
            )
        except _TRANSPORT_ERRORS as exc:
            raise SandboxError("failed to read sandbox container logs") from exc

        stdout, stdout_truncated = _truncate_tail(stdout_raw, self._limits.max_output_bytes)
        stderr, stderr_truncated = _truncate_head(stderr_raw, self._limits.max_output_bytes)

        return RawRun(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            timed_out=timed_out,
            oom_killed=oom_killed,
            output_truncated=stdout_truncated or stderr_truncated,
        )

    async def _kill(self, container: Container) -> None:
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._executor, container.kill)
        except NotFound:
            pass
        except Exception as exc:  # noqa: BLE001 - cleanup failure must never propagate
            logger.warning("failed to kill sandbox container: %s", type(exc).__name__)

    async def _remove(self, container: Container) -> None:
        """Remove `container`, retrying once after a short delay on any
        failure other than `NotFound` (already gone). If it still fails, log
        the exception class only and give up -- `sweep_orphans` is the
        backstop for anything left behind."""
        loop = asyncio.get_running_loop()
        for attempt in (1, 2):
            try:
                await loop.run_in_executor(
                    self._executor, lambda: container.remove(force=True, v=True)
                )
                return
            except NotFound:
                return
            except Exception as exc:  # noqa: BLE001 - cleanup failure must never propagate
                if attempt == 1:
                    logger.warning(
                        "failed to remove sandbox container, retrying: %s", type(exc).__name__
                    )
                    await asyncio.sleep(self._remove_retry_delay_s)
                    continue
                logger.warning("failed to remove sandbox container: %s", type(exc).__name__)

    async def sweep_orphans(self, *, stale_after_s: float = DEFAULT_STALE_AFTER_S) -> int:
        """Remove leftover sandbox containers (e.g. after a crash).

        Only removes a container if it is EITHER not currently running, OR
        running but older than `stale_after_s` (comfortably above the max
        per-run timeout) -- so a running, fresh container that belongs to
        another live process's in-flight run is never touched.
        """
        loop = asyncio.get_running_loop()
        try:
            containers: list[Container] = await loop.run_in_executor(
                self._executor,
                lambda: self._client.containers.list(
                    all=True, filters={"label": f"{SANDBOX_LABEL}=1"}
                ),
            )
        except Exception as exc:  # noqa: BLE001 - listing failure must never propagate
            logger.warning("failed to list orphan sandbox containers: %s", type(exc).__name__)
            return 0

        removed = 0
        now = datetime.now(UTC)
        for container in containers:
            try:
                attrs = cast("Mapping[str, object]", container.attrs or {})
                should_remove = not _is_running(attrs)
                if not should_remove:
                    created_at = _container_created_at(attrs)
                    should_remove = (
                        created_at is not None
                        and (now - created_at).total_seconds() > stale_after_s
                    )
                if should_remove:
                    await self._remove(container)
                    removed += 1
            except Exception as exc:  # noqa: BLE001 - one bad container must never abort the sweep
                logger.warning("failed to remove orphan sandbox container: %s", type(exc).__name__)
        return removed

    async def ping(self) -> bool:
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(self._executor, self._client.ping)
        except Exception:  # noqa: BLE001 - a broken ping must never propagate
            return False
