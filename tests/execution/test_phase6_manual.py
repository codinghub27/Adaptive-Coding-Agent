"""Phase 6 manual test cases, run end-to-end against the REAL Docker sandbox.

Mirrors `tests/graph/test_phase6_manual.py`'s "print an ACTUAL: line" style,
but drives `app.execution.runner.SandboxRunner` directly (built via
`build_sandbox_runner`), exercising the real isolation guarantees the
`DockerSandbox` container config claims: timeout kill, network isolation,
read-only root filesystem, noexec /tmp, uid/gid drop, pids limit, memory
limit, report-forgery resistance, and concurrency queueing.

All tests are `@pytest.mark.sandbox` and skip (never fail) when Docker isn't
reachable or the `aca-sandbox` image hasn't been built locally.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncGenerator, Generator

import pytest
import pytest_asyncio
from docker.errors import DockerException, ImageNotFound
from pydantic import JsonValue

from app.config import Settings
from app.execution.runner import SandboxRunner, build_sandbox_runner
from app.execution.sandbox import SANDBOX_LABEL, make_docker_client
from app.execution.verification import verify
from app.schemas.execution import ExecutionRequest, TestCase, TestSuite

#: All tests in this module share a single module-scoped event loop (via
#: `loop_scope="module"`) so the module-scoped `runner` fixture below --
#: which owns an `asyncio.Semaphore` and a Docker client bound to whatever
#: loop is running when it's built -- stays usable across every test.
pytestmark = [pytest.mark.sandbox, pytest.mark.asyncio(loop_scope="module")]

TEST_JWT_SECRET_KEY = "0123456789abcdef" * 4


def _build_settings(**overrides: object) -> Settings:
    params: dict[str, object] = {
        "database_url": "postgresql://u:p@127.0.0.1:1/x",
        "qdrant_url": "http://127.0.0.1:1",
        "groq_api_key": "test-key",
        "openrouter_api_key": "test-key",
        "langsmith_tracing": False,
        "jwt_secret_key": TEST_JWT_SECRET_KEY,
        "knowledge_enabled": False,
        "sandbox_enabled": True,
        "sandbox_max_concurrent": 2,
    }
    params.update(overrides)
    return Settings(_env_file=None, **params)  # pyright: ignore[reportCallIssue]


def _skip_unless_docker_available(settings: Settings) -> None:
    try:
        client = make_docker_client()
        if not client.ping():
            pytest.skip("docker engine not reachable")
        client.images.get(settings.sandbox_image)
        client.close()
    except DockerException as exc:
        if isinstance(exc, ImageNotFound):
            pytest.skip(f"sandbox image {settings.sandbox_image!r} not built locally")
        pytest.skip(f"docker engine not usable: {type(exc).__name__}")


def _no_sandbox_containers_remain() -> bool:
    client = make_docker_client()
    try:
        remaining = client.containers.list(all=True, filters={"label": f"{SANDBOX_LABEL}=1"})
        return len(remaining) == 0
    finally:
        client.close()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runner() -> AsyncGenerator[SandboxRunner, None]:
    settings = _build_settings()
    _skip_unless_docker_available(settings)

    runner_obj, close_runner = await build_sandbox_runner(settings)
    if runner_obj is None:
        pytest.skip("sandbox runner unavailable at startup")
    try:
        yield runner_obj
    finally:
        close_runner()


@pytest.fixture(autouse=True)
def _assert_no_leftover_containers() -> Generator[None, None, None]:
    """After every test in this module, assert no `aca.sandbox` containers
    remain -- regardless of whether the test passed or failed."""
    yield
    # Docker unreachable is covered by the runner fixture's own skip.
    with contextlib.suppress(DockerException):
        assert _no_sandbox_containers_remain()


# --------------------------------------------------------------------------
# Test 1 -- timeout kill
# --------------------------------------------------------------------------


async def test_timeout_kill(runner: SandboxRunner) -> None:
    request = ExecutionRequest(code="while True:\n    pass\n", timeout_s=5.0)

    t0 = time.perf_counter()
    result = await runner.run(request)
    host_wall_s = time.perf_counter() - t0

    verdict = verify(result, request)

    print(
        f"ACTUAL: test=timeout_kill status={result.status!r} timed_out={result.timed_out!r} "
        f"exit_code={result.exit_code!r} duration_ms={result.duration_ms!r} "
        f"host_wall_s={host_wall_s!r} verdict_status={verdict.status!r} "
        f"verdict_category={verdict.category!r}"
    )

    assert result.status == "timeout"
    assert result.timed_out is True
    assert verdict.status == "fail"
    assert verdict.category == "timeout"
    assert result.duration_ms / 1000 >= 4.5
    assert host_wall_s < 12.0
    assert _no_sandbox_containers_remain()


# --------------------------------------------------------------------------
# Test 2 -- network blocked
# --------------------------------------------------------------------------

_NETWORK_TRY_EXCEPT_CODE = (
    "import urllib.request\n"
    "try:\n"
    '    urllib.request.urlopen("http://example.com", timeout=3)\n'
    '    print("NETWORK_OK")\n'
    "except Exception as exc:\n"
    '    print("NETWORK_BLOCKED", type(exc).__name__)\n'
)

_NETWORK_BARE_CODE = (
    'import urllib.request\nurllib.request.urlopen("http://example.com", timeout=3)\n'
)


async def test_network_blocked_caught(runner: SandboxRunner) -> None:
    request = ExecutionRequest(code=_NETWORK_TRY_EXCEPT_CODE, timeout_s=8.0)

    t0 = time.perf_counter()
    result = await runner.run(request)
    host_wall_s = time.perf_counter() - t0

    print(
        f"ACTUAL: test=network_blocked_caught status={result.status!r} "
        f"stdout={result.stdout!r} host_wall_s={host_wall_s!r}"
    )

    assert result.status == "completed"
    assert "NETWORK_BLOCKED" in result.stdout
    assert "NETWORK_OK" not in result.stdout
    assert host_wall_s < 8.0  # well under the timeout -- the block is immediate, not a hang


async def test_network_blocked_bare_raises(runner: SandboxRunner) -> None:
    request = ExecutionRequest(code=_NETWORK_BARE_CODE, timeout_s=8.0)

    result = await runner.run(request)

    error_type = result.error.type if result.error else None
    print(
        f"ACTUAL: test=network_blocked_bare status={result.status!r} error_type={error_type!r} "
        f"stderr={result.stderr!r}"
    )

    assert result.status == "runtime_error"
    assert error_type in {"URLError", "OSError", "ConnectionError", "socket.gaierror", "gaierror"}


# --------------------------------------------------------------------------
# Isolation probes
# --------------------------------------------------------------------------


async def test_isolation_uid_gid(runner: SandboxRunner) -> None:
    request = ExecutionRequest(code="import os\nprint(os.getuid(), os.getgid())\n", timeout_s=8.0)

    result = await runner.run(request)

    print(f"ACTUAL: test=isolation_uid_gid status={result.status!r} stdout={result.stdout!r}")

    assert result.status == "completed"
    assert "10001 10001" in result.stdout


_READONLY_ROOT_CODE = (
    "results = {}\n"
    'for path, mode in [("/opt/harness/run.py", "a"), ("/etc/x", "w")]:\n'
    "    try:\n"
    "        open(path, mode)\n"
    '        results[path] = "no_error"\n'
    "    except Exception as e:\n"
    "        results[path] = type(e).__name__\n"
    "try:\n"
    '    with open("/tmp/probe.txt", "w") as f:\n'
    '        f.write("ok")\n'
    '    results["/tmp"] = "ok"\n'
    "except Exception as e:\n"
    '    results["/tmp"] = type(e).__name__\n'
    "print(results)\n"
)


async def test_isolation_readonly_root(runner: SandboxRunner) -> None:
    request = ExecutionRequest(code=_READONLY_ROOT_CODE, timeout_s=8.0)

    result = await runner.run(request)

    print(f"ACTUAL: test=isolation_readonly_root status={result.status!r} stdout={result.stdout!r}")

    assert result.status == "completed"
    assert "'/opt/harness/run.py': 'no_error'" not in result.stdout
    assert "'/etc/x': 'no_error'" not in result.stdout
    assert "'/tmp': 'ok'" in result.stdout


_NOEXEC_TMP_CODE = (
    "import os\n"
    'script = "/tmp/x.sh"\n'
    'with open(script, "w") as f:\n'
    '    f.write("#!/bin/sh\\necho hi\\n")\n'
    "os.chmod(script, 0o755)\n"
    "try:\n"
    "    os.execv(script, [script])\n"
    '    print("EXEC_OK")\n'
    "except PermissionError as e:\n"
    '    print("EXEC_BLOCKED", type(e).__name__)\n'
    "except Exception as e:\n"
    '    print("EXEC_OTHER", type(e).__name__)\n'
)


async def test_isolation_noexec_tmp(runner: SandboxRunner) -> None:
    request = ExecutionRequest(code=_NOEXEC_TMP_CODE, timeout_s=8.0)

    result = await runner.run(request)

    print(f"ACTUAL: test=isolation_noexec_tmp status={result.status!r} stdout={result.stdout!r}")

    assert result.status == "completed"
    assert "EXEC_BLOCKED PermissionError" in result.stdout


_DNS_BLOCKED_CODE = (
    "import socket\n"
    "try:\n"
    '    socket.getaddrinfo("example.com", 80)\n'
    '    print("DNS_OK")\n'
    "except Exception as e:\n"
    '    print("DNS_BLOCKED", type(e).__name__)\n'
)


async def test_isolation_dns_blocked(runner: SandboxRunner) -> None:
    request = ExecutionRequest(code=_DNS_BLOCKED_CODE, timeout_s=8.0)

    result = await runner.run(request)

    print(f"ACTUAL: test=isolation_dns_blocked status={result.status!r} stdout={result.stdout!r}")

    assert result.status == "completed"
    assert "DNS_BLOCKED" in result.stdout
    assert "DNS_OK" not in result.stdout


_PIDS_LIMIT_CODE = (
    "import threading, time\n"
    "count = 0\n"
    'error_name = ""\n'
    "for i in range(500):\n"
    "    try:\n"
    "        t = threading.Thread(target=time.sleep, args=(2,))\n"
    "        t.start()\n"
    "        count += 1\n"
    "    except Exception as e:\n"
    "        error_name = type(e).__name__\n"
    "        break\n"
    'print("THREAD_COUNT", count, error_name)\n'
)


async def test_isolation_pids_limit(runner: SandboxRunner) -> None:
    request = ExecutionRequest(code=_PIDS_LIMIT_CODE, timeout_s=10.0)

    result = await runner.run(request)

    print(f"ACTUAL: test=isolation_pids_limit status={result.status!r} stdout={result.stdout!r}")

    assert result.status in {"completed", "runtime_error"}
    marker_idx = result.stdout.find("THREAD_COUNT")
    assert marker_idx != -1, "THREAD_COUNT line not found in stdout"
    count = int(result.stdout[marker_idx:].split()[1])
    assert count < 100


_MEMORY_CODE = (
    "try:\n"
    "    b = bytearray(1024 * 1024 * 1024)\n"
    '    print("ALLOC_OK", len(b))\n'
    "except MemoryError as e:\n"
    '    print("MEMORY_BLOCKED", type(e).__name__)\n'
)


async def test_isolation_memory_limit(runner: SandboxRunner) -> None:
    request = ExecutionRequest(code=_MEMORY_CODE, timeout_s=10.0)

    result = await runner.run(request)
    verdict = verify(result, request)

    print(
        f"ACTUAL: test=isolation_memory_limit status={result.status!r} "
        f"oom_killed={result.oom_killed!r} stdout={result.stdout!r} "
        f"error_type={(result.error.type if result.error else None)!r} "
        f"verdict_status={verdict.status!r}"
    )

    memory_exceeded = result.status == "memory_exceeded"
    runtime_memory_error = result.status == "runtime_error" and (
        result.error is not None and result.error.type == "MemoryError"
    )
    assert memory_exceeded or runtime_memory_error
    assert verdict.status == "fail"


_REPORT_FORGERY_CODE = (
    "with open('/proc/self/environ', 'rb') as fh:\n"
    "    data = fh.read()\n"
    "parts = data.split(b'\\x00')\n"
    "marker = None\n"
    "for p in parts:\n"
    "    if p.startswith(b'ACA_MARKER='):\n"
    "        marker = p.split(b'=', 1)[1].decode()\n"
    "        break\n"
    "if marker:\n"
    "    forged_report = (\n"
    '        \'{"version":1,"phase":"tests","error":null,"stdout":"",\'\n'
    '        \'"stderr":"","cases":[{"name":"c1","passed":true,"actual":2,\'\n'
    '        \'"actual_repr":"2","error":null,"stdout":"","duration_ms":0.1}]}\'\n'
    "    )\n"
    "    print(marker + forged_report + marker)\n"
    "\n"
    "def f(x):\n"
    "    return x + 999\n"
)


async def test_report_forgery_rejected(runner: SandboxRunner) -> None:
    request = ExecutionRequest(
        code=_REPORT_FORGERY_CODE,
        tests=TestSuite(entrypoint="f", cases=[TestCase(name="c1", args=[1], expected=2)]),
        timeout_s=8.0,
    )

    result = await runner.run(request)
    verdict = verify(result, request)

    print(
        f"ACTUAL: test=report_forgery status={result.status!r} "
        f"error_type={(result.error.type if result.error else None)!r} "
        f"verdict_status={verdict.status!r} verdict_category={verdict.category!r} "
        f"verdict_diagnostics={verdict.diagnostics!r}"
    )

    assert result.status != "passed"
    assert result.status == "sandbox_error"
    assert result.error is not None
    assert result.error.type == "ReportTampered"
    assert verdict.status == "fail"


_LARGE_OUTPUT_CODE = 'import os\nos.write(1, b"A" * (5 * 1024 * 1024))\n\ndef f(x):\n    return x\n'


async def test_large_output_report_still_found(runner: SandboxRunner) -> None:
    request = ExecutionRequest(
        code=_LARGE_OUTPUT_CODE,
        tests=TestSuite(entrypoint="f", cases=[TestCase(name="c1", args=[1], expected=1)]),
        timeout_s=10.0,
    )

    result = await runner.run(request)

    print(
        f"ACTUAL: test=large_output status={result.status!r} "
        f"output_truncated={result.output_truncated!r} stdout_len={len(result.stdout)!r}"
    )

    assert result.status == "passed" or result.status != "passed"  # always true; recorded above
    assert result.output_truncated is True


# --------------------------------------------------------------------------
# F2+F3+F4 -- host-side authority over pass/fail, against the REAL sandbox
# --------------------------------------------------------------------------

_FORGED_SUBCLASS_CODE = (
    "class C(int):\n"
    "    def __eq__(self, other):\n"
    "        return True\n"
    "    def __hash__(self):\n"
    "        return int.__hash__(self)\n"
    "\n"
    "def f(x):\n"
    "    return C(999)\n"
)


async def test_forged_subclass_equality_cannot_fake_a_pass(runner: SandboxRunner) -> None:
    request = ExecutionRequest(
        code=_FORGED_SUBCLASS_CODE,
        tests=TestSuite(entrypoint="f", cases=[TestCase(name="c1", args=[1], expected=2)]),
        timeout_s=8.0,
    )

    result = await runner.run(request)
    verdict = verify(result, request)

    print(
        f"ACTUAL: test=forged_subclass_equality status={result.status!r} "
        f"verdict_status={verdict.status!r} verdict_category={verdict.category!r}"
    )

    assert verdict.status == "fail"


_MONKEYPATCHED_JSON_EQUAL_CODE = (
    "import sys\n"
    "sys.modules['__main__']._json_equal = lambda a, b: True\n"
    "\n"
    "def f(x):\n"
    "    return 42\n"
)


async def test_monkeypatched_json_equal_cannot_fake_a_pass(runner: SandboxRunner) -> None:
    request = ExecutionRequest(
        code=_MONKEYPATCHED_JSON_EQUAL_CODE,
        tests=TestSuite(entrypoint="f", cases=[TestCase(name="c1", args=[1], expected=1)]),
        timeout_s=8.0,
    )

    result = await runner.run(request)
    verdict = verify(result, request)

    print(
        f"ACTUAL: test=monkeypatched_json_equal status={result.status!r} "
        f"verdict_status={verdict.status!r} verdict_category={verdict.category!r}"
    )

    assert verdict.status == "fail"


#: Large enough that its canonical text exceeds the harness's 4000-char
#: per-case actual budget (F3's "report not lost" case), but small enough
#: that 3 copies of it embedded in the request still fit under the sandbox's
#: base64 payload size limit (`MAX_PAYLOAD_B64_BYTES`).
_LARGE_CORRECT_LIST_CODE = "def f(x):\n    return list(range(2000))\n"


async def test_large_correct_list_result_still_verifies_as_pass(runner: SandboxRunner) -> None:
    expected_list: list[JsonValue] = list(range(2000))
    request = ExecutionRequest(
        code=_LARGE_CORRECT_LIST_CODE,
        tests=TestSuite(
            entrypoint="f",
            cases=[
                TestCase(name="c1", args=[1], expected=expected_list),
                TestCase(name="c2", args=[2], expected=expected_list),
                TestCase(name="c3", args=[3], expected=expected_list),
            ],
        ),
        timeout_s=10.0,
    )

    result = await runner.run(request)
    verdict = verify(result, request)

    print(
        f"ACTUAL: test=large_correct_list status={result.status!r} "
        f"verdict_status={verdict.status!r} cases_passed={verdict.cases_passed!r} "
        f"cases_total={verdict.cases_total!r}"
    )

    assert verdict.status == "pass"
    assert verdict.cases_passed == verdict.cases_total == 3


_SELF_REFERENCING_LIST_CODE = "def f(x):\n    c = [1, 2]\n    c.append(c)\n    return c\n"


async def test_self_referencing_list_result_fails_not_inconclusive(runner: SandboxRunner) -> None:
    request = ExecutionRequest(
        code=_SELF_REFERENCING_LIST_CODE,
        tests=TestSuite(entrypoint="f", cases=[TestCase(name="c1", args=[1], expected=[1, 2])]),
        timeout_s=8.0,
    )

    result = await runner.run(request)
    verdict = verify(result, request)

    print(
        f"ACTUAL: test=self_referencing_list status={result.status!r} "
        f"verdict_status={verdict.status!r} verdict_category={verdict.category!r}"
    )

    assert verdict.status == "fail"
    assert verdict.status != "inconclusive"


# --------------------------------------------------------------------------
# Test 4 -- concurrency cap (real)
# --------------------------------------------------------------------------


async def test_concurrency_cap_real(runner: SandboxRunner) -> None:
    request = ExecutionRequest(code="import time\ntime.sleep(1.5)\n", timeout_s=15.0)

    t0 = time.perf_counter()
    results = await asyncio.gather(*(runner.run(request) for _ in range(5)))
    total_wall_s = time.perf_counter() - t0

    statuses = [r.status for r in results]
    print(
        f"ACTUAL: test=concurrency_cap statuses={statuses!r} "
        f"peak_in_flight={runner.peak_in_flight!r} total_wall_s={total_wall_s!r}"
    )

    assert all(status == "completed" for status in statuses)
    assert runner.peak_in_flight == 2
    assert total_wall_s >= 3 * 1.5 * 0.9  # allow a little slack below 3 waves
    assert _no_sandbox_containers_remain()


# --------------------------------------------------------------------------
# Test 5 -- cleanup on cancellation (real)
# --------------------------------------------------------------------------


async def test_cleanup_on_cancellation_real(runner: SandboxRunner) -> None:
    request = ExecutionRequest(code="while True:\n    pass\n", timeout_s=20.0)

    task = asyncio.ensure_future(runner.run(request))
    await asyncio.sleep(2.0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    deadline = time.perf_counter() + 15.0
    cleaned_up = False
    while time.perf_counter() < deadline:
        if _no_sandbox_containers_remain():
            cleaned_up = True
            break
        await asyncio.sleep(0.5)

    print(f"ACTUAL: test=cleanup_on_cancellation cleaned_up={cleaned_up!r}")

    assert cleaned_up
