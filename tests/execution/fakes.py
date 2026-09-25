"""Hand-written, typed docker-py fakes for unit-testing `DockerSandbox`.

No real Docker daemon is involved: these fakes implement just enough of the
`docker.client.DockerClient` / `docker.models.containers.Container` surface
that `app.execution.sandbox.DockerSandbox` calls, with hooks the tests can
configure per-scenario. They are cast to the real docker-py types at the
`DockerSandbox(...)` construction site rather than trying to satisfy the
(very large) real stub `Protocol`s structurally.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from docker.errors import DockerException, NotFound


class FakeContainer:
    """A configurable stand-in for `docker.models.containers.Container`."""

    def __init__(
        self,
        *,
        name: str = "fake",
        exit_code: int | None = 0,
        oom_killed: bool = False,
        stdout: bytes = b"",
        stderr: bytes = b"",
        on_start: Callable[[], None] | None = None,
        on_wait: Callable[[float | None], dict[str, Any]] | None = None,
        on_kill: Callable[[], None] | None = None,
        # `sweep_orphans` fields: default to "not running" so a fake built
        # with no extra args behaves like a leftover, dead container.
        state_status: str = "exited",
        started_at: str | None = None,
        created: object | None = None,
        # `_remove` retry: a queue of side effects consumed one per `remove()`
        # call; `None` means "succeed", an exception instance means "raise
        # this". Once exhausted, falls back to the plain not-found behaviour.
        remove_side_effects: list[BaseException | None] | None = None,
    ) -> None:
        self.name = name
        self.attrs: dict[str, Any] = {
            "State": {
                "OOMKilled": oom_killed,
                "ExitCode": exit_code,
                "Status": state_status,
                "StartedAt": started_at,
            },
            "Created": created,
        }
        self._stdout = stdout
        self._stderr = stderr
        self._on_start = on_start
        self._on_wait = on_wait
        self._on_kill = on_kill
        self._remove_side_effects = list(remove_side_effects) if remove_side_effects else None

        self.start_calls = 0
        self.wait_calls = 0
        self.kill_calls = 0
        self.reload_calls = 0
        self.remove_calls = 0
        self.removed = False

    def start(self) -> None:
        self.start_calls += 1
        if self._on_start is not None:
            self._on_start()

    def wait(self, *, timeout: float | None = None, condition: str | None = None) -> dict[str, Any]:
        self.wait_calls += 1
        if self._on_wait is not None:
            return self._on_wait(timeout)
        return {"StatusCode": self.attrs["State"]["ExitCode"] or 0}

    def kill(self, signal: str | int | None = None) -> None:
        self.kill_calls += 1
        if self._on_kill is not None:
            self._on_kill()

    def reload(self) -> None:
        self.reload_calls += 1

    def logs(self, *, stdout: bool = True, stderr: bool = True) -> bytes:
        if stdout and not stderr:
            return self._stdout
        if stderr and not stdout:
            return self._stderr
        return self._stdout + self._stderr

    def remove(self, *, force: bool = False, v: bool = False) -> None:
        self.remove_calls += 1
        if self._remove_side_effects:
            effect = self._remove_side_effects.pop(0)
            if effect is not None:
                raise effect
            self.removed = True
            return
        if self.removed:
            raise NotFound("already removed")
        self.removed = True


class FakeContainerCollection:
    def __init__(
        self,
        *,
        create_result: FakeContainer | Callable[..., FakeContainer] | None = None,
        create_error: Exception | None = None,
        list_result: list[FakeContainer] | None = None,
    ) -> None:
        self._create_result = create_result
        self._create_error = create_error
        self.list_result = list_result or []
        self.create_calls: list[dict[str, Any]] = []
        self.list_calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> FakeContainer:
        self.create_calls.append(kwargs)
        if self._create_error is not None:
            raise self._create_error
        if callable(self._create_result) and not isinstance(self._create_result, FakeContainer):
            return self._create_result(**kwargs)
        if isinstance(self._create_result, FakeContainer):
            return self._create_result
        return FakeContainer()

    def list(
        self, *, all: bool = False, filters: dict[str, Any] | None = None
    ) -> list[FakeContainer]:
        self.list_calls.append({"all": all, "filters": filters})
        return self.list_result


class FakeDockerClient:
    """A configurable stand-in for `docker.client.DockerClient`."""

    def __init__(
        self,
        *,
        containers: FakeContainerCollection | None = None,
        ping_ok: bool = True,
    ) -> None:
        self.containers = containers or FakeContainerCollection()
        self._ping_ok = ping_ok

    def ping(self) -> bool:
        if not self._ping_ok:
            raise DockerException("daemon unreachable")
        return True
