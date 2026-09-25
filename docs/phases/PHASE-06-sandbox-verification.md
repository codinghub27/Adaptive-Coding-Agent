# PHASE-06 — Code Execution Sandbox & Verification

## Status
Done (2026-09-25), commit `967b880`

## Goal
Safely run code and establish correctness by **execution**, not by the model's
opinion. This is the production-engineering core of the project.

## Scope
- `app/execution/sandbox.py` — Docker-based runner: spin a locked-down container,
  copy code + test harness, capture `stdout`, `stderr`, `exit_code`, timing.
- `app/execution/runner.py` — high-level API: `run_code(code, tests, lang)` →
  typed `ExecutionResult` (passed/failed cases, first failing case, traces).
- `app/execution/verification.py` — Verification Agent: compare execution result
  against expected behaviour; produce a typed verdict consumed by the graph.
- `app/graph/` — add `execute_code` + `verify` nodes with a PASS/FAIL branch.

Sandbox hard requirements:
- No network (`--network none`).
- Read-only FS except a scratch tmpdir; drop capabilities; non-root user.
- CPU + memory limits; pids limit; wall-clock timeout (kill on overrun).
- No host mounts of anything sensitive; ephemeral container per run.

## Out of Scope
- Which code gets run (agents decide, Phase 07).
- Multi-language support beyond Python for now (design for extension).
- Frontend, evaluation.
- Future phases.

## Current Implementation
Inspected 2026-09-24 (HEAD `0807431`; Phases 01–05 + JWT auth Done). Via
token-savior symbol lookups, docker MCP, context7 (`/docker/docker-py`) and the
venv.

- **Execution** — `app/execution/__init__.py` exists and is empty. There is no
  sandbox, runner, verification, schema or Dockerfile. `docker/` holds only
  `.gitkeep`.
- **Docker SDK** — `docker` (docker-py) is **not installed** and not in
  `requirements.txt`. `import docker` currently resolves to the repo's
  `docker/` folder as a *namespace package* (it has no `__init__.py`). A real
  installed regular package takes precedence over a namespace portion, but this
  must be checked after install.
- **Docker engine** — server 29.5.3 (Docker Desktop, Windows host) is running.
  The compose `postgres` (5433) and `qdrant` (v1.19.0, 6333) are healthy. No
  `python` images are pulled locally. The docker MCP (`build_image`,
  `run_container`, `list_containers`, …) works. It has **no inspect tool** and
  `run_container` takes no isolation flags, so checking HostConfig flags needs
  `docker inspect` (Bash, `ask` permission).
- **Graph (Phase 04/05)** — `AgentState` (`app/graph/state.py:88`, frozen,
  `extra="forbid"`) reserves `execution_result: dict[str, JsonValue] | None`.
  Nothing reads or writes it. `AgentStateUpdate` mirrors it. `GraphContext`
  (`llm`, `session`, `user_id`, `conversation_id`, `retriever`,
  `knowledge_top_k`) has no sandbox/runner. Nodes register in
  `build.py::NODE_FUNCTIONS`, which must match `nodes.py::FALLBACKS` (checked in
  `build_graph`). Every node is wrapped by `safe_node`. Edges: `… plan_teaching
  -> retrieve_knowledge -> route -?-> {dsa_agent|debug_agent|explain_agent|
  clarify} -> final_response -> update_learner_model -> END`.
  `RECURSION_LIMIT = 20`. `run_graph(raw, *, llm, session, user_id,
  conversation_id, max_llm_calls, retriever, knowledge_top_k)` is called only
  by `app/graph/api.py`.
- **Agents** — stubs return `AgentOutcome{text, topic, pattern, solved=None,
  …}`. No agent produces code to run (Phase 07).
- **Settings** (`app/config.py::Settings`) — no sandbox/execution settings.
  `tests/conftest.py::make_settings` builds test settings.
- **Tests** — markers `integration`, `db`, `live`; `asyncio_mode = "auto"`.
  No `tests/execution/`. Baseline: 893 passed, 2 skipped, 2 pre-existing auth
  failures (Phase 05 Known Issues).

## Planned Changes
1. Build a minimal sandbox image (pinned Python, no extra network tools).
2. Implement the container lifecycle wrapper with all limits + timeout + cleanup.
3. Define `ExecutionResult` and a test-harness format (cases → expected).
4. Implement the Verification Agent (structured verdict: correct? failing case?
   category of failure?).
5. Add `execute_code` + `verify` graph nodes and the PASS/FAIL edge.
6. Tests: infinite loop is killed by timeout; network attempt fails; a known-bad
   solution is reported failing with the right first-failing case.

## Files Expected
- Create: `app/execution/{__init__,sandbox,runner,verification}.py`,
  `app/schemas/execution.py`, `docker/sandbox.Dockerfile`,
  `tests/execution/test_sandbox.py`, `tests/execution/test_verification.py`
- Modify: `app/graph/{nodes,build}.py`
- Delete: None unless explicitly approved

**Actually created / modified (additions beyond the list are recorded here):**
- Created: `app/execution/{base,sandbox,runner,verification}.py`,
  `app/schemas/execution.py`, `docker/sandbox.Dockerfile`,
  `docker/.dockerignore`, `docker/harness/run.py`,
  `tests/execution/{__init__,fakes,test_sandbox_lifecycle,test_sandbox_docker,test_no_host_exec,test_base_import_weight,test_runner,test_runner_interpret,test_verification,test_phase6_manual}.py`,
  `tests/graph/{test_execute_verify_nodes,test_phase6_manual}.py`,
  `tests/schemas/test_execution.py`.
- Modified: `app/graph/{state,nodes,routing,build,api}.py`, `app/config.py`,
  `app/main.py`, `app/schemas/__init__.py`, `requirements.txt` (`docker`,
  `types-docker`), `pyproject.toml` (`sandbox` marker), `tests/conftest.py`,
  `tests/graph/{test_build,test_nodes,test_phase4_manual,test_state_import_weight}.py`.
- Additions beyond the plan:
  - `app/execution/base.py`: lightweight `CodeRunner` / `SandboxBackend`
    Protocols + `RawRun`, so `app.graph.state` never imports `docker`
    (the same lesson as Phase 05's `app/knowledge/base.py`).
  - `docker/harness/run.py`: the in-container test harness (the only code
    that `exec`s user code, and only inside the container).
  - `app/graph/routing.py`: `VerifyKey` / `verify_after` / `VERIFY_NODES`.
  - `app/graph/api.py` + `app/main.py`: runner built at startup (fail-soft)
    and passed to `run_graph`; `ChatResponse.verification` (additive).
  - Tests split by module, plus the AST guard `test_no_host_exec.py`.

## Architecture Decisions
- One ephemeral container per run — no reuse — to avoid state leakage.
- Verification consumes only execution facts; the LLM never overrides a failing
  test into a "pass".
- Design `run_code` language-agnostic even though only Python ships now.
- **Docker Engine API only (docker-py 7.2.0).** No `subprocess`/`docker`
  CLI on the host. An AST guard test fails the build if `app/execution/` or
  `app/graph/nodes.py` ever imports `subprocess`/`pty` or calls
  `exec`/`eval`/`compile`/`os.system`/`os.exec*`/`os.spawn*`/`os.popen`.
- **Code transport = base64 env var (owner decision 1a).** The runner passes
  only `ACA_PAYLOAD` (base64 JSON: version, language, code, tests) and
  `ACA_MARKER` (per-run `secrets.token_hex(16)`). There are no mounts and no
  volumes. The encoded payload is capped at 120,000 bytes (Linux allows 128
  KiB per env string); larger payloads are `rejected` / `PayloadTooLarge`
  before any container is created.
- **Harness in the image** (`/opt/harness/run.py`, root-owned 0444 in a 0555
  dir, `python -I -B`). It reads and deletes the env vars, `dup`s fd 1, compiles
  the code as `solution.py` and runs it in a fresh module (`__main__` in script
  mode, `solution` in tests mode). It calls the entrypoint once per case with
  deep-copied args and per-case stdout capture, and compares with strict JSON
  equality (bool ≠ int, 2 == 2.0, tuples → lists). It writes exactly one
  `MARKER{json}MARKER` line to the saved fd, then calls `os._exit(0)`, so
  `atexit` handlers and threads cannot write after the report.
- **Report trust (owner decision 2).** `interpret()` accepts a report only
  when the marker occurs exactly twice and only whitespace follows it.
  Anything else is `sandbox_error` / `ReportTampered`, and the verdict is
  **fail**. No marker → `runtime_error` / `NoReport`. Timeout and OOM take
  precedence over any report.
- **The host decides pass/fail, not the harness** (after code review). The
  harness's `passed` flag is for display only. For each case the harness
  reports `actual_sha256 = sha256(canonical(actual))`, where canonical = sorted
  keys, fixed separators, integral floats → int, bool kept distinct. The
  verifier recomputes `sha256(canonical(expected))` on the host from the
  request. A case passes only with no error and matching digests. The
  reported case names must equal the requested names exactly (same order, no
  duplicates), else fail / `case_set_mismatch`. Without a request there is no
  pass (inconclusive). The harness accepts only exact builtin result types, so
  a subclass with a rigged `__eq__` fails. The host and harness `canonical()`
  are kept identical by a parity test. `actual` is included in the report only
  up to 4,000 canonical chars, so a report stays under ~560 KB and always fits
  the 1 MiB stdout tail.
- **Deadline enforced on the host.** `container.wait()` runs in a dedicated
  thread pool under `asyncio.wait_for(timeout_s)`. On overrun the container
  is SIGKILLed, then a bounded wait (10s) follows. Removal
  (`remove(force=True, v=True)`) sits in `finally` under `asyncio.shield`, so
  it also runs on timeout, error and cancellation. A startup
  `sweep_orphans()` removes any `aca.sandbox=1` leftovers.
- **Concurrency cap.** `SandboxRunner` holds an `asyncio.Semaphore`
  (`sandbox_max_concurrent=4`). Runs beyond the cap wait in a queue; only a
  queue wait longer than `sandbox_queue_timeout_s` (30s) gives `sandbox_error` /
  `SandboxBusy`.
- **Language-agnostic.** `SandboxRunner` takes a `Mapping[Language,
  SandboxBackend]`. Only `"python"` is registered; any other language →
  `rejected` / `UnsupportedLanguage`.
- **Verifier is deterministic (no LLM).** `verify(result, request) ->
  Verdict`. In the tests phase the verdict comes from the cases, never from
  the status string. Pass requires ≥ 1 case, all passed, and a case count
  equal to the request's. Infra errors → `inconclusive` (never pass, never
  blamed on the code). Categories: wrong_answer, runtime_error, timeout,
  memory_limit, syntax_error, missing_entrypoint, no_tests, sandbox_error,
  rejected. Wrong-answer diagnostics: `off_by_one_suspected`,
  `empty_result`, `wrong_type`, `wrong_length`, `order_mismatch`.
- **Graph (owner decision 3).** `{dsa,debug,explain}_agent → execute_code →
  verify -?(verify_after)-> final_response`; `clarify → final_response`
  directly. All four `VerifyKey`s (pass/fail/inconclusive/skipped) map to
  `final_response` in Phase 06; Phase 07 retargets `fail` to the debugger
  loop. `route` / `route_after` / `ROUTE_NODES` are unchanged. Both new nodes
  skip when `state.execution_request` is `None` (true for every turn until
  Phase 07). Their `safe_node` fallbacks give `sandbox_error` → `inconclusive`,
  never pass.
- **Fail-soft startup.** `build_sandbox_runner` returns `None` when
  disabled, when Docker is unreachable, when the image is missing, on any
  exception, or after the 15s startup timeout. The app still boots, and
  `execute_code` then reports `SandboxUnavailable` → inconclusive.

## Dependencies
- PHASE-01 (config), PHASE-04 (graph nodes to attach to).

## Implementation Notes
Keep this section short. Record image name/tag, the exact `docker run` flags, and
the timeout/limit values.

- **Image:** `aca-sandbox:py3.11-v1` (id `sha256:3ec1aca300bb…`, ~48 MB),
  built from `python:3.11-slim-bookworm@sha256:a36c24f9cbdf4fd0f52d67f0823eeac19c2028c637cecc392d97f980d4fec56b`
  (Python 3.11.16). It has no pip/setuptools/wheel/ensurepip and no
  curl/wget/nc, and it runs as user `sandbox` 10001:10001.
  Rebuild: `docker build -f docker/sandbox.Dockerfile -t aca-sandbox:py3.11-v1 docker`.
- **Container flags** (`app/execution/sandbox.py::container_kwargs`; CLI equivalent):
  `--network none` (+ `NetworkDisabled`) `--read-only`
  `--tmpfs /tmp:rw,noexec,nosuid,nodev,size=16m` `--user 10001:10001`
  `--cap-drop ALL` `--security-opt no-new-privileges:true` `--memory 256m`
  `--memory-swap 256m` `--cpus 1` (`nano_cpus=1e9`) `--pids-limit 64`
  `--ulimit nofile=256:256` `--ipc none` `--init`
  `--log-driver json-file --log-opt max-size=4m --log-opt max-file=1`
  `--label aca.sandbox=1` `--label aca.sandbox.instance=<uuid4>` `--workdir /tmp` `--hostname sandbox`. No
  volumes, binds, mounts, devices, ports or cap_add; `privileged=False`.
  Verified on a live container via `docker inspect` (see Test Results).
- **Limits:** default timeout 5s (0.5–30s allowed per request), kill grace 10s,
  captured stdout tail / stderr head 1 MiB each, `ExecutionResult`
  stdout/stderr 16,000 chars, harness string fields 2,000 chars, ≤ 100 cases,
  code ≤ 50,000 chars, encoded payload ≤ 120,000 bytes.
- **Settings:** `SANDBOX_ENABLED` (true), `SANDBOX_IMAGE`,
  `SANDBOX_MEMORY_MB` (256), `SANDBOX_MAX_CONCURRENT` (4),
  `SANDBOX_QUEUE_TIMEOUT_S` (30), `SANDBOX_STARTUP_TIMEOUT_S` (15).
- **Phase 07 contract:** an agent sets `execution_request: ExecutionRequest`
  (code + optional `TestSuite{entrypoint, cases[name,args,kwargs,expected]}`
  + `timeout_s`) in its `AgentStateUpdate`. It then reads
  `state.execution_result: ExecutionResult` and
  `state.verification: Verdict` (status, category, first_failing_case,
  expected, actual, error_type, diagnostics, summary). To loop, retarget
  `VERIFY_NODES["fail"]`.

## Manual Test Cases
### Test 1
Input: Code with `while True: pass` and a 5s timeout.
Expected: Container is killed at timeout; `ExecutionResult` reports timeout, not
a hang on the host.
Actual: real runner + real container (`tests/execution/test_phase6_manual.py`,
`sandbox` marker):
```
ACTUAL: test=timeout_kill status='timeout' timed_out=True exit_code=137 duration_ms=5247.016199995414 host_wall_s=5.435210299998289 verdict_status='fail' verdict_category='timeout'
```
SIGKILLed at the deadline (exit 137). The host returned in 5.4s, and no
`aca.sandbox` container remained. A cancelled run (a 20s timeout, cancelled
after 2s) also left no container (`cleanup_on_cancellation cleaned_up=True`).
**Pass.**

### Test 2
Input: Code that tries `urllib.request.urlopen(...)`.
Expected: Network call fails inside the sandbox; run completes with the error
captured, host unaffected.
Actual:
```
ACTUAL: test=network_blocked_caught status='completed' stdout='NETWORK_BLOCKED URLError\n' host_wall_s=0.6758537999994587
ACTUAL: test=network_blocked_bare status='runtime_error' error_type='URLError' stderr=''
ACTUAL: test=isolation_dns_blocked status='completed' stdout='DNS_BLOCKED gaierror\n'
```
It fails fast (0.7s) with `URLError`, and the error is captured either as
program output or as a `runtime_error`. **Pass.**

### Test 3
Input: A `two_sum` with an off-by-one, plus 4 test cases.
Expected: Verifier reports failing, names the first failing case, categorizes it.
Actual: through the full graph (`run_graph` with the real runner and a
`dsa_agent` override that sets the `ExecutionRequest`;
`tests/graph/test_phase6_manual.py`). The `two_sum` returns `i + 1`, with 4
cases c1..c4:
```
ACTUAL: route='dsa' execution_status='failed' verdict_status='fail' category='wrong_answer' first_failing_case='c1' expected=[0, 1] actual=[0, 2] diagnostics=['off_by_one_suspected'] summary="0/4 cases passed; first failing case 'c1': expected [0, 1], got [0, 2]"
```
**Pass.**

### Extra isolation probes (real sandbox)
```
ACTUAL: test=isolation_uid_gid status='completed' stdout='10001 10001\n'
ACTUAL: test=isolation_readonly_root status='completed' stdout="{'/opt/harness/run.py': 'PermissionError', '/etc/x': 'OSError', '/tmp': 'ok'}\n"
ACTUAL: test=isolation_noexec_tmp status='completed' stdout='EXEC_BLOCKED PermissionError\n'
ACTUAL: test=isolation_pids_limit status='completed' stdout='THREAD_COUNT 62 RuntimeError\n'
ACTUAL: test=isolation_memory_limit status='memory_exceeded' oom_killed=True stdout='' error_type=None verdict_status='fail'
ACTUAL: test=report_forgery status='sandbox_error' error_type='ReportTampered' verdict_status='fail' verdict_category='sandbox_error' verdict_diagnostics=['report_tampered']
ACTUAL: test=large_output status='passed' output_truncated=True stdout_len=16000
ACTUAL: test=concurrency_cap statuses=['completed', 'completed', 'completed', 'completed', 'completed'] peak_in_flight=2 total_wall_s=6.609714599995641
```
The forgery probe read the marker from `/proc/self/environ` and printed a fake
passing report. It was detected (4 marker occurrences) and scored as a fail.
With a cap of 2, 5 runs were queued in waves (peak 2) and none were rejected.

## Commands Run
```text
docker MCP: list_images / list_containers / pull_image python:3.11-slim-bookworm   # infra inspection + base pull
docker MCP: build_image   # failed without detail (MCP server can't read the host build context) -> CLI below
docker build -f docker/sandbox.Dockerfile -t aca-sandbox:py3.11-v1 docker
docker run --rm --network none --read-only --cap-drop ALL ... --entrypoint sh aca-sandbox:py3.11-v1 -c 'id; python -V; ...'   # image hardening check
docker run --rm <full flag set> -e ACA_MARKER=... -e ACA_PAYLOAD=... aca-sandbox:py3.11-v1   # harness smoke tests
venv/Scripts/python.exe -m pip install docker types-docker        # docker 7.2.0, types-docker 7.2.0.20260923
venv/Scripts/python.exe <scratch>/inspect_live.py                 # docker-py inspect of a live sandbox container
venv/Scripts/python.exe -m pyright                                # strict, after every packet
venv/Scripts/python.exe -m ruff check . && venv/Scripts/python.exe -m ruff format --check .
venv/Scripts/python.exe -m pytest -m sandbox -q -s -rs
venv/Scripts/python.exe -m pytest -q
venv/Scripts/python.exe -c "from app.graph.build import get_graph; print(get_graph().get_graph().draw_mermaid())"
```

## Test Results
- `pytest -q`: **1082 passed, 2 skipped, 2 failed**. The 2 failures are the
  pre-existing auth tests (`MultipleResultsFound`, Phase 05 Known Issues),
  unchanged. Phase 06 added 189 tests (baseline 893 passed). Docker,
  Postgres and Qdrant were up, so the `sandbox`/`integration`/`db` tests ran.
- `pytest -m sandbox`: **19 passed** against the real `aca-sandbox:py3.11-v1`:
  Tests 1–3, the isolation probes, the concurrency cap, cancellation cleanup,
  and the code-review regressions:
  ```
  ACTUAL: test=forged_subclass_equality status='failed' verdict_status='fail' verdict_category='wrong_answer'
  ACTUAL: test=monkeypatched_json_equal status='passed' verdict_status='fail' verdict_category='wrong_answer'
  ACTUAL: test=large_correct_list status='passed' verdict_status='pass' cases_passed=3 cases_total=3
  ACTUAL: test=self_referencing_list status='failed' verdict_status='fail' verdict_category='runtime_error'
  ```
  (In the monkeypatch case the harness *claims* passed and the host still
  fails it.) After every run `docker ps -a --filter label=aca.sandbox=1` was
  empty.
- **Live isolation check:** docker-py `inspect` of a running sandbox container:
  `NetworkMode=none`, no networks, `NetworkDisabled=True`,
  `ReadonlyRootfs=True`, `User=10001:10001`, `CapDrop=[ALL]`, `CapAdd=None`,
  `SecurityOpt=[no-new-privileges:true]`, `Privileged=False`,
  `Memory=MemorySwap=268435456`, `NanoCpus=1e9`, `PidsLimit=64`,
  `Tmpfs=/tmp rw,noexec,nosuid,nodev,size=16m`, `Init=True`, `IpcMode=none`,
  `Ulimits nofile 256`, `Binds/Mounts/Devices=None`, `PortBindings={}`. A 4s
  timeout killed a busy loop at 4.3s (exit 137), and the container was removed.
- `pyright` (strict, app + tests + harness): 0 errors. `ruff check` /
  `format --check`: clean.
- Graph (`draw_mermaid`): `{dsa,debug,explain}_agent --> execute_code -->
  verify -.-> final_response`; `clarify --> final_response`; the `route`
  edges are unchanged.
- `code-review` (high): 10 findings. **None ran user code on the host.**
  Fixed, each with a regression test:
  1–3. Verification could be faked from inside the process: an int subclass
     with `__eq__`, a monkeypatched `_json_equal`, or the host trusting the
     harness's `passed` flag. Fix: the host re-checks digests and the exact
     case set, and only exact builtin types are accepted. The remaining risk
     of (1) is recorded under Known Issues.
  4. A large report lost its opening marker (stdout was cut to its last 64 KiB),
     so a correct solution was flagged as tampering. Fix: per-case report
     budget, 1 MiB tail, 4m log.
  5. `sweep_orphans` killed another live worker's containers. Fix: per-instance
     label, and the sweep removes only non-running or stale (> 300s) containers.
  6. A cancellation during `create` leaked the container. Fix: shielded create
     future + removal; `_remove` retries once.
  7. Raw `requests` exceptions escaped `DockerException` handling. Fix: they are
     mapped to `SandboxError`, and cleanup catches everything.
  8. An unserialisable result (a self-referencing list) made the whole run
     `inconclusive`. Fix: caught per case → that case fails.
  9. The agent → `execute_code` edges were hard-coded. Fix: derived from
     `ROUTE_NODES`.
  10. The truncation helper was duplicated. Fix: one `truncate_text` in
      `app/execution/base.py`.

## Security / Reliability Notes
- **Never** execute user code on the API host under any circumstance.
- Enforce limits at the container level, not in Python.
- Cap concurrent sandboxes; queue beyond the cap.

## Known Issues
- **Visible tests can be gamed (accepted, owner decision 2).** User code runs
  in the same process as the harness. It can read the marker and payload
  (including `expected`) from `/proc/self/environ`, forge a report with
  matching digests, and `os._exit(0)`. That is equivalent to hard-coding
  answers to tests the user can already see. It only fools the submitter,
  and host-side digests plus case-set checks block every cheaper trick.
  Hidden tests would need an out-of-process grader with non-dumpable
  secrets. Revisit if the sandbox ever grades untrusted third-party code.
- The docker MCP `build_image` fails without detail (its server cannot read
  the host build context), so images are built with the `docker` CLI. The MCP
  also has no inspect tool, so isolation was checked with docker-py `inspect`.
- **The image must exist before startup.** A missing image or a stopped Docker
  Desktop makes `app.state.runner = None`: the app starts, and runs return
  `SandboxUnavailable` → inconclusive. Build with the command in
  Implementation Notes.
- The deadline starts when `container.start()` returns. Container create/start
  overhead (~0.3–1s on Docker Desktop) is counted in `duration_ms` but not in
  the user's time budget.
- Python threads cannot be cancelled: after a timeout, the first `wait()` call
  keeps an executor thread until the kill lands (the executor has
  `max_concurrent × 3` workers).
- `.env.example` does not list the new `SANDBOX_*` settings (the project deny
  rule blocks agent access to `.env.*`). The owner should add
  `SANDBOX_ENABLED`, `SANDBOX_IMAGE`, `SANDBOX_MEMORY_MB`,
  `SANDBOX_MAX_CONCURRENT`, `SANDBOX_QUEUE_TIMEOUT_S`,
  `SANDBOX_STARTUP_TIMEOUT_S`.
- No agent sets `execution_request` yet (Phase 07), so every live `/chat` turn
  skips execution and `verification` is `null`.
- Pre-existing, outside Phase 06: the 2 auth `MultipleResultsFound` test
  failures (see Phase 05).

## Git Commit
`967b880` — feat: phase 6 code execution sandbox + verification

## Next Phase
- PHASE-07 — Specialized Agents (DSA Solver, Debugger, Code Explainer).
