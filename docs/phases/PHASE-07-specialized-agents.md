# PHASE-07 — Specialized Agents (DSA Solver, Debugger, Code Explainer)

## Status
Done

## Goal
Replace the Phase 04 stubs with the three real capabilities — as LangGraph
subgraphs — using the planner, RAG context, and the sandbox. This is where the
"teach, don't dump" behaviour becomes real.

## Scope
- `app/agents/dsa_solver.py` + subgraph — derive a solution the teaching way:
  understand → constraints → pattern → brute force → why it's slow → key insight
  → pseudocode → code → line-by-line → tests → complexity → common mistakes.
- `app/agents/hint_engine.py` — the **progressive hint ladder** (L0 nudge → L1
  what-to-track → L2 data-structure hint → L3 concrete idea → L4 pseudocode →
  L5 partial → L6 full). Emits the *next* hint given how far the user has gotten;
  stops when solved.
- `app/agents/debugger.py` + subgraph — static analysis → infer intended
  approach → run tests (Phase 06) → find failing case → trace → localize → explain
  the bug → patch → re-run → verify.
- `app/agents/explainer.py` + subgraph — AST/Tree-sitter parse → structure
  (module/class/function/block/line) → line-by-line explanation → complexity.
- `app/agents/reviewer.py` — code review pass (correctness via tests,
  complexity, readability, Python practices, edge cases, improvements).
- Swap the stub route targets in `app/graph/build.py` for these subgraphs.

## Out of Scope
- Final response formatting/streaming (Phase 08) — agents return structured
  results, not final prose.
- Frontend, evaluation.
- Future phases.

## Current Implementation
Inspected 2026-09-25 (phases 01-06 committed; `00011eb` = phase 6 done).

**Phase 04 graph contract (`app/graph/`)**
- `state.py` — `AgentState` (frozen Pydantic, `extra="forbid"`) carries
  `input / structured_input / intent / profile / recent_context / plan /
  retrieved_context / execution_request / execution_result / verification /
  route / agent_output / response / events / events_persisted / errors`.
  Nodes return the `AgentStateUpdate` TypedDict partial.
  `AgentOutcome{text, topic, pattern, solved, hints_used,
  needed_full_solution, errors}` is what an agent node must produce — the
  `hints_used` / `needed_full_solution` fields already exist and already flow
  into the learning event, but the stubs leave them at their defaults.
  `GraphContext{llm, session, user_id, conversation_id, retriever,
  knowledge_top_k, runner}` is injected via LangGraph `context=`.
- `routing.py` — `RouteKey = "dsa"|"debug"|"explain"|"clarify"`, `INTENT_ROUTES`
  (CODE_REVIEW + OPTIMIZATION currently map to `explain`), `ROUTE_NODES`
  (`dsa_agent` / `debug_agent` / `explain_agent` / `clarify`), and
  `VERIFY_NODES` where all four verdict statuses currently target
  `final_response`.
- `nodes.py` (757 lines) — real pipeline nodes plus three stubs:
  `dsa_agent` / `debug_agent` / `explain_agent` all return
  `_stub_outcome(label, plan)`, a fixed plan-derived placeholder string.
  `safe_node(name, fn, fallback)` wraps every node so an exception degrades to
  the fallback + a `NodeError`; `FALLBACKS` must contain exactly the same keys
  as `NODE_FUNCTIONS`.
- `build.py` — edges: `START → understand_input → classify_intent →
  load_learner_profile → plan_teaching → retrieve_knowledge → route →
  {agent nodes} → execute_code → verify → final_response →
  update_learner_model → END` (`clarify` skips straight to `final_response`).
  `RECURSION_LIMIT = 20`. `build_graph(node_overrides=...)` exists for tests.
- Node signature: `async def node(state, runtime: Runtime[GraphContext]) ->
  AgentStateUpdate` (`Node` protocol, keyword-only `runtime`).

**Phase 04 planner (`app/agents/planner.py`)** — `analyze_problem`,
`build_plan`, `difficulty_for`, `INTENT_DEFAULTS`. `TeachingPlan`
(`app/schemas/plan.py`) has `difficulty, assistance_level
("hint"|"concept"|"pseudocode"|"partial"|"full", ordered by
`ASSISTANCE_ORDER`), solution_strategy, topic, skill_level, step_by_step,
concise, watch_errors, rationale`.

**Phase 05 RAG** — `retrieve_knowledge` node fills
`state.retrieved_context: list[RetrievalHit]`
(`RetrievalHit{chunk: KnowledgeChunk{id,text,source,title,heading,topic,
pattern,metadata}, score, retrievers, reranked}`); `should_retrieve` /
`build_retrieval_query` decide and build the query. `Retriever` protocol lives
in `app/knowledge/base.py`.

**Phase 06 sandbox** — the execution entrypoint is the `CodeRunner` protocol
(`app/execution/base.py`: `async run(ExecutionRequest) -> ExecutionResult`),
implemented by `SandboxRunner` in `app/execution/runner.py` and constructed by
`build_sandbox_runner(...)`. (There is no free function named `run_code`.)
Schemas in `app/schemas/execution.py`: `ExecutionRequest{language, code, tests:
TestSuite|None, timeout_s}`, `TestSuite{entrypoint, cases: [TestCase{name,
args, kwargs, expected}]}`, `ExecutionResult{status, cases: [CaseResult],
stdout, stderr, phase, error, ...}` with `cases_passed` / `cases_total` /
`first_failure` properties, and `Verdict{status, category, first_failing_case,
expected, actual, error_type, summary, diagnostics, cases_passed,
cases_total}`. `app/execution/verification.py::verify(result, request) ->
Verdict` is the pure, deterministic ground-truth judge. The graph's
`execute_code` node is a no-op whenever `state.execution_request is None` —
which is *always* today, because no stub sets one.

**Phase 03 events** — `app/memory/events.py::record_event`,
`requested_help_for`, `rebuild_profile`. `nodes.py::_build_learning_event`
already reads `agent_output.hints_used`, `.needed_full_solution`, `.errors`,
`.pattern`, `.solved`, so populating those on the real agents is all that is
needed.

**What does not exist yet:** `app/agents/{hint_engine,dsa_solver,debugger,
explainer,reviewer}.py`, `app/graph/subgraphs/`, `app/schemas/agent_results.py`,
`tests/agents/test_{hint_engine,debugger,explainer}.py`. `tests/agents/` holds
only `test_planner.py`.

**Environment note:** `tree-sitter` / `tree-sitter-python` are **not
installed** (not in `requirements.txt`, not in the venv). Docker is up and the
`aca-sandbox:py3.11-v1` image is present. LangGraph is 1.2.12.

## Planned Changes
1. Implement the hint ladder as a pure function of (problem, plan, progress).
2. Build the DSA subgraph using RAG context + hint engine; never emit full code
   when the plan says hint-level.
3. Build the debugger subgraph on top of the sandbox; always re-verify patches.
4. Build the AST-based explainer (Python via `ast`; Tree-sitter for structure).
5. Implement the reviewer, grounding "correctness" in sandbox results.
6. Register subgraphs; update routing; add `LearningEvent` fields (hints_used,
   needed_full_solution) from actual agent behaviour.
7. Tests per agent with fixed fixtures.

## Files Expected
- Create: `app/agents/{dsa_solver,hint_engine,debugger,explainer,reviewer}.py`,
  `app/graph/subgraphs/{dsa,debug,explain}.py`, `app/schemas/agent_results.py`,
  `tests/agents/test_hint_engine.py`, `tests/agents/test_debugger.py`,
  `tests/agents/test_explainer.py`
- Modify: `app/graph/{build,routing,state}.py`, `app/memory/events.py`
- Delete: None unless explicitly approved

## Architecture Decisions
- Hint level is driven by the `TeachingPlan`, so the same problem yields
  different help for different learners — the core differentiator.
- Debugger and reviewer treat sandbox verification as ground truth; the LLM
  explains results, it does not certify correctness.
- Each capability is an isolated subgraph so the system stays clean as it grows.

### Decisions locked at the start of Phase 07

1. **Parsing: `ast` for semantics, Tree-sitter for structure.** Both are real
   dependencies (`tree-sitter` 0.26.0, `tree-sitter-python` 0.25.0, added to
   `requirements.txt`). The stdlib `ast` module drives line-by-line
   explanation and complexity (`ast.parse` / `walk` / `get_source_segment`);
   Tree-sitter supplies the structural outline and survives syntactically
   broken input, which `ast` cannot parse at all -- useful for the debugger.
2. **Subgraph integration: invoke from inside the existing node function.**
   The compiled subgraph is called by the `dsa_agent` / `debug_agent` /
   `explain_agent` node bodies rather than being registered directly as the
   node. LangGraph 1.2.12 supports registering a compiled graph as a node and
   merges the parent `Runtime[GraphContext]` into it automatically, but doing
   so would bypass `safe_node`, losing the per-node fallback + `NodeError`
   degradation. Keeping the wrapper preserves both that and the stable Phase
   04 node names/edges.
3. **`VERIFY_NODES` left unchanged; the debugger owns its retry loop.**
   Retargeting `"fail"` to `debug_agent` (as `routing.py` originally
   anticipated) would create a `debug_agent -> execute_code -> verify ->
   debug_agent` cycle competing with `RECURSION_LIMIT = 20`. Instead the
   debugger retries internally with an explicit attempt cap, so the outer
   graph stays an acyclic DAG.
4. **Debugger runs code twice, deliberately.** Its internal sandbox runs are
   exploratory (find the failing case, validate a candidate patch); the
   `ExecutionRequest` it surfaces is then re-run by the stable outer
   `execute_code -> verify` path, which produces the authoritative `Verdict`.
   One extra sandbox run is the price of keeping ground truth on a single,
   well-tested code path.

5. **Hint-ladder levels (Phase 08 depends on this).** Seven rungs, `HintLevel`
   IntEnum: `L0_NUDGE, L1_WHAT_TO_TRACK, L2_DATA_STRUCTURE, L3_CONCRETE_IDEA,
   L4_PSEUDOCODE, L5_PARTIAL, L6_FULL`. The ceiling per plan is
   `MAX_HINT_LEVEL_FOR_ASSISTANCE` in `app/schemas/agent_results.py` --
   the single source of truth: `hint->L2, concept->L3, pseudocode->L4,
   partial->L5, full->L6`. Climbing is `min(last + 1, ceiling)`: never skips a
   rung, never exceeds the ceiling, idempotent once at the ceiling, and
   returns `None` once solved. `reveals_code` is only ever true at L5/L6, and
   `HintResult` enforces both invariants by validator.
6. **Stage gating is by the turn's actual level, not the plan ceiling.** On
   turn 1 `next_hint` returns L0 even for a `full`-assistance plan, so gating
   on the ceiling would ask the LLM for a full solution and then throw it
   away. Each `DSAResult` field is gated on the rung that earns it
   (`_STAGE_GATES` in `app/agents/dsa_solver.py`); `code` is produced only at
   exactly L6.
7. **Cross-turn hint progress is reconstructed from learning events, not new
   state.** `AgentState` has nowhere to persist `HintProgress`, and it is
   frozen/`extra="forbid"` by design. Since `_build_learning_event` already
   persists `hints_used` (= level + 1) and `solved` keyed by
   `conversation_id` + `topic`, the `dsa_agent` node rebuilds progress by
   reading the most recent matching event (`last_level = hints_used - 1`).
   Zero schema change, and the ladder survives across turns.

## Dependencies
- PHASE-04 (planner/graph), PHASE-05 (RAG context), PHASE-06 (sandbox/verify).

## Implementation Notes
- **Agent result schema (Phase 08 consumes this):** `app/schemas/agent_results.py`
  defines `DSAResult`, `DebugResult`, `ExplainResult`, `ReviewResult`, each with
  `to_outcome() -> AgentOutcome` mapping onto the Phase 04 contract, plus
  `HintResult`/`HintLevel`, `StaticFinding`, `BugLocation`, `CodeStructureNode`,
  `LineExplanation`, `ReviewFinding`. These carry **structured data, not prose** --
  Phase 08 owns all formatting. `to_outcome()` fills `text` with a placeholder
  short field only.
- **Enforced-by-validator invariants** (verified independently by the planner):
  `HintResult` rejects `level > ceiling` and `reveals_code` below L5;
  `DSAResult` rejects `code` at any level below L6; `DebugResult` rejects
  `fixed=True` without `final_verdict.status == "pass"`; `ReviewResult` exposes
  correctness only via a `claims_correct` property gated on a passing verdict.
- **Subgraph pattern:** each capability has a private `TypedDict` state (never
  merged into `AgentState`), a module-level compiled subgraph built once, and a
  frozen `*RunResult` dataclass boundary (`{result, execution_request}`) that
  P7's node body maps back into an `AgentStateUpdate`.
- **LLM budget:** one call per DSA turn (a single structured call returning all
  stages), at most three per debug turn. Every call goes through
  `runtime.context.llm` (the traced, budgeted wrapper).

## Manual Test Cases
Automated as `tests/graph/test_phase7_manual.py` so they re-run on every suite
pass rather than being a one-off transcript.

### Test 1
Input: `DSA_HINT` on "Subarray Sum Equals K", learner mid-level, `prefers_hints`.
Expected: Returns the next single hint (not full code); repeated asks climb the
ladder; full solution only at the top level.
Actual: **PASS.** A `prefers_hints` plan yields `assistance_level="hint"`,
ceiling L2. Repeated asks return levels 0 -> 1 -> 2 and then hold at 2
indefinitely. `DSAResult.code` is `None` at every rung and no runnable solution
body appears in any hint text. Only a `full`-assistance plan climbs to L6, and
only there does `code` become non-None (verified separately by the planner
across all five assistance levels: ceilings 2/3/4/5/6, never skipping a rung,
idempotent at the ceiling).

### Test 2
Input: `CODE_DEBUG` on a wrong sliding-window solution with tests.
Expected: Identifies the failing case, explains the window-shrink bug, patches,
re-runs, and verification passes.
Actual: **PASS, against the real Docker sandbox** (`@pytest.mark.sandbox`,
image `aca-sandbox:py3.11-v1`; the test skips rather than silently faking if the
engine is unreachable, and it reported `1 passed`). Recorded output:
`initial='fail' category='wrong_answer' first_failing_case='c1' attempts=1
final='pass' fixed=True`. A fake-runner variant covers machines without Docker.
Separately verified by the planner: given an LLM that insists "there is no bug,
the code is 100% CORRECT" while the sandbox keeps failing, the result is still
`fixed=False` -- the verdict overrules the model, every patch is re-executed,
and the attempt cap holds at 2.

### Test 3
Input: `CODE_EXPLAIN` on a `two_sum` implementation.
Expected: Line-by-line explanation from AST structure + correct O(n)/O(n)
complexity.
Actual: **PASS.** Hashmap `two_sum` -> `O(n)` time / `O(n)` space; the
nested-loop brute force -> `O(n^2)`. Big-O comes from the static heuristic, not
the LLM. Every `LineExplanation.lineno` maps to a real line of the submitted
source.

## Commands Run
```text
./venv/Scripts/python.exe -m pyright                          # strict, 0 errors
./venv/Scripts/python.exe -m ruff check app tests             # clean
./venv/Scripts/python.exe -m pytest tests -q                  # full suite
./venv/Scripts/python.exe -m pytest tests -q -m sandbox       # Docker-backed
./venv/Scripts/python.exe -m pytest tests/graph/test_phase7_manual.py -m sandbox -s
```

## Test Results
- `pytest tests -q` -> **1149 passed, 2 failed, 12 skipped** (95s).
- The 2 failures are **pre-existing and unrelated to Phase 07**:
  `tests/auth/test_refresh_tokens_db.py::test_create_refresh_token_never_stores_raw_token`
  and `tests/auth/test_routes.py::test_login_persists_hashed_refresh_token_without_raw_values`,
  both `sqlalchemy.exc.MultipleResultsFound`. They fail identically on the
  unmodified tree and in isolation, which points at leftover rows in the dev
  Postgres rather than test-ordering leakage. Not fixed here -- out of scope.
- `pytest tests -m sandbox` -> **20 passed** (Docker-backed), including the new
  Manual Test 2.
- `pyright` (strict, whole repo) -> **0 errors, 0 warnings, 0 informations**.
- `ruff check app tests` -> clean.
- New tests added this phase: `tests/agents/test_{hint_engine,dsa_solver,
  debugger,explainer,reviewer}.py`, `tests/graph/test_phase7_wiring.py`,
  `tests/graph/test_phase7_manual.py`.
- Stale Phase 04 stub assertions in `tests/graph/{test_build,test_chat_api,
  test_execute_verify_nodes,test_nodes,test_phase4_manual,test_routing}.py`
  were updated to assert real subgraph behaviour, preserving each test's
  original intent (zero-LLM-call paths, no-echo security checks, routing).

### Defects found by `code-review` (high) and fixed before commit
The pre-commit review found the phase's two headline features were **inert
end-to-end**, despite green unit tests. Both are fixed:

- **The hint ladder never advanced past L0.** `DSAResult.to_outcome()` returns
  `solved=None` (correct -- a hint turn does not know whether the learner
  solved it), but `update_learner_model` only persists an event when
  `solved is not None`. So a DSA turn never wrote an event, and
  `resolve_hint_progress` -- which rebuilt progress by reading events -- always
  returned a fresh `HintProgress()`. Every turn replayed L0. The Manual Test 1
  and the P3 tests missed it because they passed `progress=` in explicitly,
  testing the subgraph in isolation and skipping the integration seam.
  **Fix:** hint progress is conversation state, not evidence about the learner,
  so it now lives in its own store -- `hint_progress` table (migration
  `c2bfb7a6c2b8`, unique on `(user_id, conversation_id, topic)`, upserted),
  `app/memory/hint_progress.py`, read/written only by `dsa_agent`. Verified:
  three consecutive `dsa_agent` turns on one conversation+topic now climb
  `0 -> 1 -> 2` through the real store with nothing mocked
  (`test_dsa_agent_hint_level_climbs_across_turns_via_hint_progress_store`).
  This also fixes the key mismatch (lookup used `plan.topic`, writes used
  `agent_output.topic`) and stops debugger/explainer events polluting DSA
  progress.
- **The debugger could never produce a fix.** `DEFAULT_MAX_LLM_CALLS` was 3; a
  debug turn spends `classify_intent` + `infer_approach` + `explain` + `patch`
  = 4, so the patch call always raised `LLMBudgetExceededError`, which
  `patch_code` swallowed as an `LLMError`, leaving `patched_code=None` and
  `fixed=False` on every real run. **Fix:** raised to 8, which covers the worst
  real path (image + debug + retry = 6) with headroom, guarded by a test
  asserting the default fits that path rather than a bare literal.

### Bug found and fixed during this phase
`DebugResult.to_outcome()` / `ReviewResult.to_outcome()` mapped a **"skipped"**
or **"inconclusive"** verdict to `solved=False`. Since `update_learner_model`
persists an event whenever `solved is not None`, a turn where the sandbox was
simply unavailable -- nothing executed at all -- was recorded as an observed
failure and **lowered the learner's skill level** (`sliding_window` 0.3 -> 0.26).
That is the same error class as trusting an LLM's correctness claim, inverted:
asserting a negative that nothing verified. Fixed with a shared
`solved_from_verdict()` helper in `app/schemas/agent_results.py`:
`pass` -> True, `fail` -> False, `skipped`/`inconclusive`/absent -> **None**.
Four tests that had been written to assert the buggy values were corrected to
assert the event being surfaced but **not** persisted, with the profile
untouched.

## Security / Reliability Notes
- All code runs go through the Phase 06 sandbox — agents never execute directly.
- Guard against hint-ladder skipping (don't leak full solution at a low level).

## Known Issues
- **Deferred to Phase 08 (found by `code-review`, recorded not dropped):**
  `to_outcome()` lossily projects a rich structured result onto one `text`
  field, so a `CODE_REVIEW` turn replies with the correctness boilerplate while
  every style/complexity finding is discarded, and `ExplainResult` can yield an
  empty `text` (dropping `structure` and `line_explanations` entirely) when the
  rationale call returns None. `final_response` passes that through raw. This
  is genuinely response-generation work, which Phase 08 owns -- but the blank
  reply is a defect today, not a formatting preference.
- **`list_events` pages 50 rows and filters in Python** rather than filtering by
  `conversation_id`/`topic` in SQL. No longer on the hint path (that moved to
  the dedicated store), but still true for other callers.
- ~~**Duplicate static findings for nested functions.**~~ **FIXED in Phase 08.**
  `_check_unused_and_shadowed` now walks only its own scope, stopping at nested
  `def`/`async def`/`class` boundaries, so each finding is emitted once.
  Regression tests in `tests/agents/test_debugger.py`.
- ~~**Nothing upstream populates `state.execution_request.tests`.**~~ **FIXED in
  Phase 08.** `app/execution/testgen.py::extract_test_suite` derives a
  `TestSuite` from the statement's worked examples (`Input:`/`Output:`, markdown
  tolerant) plus the entrypoint of the learner's own code, parsing values with
  `ast.literal_eval` only and returning `None` rather than guessing.
  `debug_agent` and the review branch of `explain_agent` pass it through;
  an explicit suite already on the state still wins. Verified end to end against
  the real stack: `verification=status=pass cases=2/2` on a buggy `two_sum`.
- **A related, far worse defect was found while verifying that fix, and is also
  FIXED in Phase 08:** `visit_Compare` zipped `sides` (N+1) against `node.ops`
  (N) under `strict=True`, so `static_analysis` raised `ValueError` on **any**
  comparison -- meaning the debugger failed on essentially every real
  submission, degraded by `safe_node` into a generic apology. The Phase 07 suite
  was green throughout because no unit test fed it a comparison.
- **The debugger deliberately runs code twice** (internal exploratory runs plus
  the authoritative outer `execute_code -> verify`). Accepted trade-off, see
  Architecture Decision 4; costs one extra sandbox run per debug turn.
- **The complexity heuristic is static and bounded.** It reasons from loop
  nesting, recursion, and auxiliary allocation; it returns `None` rather than
  guessing when it cannot decide, and it will not derive tight bounds for
  algorithms whose complexity is not evident from syntactic structure.
- `BugLocation.symbol` is always `None` today -- no deterministic source for a
  symbol name was available; only `lineno` is populated.

## Git Commit
`f5ddafe` -- feat: phase 7 specialized agents -- dsa, debugger, explainer, reviewer

## Next Phase
- PHASE-08 — Response Generation, Frontend & Evaluation (LangSmith).
