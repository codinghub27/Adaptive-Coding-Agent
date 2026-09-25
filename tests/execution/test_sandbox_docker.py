"""Real-Docker smoke test for `DockerSandbox`.

Runs the actual `aca-sandbox` image with a trivial script-mode payload and
checks the container leaves nothing behind. Skipped (not failed) when Docker
isn't reachable or the image hasn't been built locally.
"""

from __future__ import annotations

import base64
import json
import secrets
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor

import pytest
from docker.errors import DockerException, ImageNotFound

from app.execution.sandbox import SANDBOX_LABEL, DockerSandbox, SandboxLimits, make_docker_client

pytestmark = pytest.mark.sandbox


@pytest.fixture
def executor() -> Generator[ThreadPoolExecutor, None, None]:
    pool = ThreadPoolExecutor(max_workers=4)
    yield pool
    pool.shutdown(wait=True)


def _skip_unless_docker_available(limits: SandboxLimits) -> None:
    try:
        client = make_docker_client()
        if not client.ping():
            pytest.skip("docker engine not reachable")
        client.images.get(limits.image)
    except DockerException as exc:
        if isinstance(exc, ImageNotFound):
            pytest.skip(f"sandbox image {limits.image!r} not built locally")
        pytest.skip(f"docker engine not usable: {type(exc).__name__}")


async def test_real_sandbox_runs_script_and_cleans_up(
    executor: ThreadPoolExecutor,
) -> None:
    limits = SandboxLimits()
    _skip_unless_docker_available(limits)

    client = make_docker_client()
    sandbox = DockerSandbox(client, limits, executor)

    marker = secrets.token_hex(16)
    payload = {"version": 1, "language": "python", "code": "print('hi')", "tests": None}
    payload_b64 = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    env = {"ACA_MARKER": marker, "ACA_PAYLOAD": payload_b64}

    result = await sandbox.run(env, timeout_s=10.0)

    assert result.exit_code == 0
    assert marker.encode("ascii") in result.stdout

    leftover = client.containers.list(all=True, filters={"label": f"{SANDBOX_LABEL}=1"})
    assert leftover == []
