"""Language-agnostic code-execution entrypoint.

`SandboxRunner` is the only thing the rest of the app talks to for running
untrusted code (`app.execution.base.CodeRunner`): it encodes an
`ExecutionRequest` into the sandbox wire payload, dispatches it to the
per-language `SandboxBackend` (today, only `app.execution.sandbox.DockerSandbox`
for Python), bounds concurrency with a semaphore-backed queue, and maps the
raw process outcome (`RawRun`) plus the harness's marker-delimited report
line back into a typed `ExecutionResult` via `interpret` -- the pure function
that is the core of this module and is exhaustively table-tested on its own.

`interpret` never trusts a report it can't unambiguously locate: the harness
report line is framed by a random-per-run marker token, and anything other
than exactly two occurrences of that marker with nothing but whitespace after
the second one is treated as `ReportTampered` rather than parsed.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import secrets
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Final

from docker.client import DockerClient
from pydantic import ValidationError

from app.config import Settings
from app.execution.base import RawRun, SandboxBackend, truncate_text
from app.execution.sandbox import DockerSandbox, SandboxError, SandboxLimits, make_docker_client
from app.schemas.execution import (
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    HarnessError,
    HarnessReport,
    Language,
)

logger = logging.getLogger(__name__)

#: `docker run -e` env vars are limited to roughly 128 KiB on Linux; leave
#: headroom for the `ACA_PAYLOAD=` prefix and the sibling `ACA_MARKER` var.
MAX_PAYLOAD_B64_BYTES: Final = 120_000
#: Cap on `ExecutionResult.stdout`/`.stderr` so a runaway submission can't
#: balloon the response body; the harness already caps its own captured
#: fields, this bounds the combined (pre-marker raw + harness-reported) text.
MAX_RESULT_OUTPUT_CHARS: Final = 16_000

_NO_REPORT_MESSAGE = "process exited without a harness report"
_REPORT_TAMPERED_MESSAGE = (
    "the harness report marker appeared an unexpected number of times, "
    "or trailing output followed the report"
)
_MALFORMED_REPORT_MESSAGE = "the harness report could not be parsed"


# --------------------------------------------------------------------------
# Payload encoding
# --------------------------------------------------------------------------


def encode_payload(request: ExecutionRequest) -> str:
    """Build the base64 `ACA_PAYLOAD` envelope the harness expects.

    Only the fields the harness's wire protocol defines are included (see
    `docker/harness/run.py`'s module docstring): `version`, `language`,
    `code`, and `tests` (`null`, or `entrypoint` + `cases[name,args,kwargs,
    expected]`).
    """
    tests_payload = request.tests.model_dump(mode="json") if request.tests is not None else None
    envelope = {
        "version": 1,
        "language": request.language,
        "code": request.code,
        "tests": tests_payload,
    }
    raw_bytes = json.dumps(envelope, ensure_ascii=True).encode("utf-8")
    return base64.b64encode(raw_bytes).decode("ascii")


# --------------------------------------------------------------------------
# Raw-run -> ExecutionResult interpretation (pure)
# --------------------------------------------------------------------------


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _raw_result(
    raw: RawRun, status: ExecutionStatus, *, error: HarnessError | None = None
) -> ExecutionResult:
    """Build a result straight from the process outcome, no report parsed."""
    stdout_text, stdout_truncated = truncate_text(_decode(raw.stdout), MAX_RESULT_OUTPUT_CHARS)
    stderr_text, stderr_truncated = truncate_text(_decode(raw.stderr), MAX_RESULT_OUTPUT_CHARS)
    return ExecutionResult(
        status=status,
        exit_code=raw.exit_code,
        duration_ms=raw.duration_ms,
        timed_out=raw.timed_out,
        oom_killed=raw.oom_killed,
        stdout=stdout_text,
        stderr=stderr_text,
        error=error,
        output_truncated=raw.output_truncated or stdout_truncated or stderr_truncated,
    )


def _status_for_phase(report: HarnessReport) -> ExecutionStatus:
    if report.phase == "protocol":
        return "sandbox_error"
    if report.phase == "compile":
        return "compile_error"
    if report.phase == "load":
        return "runtime_error"
    if report.phase == "entrypoint":
        return "entrypoint_missing"
    if report.phase == "script":
        return "completed"
    # report.phase == "tests"
    return "passed" if report.cases and all(case.passed for case in report.cases) else "failed"


def interpret(raw: RawRun, marker: str) -> ExecutionResult:
    """Map a `RawRun` (+ the marker that framed this run's report) to an
    `ExecutionResult`. Pure -- no I/O, no logging, safe to table-test."""
    if raw.timed_out:
        return _raw_result(raw, "timeout")
    if raw.oom_killed:
        return _raw_result(raw, "memory_exceeded")

    stdout_text = _decode(raw.stdout)
    occurrences = stdout_text.count(marker)

    if occurrences == 0:
        return _raw_result(
            raw, "runtime_error", error=HarnessError(type="NoReport", message=_NO_REPORT_MESSAGE)
        )

    first_idx = stdout_text.find(marker)
    second_idx = stdout_text.find(marker, first_idx + len(marker))
    trailing = stdout_text[second_idx + len(marker) :]
    well_framed = occurrences == 2 and second_idx != -1 and trailing.strip() == ""
    if not well_framed:
        # Never trust a report we can't unambiguously locate -- this is the
        # shape user code forging a fake report line would produce.
        return _raw_result(
            raw,
            "sandbox_error",
            error=HarnessError(type="ReportTampered", message=_REPORT_TAMPERED_MESSAGE),
        )

    pre_marker_text = stdout_text[:first_idx]
    report_text = stdout_text[first_idx + len(marker) : second_idx]

    try:
        report = HarnessReport.model_validate_json(report_text)
    except ValidationError:
        return _raw_result(
            raw,
            "sandbox_error",
            error=HarnessError(type="MalformedReport", message=_MALFORMED_REPORT_MESSAGE),
        )

    stdout_combined, stdout_truncated = truncate_text(
        pre_marker_text + report.stdout, MAX_RESULT_OUTPUT_CHARS
    )
    stderr_combined, stderr_truncated = truncate_text(
        report.stderr + _decode(raw.stderr), MAX_RESULT_OUTPUT_CHARS
    )

    return ExecutionResult(
        status=_status_for_phase(report),
        exit_code=raw.exit_code,
        duration_ms=raw.duration_ms,
        timed_out=raw.timed_out,
        oom_killed=raw.oom_killed,
        stdout=stdout_combined,
        stderr=stderr_combined,
        phase=report.phase,
        error=report.error,
        cases=report.cases,
        output_truncated=raw.output_truncated or stdout_truncated or stderr_truncated,
    )


# --------------------------------------------------------------------------
# SandboxRunner
# --------------------------------------------------------------------------


class SandboxRunner:
    """Dispatches `ExecutionRequest`s to a per-language `SandboxBackend`,
    bounding concurrency with a semaphore-backed queue.

    Runs beyond `max_concurrent` wait for a free slot (queued) rather than
    being rejected outright; only a queue wait exceeding `queue_timeout_s`
    is rejected. Logs class names / status only -- never code, env, or
    output.
    """

    def __init__(
        self,
        backends: Mapping[Language, SandboxBackend],
        *,
        max_concurrent: int,
        queue_timeout_s: float,
    ) -> None:
        self._backends = backends
        self._queue_timeout_s = queue_timeout_s
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._in_flight = 0
        self._peak_in_flight = 0

    @property
    def in_flight(self) -> int:
        return self._in_flight

    @property
    def peak_in_flight(self) -> int:
        return self._peak_in_flight

    async def run(self, request: ExecutionRequest) -> ExecutionResult:
        backend = self._backends.get(request.language)
        if backend is None:
            logger.info("sandbox run rejected: unsupported language")
            return ExecutionResult(
                status="rejected",
                language=request.language,
                error=HarnessError(
                    type="UnsupportedLanguage",
                    message=f"no sandbox backend is registered for language {request.language!r}",
                ),
            )

        payload_b64 = encode_payload(request)
        if len(payload_b64) > MAX_PAYLOAD_B64_BYTES:
            logger.info("sandbox run rejected: payload too large")
            return ExecutionResult(
                status="rejected",
                language=request.language,
                error=HarnessError(
                    type="PayloadTooLarge",
                    message="the encoded submission exceeds the sandbox payload size limit",
                ),
            )

        try:
            async with asyncio.timeout(self._queue_timeout_s):
                await self._semaphore.acquire()
        except TimeoutError:
            logger.info("sandbox run rejected: queue wait exceeded timeout")
            return ExecutionResult(
                status="sandbox_error",
                language=request.language,
                error=HarnessError(
                    type="SandboxBusy", message="the sandbox queue wait exceeded the timeout"
                ),
            )

        self._in_flight += 1
        self._peak_in_flight = max(self._peak_in_flight, self._in_flight)
        try:
            marker = secrets.token_hex(16)
            env = {"ACA_PAYLOAD": payload_b64, "ACA_MARKER": marker}
            try:
                raw = await backend.run(env, request.timeout_s)
            except SandboxError as exc:
                logger.warning("sandbox backend unavailable: %s", type(exc).__name__)
                return ExecutionResult(
                    status="sandbox_error",
                    language=request.language,
                    error=HarnessError(
                        type="SandboxUnavailable",
                        message="the sandbox backend failed to run this submission",
                    ),
                )
            return interpret(raw, marker)
        finally:
            self._in_flight -= 1
            self._semaphore.release()


# --------------------------------------------------------------------------
# Startup wiring
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _NoopCloser:
    def __call__(self) -> None:
        return None


def _close_quietly(client: DockerClient | None) -> None:
    """Best-effort close of a partially-initialized Docker client.

    Called when the client was created but a later startup probe (ping,
    image check) failed -- swallows any error so a broken close can't itself
    crash startup.
    """
    if client is None:
        return
    try:
        client.close()
    except Exception as exc:  # best-effort cleanup only
        logger.warning(
            "failed to close docker client after startup probe failure: %s", type(exc).__name__
        )


async def build_sandbox_runner(
    settings: Settings,
) -> tuple[SandboxRunner | None, Callable[[], None]]:
    """Build a `SandboxRunner` at startup, fail-soft.

    Returns `(None, noop_close)` when the sandbox is disabled via settings,
    or when the Docker daemon / sandbox image isn't reachable -- the app
    must still start (degraded) rather than fail to boot because Docker
    Desktop isn't running.
    """
    if not settings.sandbox_enabled:
        return None, _NoopCloser()

    loop = asyncio.get_running_loop()
    client: DockerClient | None = None
    try:
        async with asyncio.timeout(settings.sandbox_startup_timeout_s):
            probed: DockerClient = await loop.run_in_executor(None, make_docker_client)
            client = probed
            await loop.run_in_executor(None, probed.ping)
            await loop.run_in_executor(None, lambda: probed.images.get(settings.sandbox_image))
    except TimeoutError:
        logger.warning("sandbox unavailable at startup: startup probe timed out")
        _close_quietly(client)
        return None, _NoopCloser()
    except Exception as exc:
        # Deliberately broad: on Windows a stopped Docker Desktop can surface
        # through requests/pywin32 machinery as errors outside docker-py's own
        # exception hierarchy. The app must still start (degraded) either way.
        logger.warning("sandbox unavailable at startup: %s", type(exc).__name__)
        _close_quietly(client)
        return None, _NoopCloser()

    assert client is not None  # the try block above always assigns it before succeeding

    executor = ThreadPoolExecutor(
        max_workers=settings.sandbox_max_concurrent * 3, thread_name_prefix="sandbox"
    )
    limits = SandboxLimits(image=settings.sandbox_image, memory_mb=settings.sandbox_memory_mb)
    sandbox = DockerSandbox(client, limits, executor)
    try:
        await sandbox.sweep_orphans()
    except Exception as exc:  # startup must never crash because orphan sweep failed
        logger.warning("sandbox orphan sweep failed at startup: %s", type(exc).__name__)

    runner = SandboxRunner(
        backends={"python": sandbox},
        max_concurrent=settings.sandbox_max_concurrent,
        queue_timeout_s=settings.sandbox_queue_timeout_s,
    )

    def _close() -> None:
        executor.shutdown(wait=False)
        client.close()

    return runner, _close
