# PHASE-06 — Code Execution Sandbox & Verification

## Status
Not Started

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
Document only what actually exists after inspecting the repository.

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

## Architecture Decisions
- One ephemeral container per run — no reuse — to avoid state leakage.
- Verification consumes only execution facts; the LLM never overrides a failing
  test into a "pass".
- Design `run_code` language-agnostic even though only Python ships now.

## Dependencies
- PHASE-01 (config), PHASE-04 (graph nodes to attach to).

## Implementation Notes
Keep this section short. Record image name/tag, the exact `docker run` flags, and
the timeout/limit values.

## Manual Test Cases
### Test 1
Input: Code with `while True: pass` and a 5s timeout.
Expected: Container is killed at timeout; `ExecutionResult` reports timeout, not
a hang on the host.
Actual:

### Test 2
Input: Code that tries `urllib.request.urlopen(...)`.
Expected: Network call fails inside the sandbox; run completes with the error
captured, host unaffected.
Actual:

### Test 3
Input: A `two_sum` with an off-by-one, plus 4 test cases.
Expected: Verifier reports failing, names the first failing case, categorizes it.
Actual:

## Commands Run
```text
# commands
```

## Test Results
-

## Security / Reliability Notes
- **Never** execute user code on the API host under any circumstance.
- Enforce limits at the container level, not in Python.
- Cap concurrent sandboxes; queue beyond the cap.

## Known Issues
-

## Git Commit
Not created yet.

## Next Phase
- PHASE-07 — Specialized Agents (DSA Solver, Debugger, Code Explainer).
