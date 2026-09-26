# PHASE-08 — Response Generation, Frontend & Evaluation (LangSmith)

## Status
Done

## Goal
Turn structured agent results into clear, adaptive responses; ship a usable
frontend; and close the loop with LangSmith tracing + an evaluation suite so the
agent's teaching quality is measurable.

## Scope
- `app/response/generate.py` — Response Generator: render the correct output
  shape (hint / explanation / correct code) from the agent result + plan, with
  next steps. Optional streaming.
- `app/response/format.py` — consistent structured sections (approach, code,
  complexity, common mistakes) matching the plan's assistance level.
- `frontend/` — Streamlit app: multimodal input (text/code/image upload), chat
  view, hint-ladder controls, learner-profile panel.
- LangSmith: ensure every graph node/LLM/tool call is traced end-to-end.
- `eval/` — dataset + evaluators: routing accuracy, hint-appropriateness
  (no full solution when hint was requested), debug-fix success (verified),
  explanation correctness, groundedness to retrieved context.
- `POST /chat` returns the final structured response; wire the Streamlit client.

## Out of Scope
- React/Next.js frontend (later), Redis/Celery scale-out (later).
- New agent capabilities.
- **LangSmith, deferred out of this phase by the project owner (2026-09-26):**
  no tracing changes, no eval dataset, no evaluators, no `run_eval.py`, no
  `make eval`, and no tests touching any of it. The owner verifies LangSmith
  manually. `app/llm/client.py::Tracer`, `app/llm/embeddings.py` and the
  `langsmith_*` settings are **not to be modified in this phase.** The Scope
  bullets above covering LangSmith and `eval/` are therefore carried forward to
  a later phase, not implemented here; `eval/` stays a `.gitkeep`.

## Current Implementation
Inspected 2026-09-26. Phases 01-07 are committed and clean (`f5ddafe` phase 7
code, `f9d8649` phase 7 doc). Working tree carries only `.mcp.json` (added the
`token-savior` server entry) and an untracked `cc_shortcuts.txt`.

**Response layer** — `app/response/__init__.py` exists and is **empty (0
lines)**. `generate.py` / `format.py` do not exist. `app/graph/nodes.py::
final_response` (line 593) is a pass-through: `response = agent_output.text or
SAFE_FALLBACK_RESPONSE`, with `_final_response_fallback` returning
`SAFE_FALLBACK_RESPONSE`. Its own docstring says "Phase 08 replaces this".

**The load-bearing gap: the structured agent result never reaches
`final_response`.** `AgentState` (frozen, `extra="forbid"`) has
`agent_output: AgentOutcome | None` and nothing else for agent output.
`AgentOutcome` is `{text, topic, pattern, solved, hints_used,
needed_full_solution, errors}` — one flat `text` field. The Phase 07 nodes
(`dsa_agent` l.387, `debug_agent` l.435, `explain_agent` l.447) each build the
rich result (`DSAResult` / `DebugResult` / `ExplainResult` / `ReviewResult`) and
then **discard it**, returning only `{"agent_output": run.result.to_outcome()}`
(+ `execution_request`). `to_outcome()` projects lossily: `DSAResult` -> the
hint text, `DebugResult` -> `bug_explanation or inferred_approach or ""`,
`ExplainResult` -> `complexity_rationale or ""` (dropping `structure` and all
`line_explanations`), `ReviewResult` -> `findings[0].message or ""`. This is
exactly the Phase 07 "Known Issue" deferred here, and it means Phase 08 cannot
be built on `agent_output` alone — the data it must format is thrown away one
node earlier.

**Phase 07 result schema** (`app/schemas/agent_results.py`, 351 lines) — the
input Phase 08 formats. `HintLevel` IntEnum L0..L6;
`MAX_HINT_LEVEL_FOR_ASSISTANCE` maps `hint->L2, concept->L3, pseudocode->L4,
partial->L5, full->L6`; `HintResult{level, text, is_terminal, reveals_code,
ceiling}` (validator: `reveals_code` only at L5/L6).
`DSAResult{topic, pattern, hint, understanding, constraints, brute_force,
why_slow, key_insight, pseudocode, code, complexity_time, complexity_space,
common_mistakes, citations}` — validator `_code_requires_full_hint_level`
already rejects `code` below L6, so the "hint can never render as full code"
invariant has a schema-level anchor to build on.
`DebugResult{static_findings, inferred_approach, failing_case, bug_explanation,
bug_location, patched_code, attempts, initial_verdict, final_verdict, fixed}`
(validator: `fixed` requires `final_verdict.status == "pass"`).
`ExplainResult{structure, line_explanations, complexity_*}`.
`ReviewResult{correctness_verdict, findings}` + `claims_correct` property.
`solved_from_verdict()` maps pass/fail/None.

**Plan** (`app/schemas/plan.py`) — `TeachingPlan{difficulty, assistance_level,
solution_strategy, topic, skill_level, step_by_step, concise, watch_errors,
rationale}`; `AssistanceLevel = hint|concept|pseudocode|partial|full` with
`ASSISTANCE_ORDER`. `SolutionStrategy = socratic_hints | guided_debugging |
step_by_step_explanation | concise_review | clarify`.

**`POST /chat`** (`app/graph/api.py`, 179 lines) — multipart form
(`text, language, image, conversation_id, topic`), bearer auth via
`get_current_user`, returns `ChatResponse{response, route, intent, plan,
verification, events, events_persisted, errors, llm_calls}`. Calls
`run_graph(...)` then `session.commit()`. Retriever/runner/top-k come off
`app.state`. Not streaming.

**API surface available to a frontend** — only three routers, no prefixes
except auth: `/auth/{register,login,refresh,logout}` (`app/auth/routes.py`),
`/understand` (`app/input/api.py`), `/chat`. There is **no** endpoint to create
a conversation and **no** endpoint to read the learner profile —
`start_conversation` and `get_profile` exist in `app/memory/` but are called
only from nodes and tests. Both are needed for the chat view (hint ladder needs
a stable `conversation_id`) and the profile panel.

**Hint-ladder continuity already works server-side.** Phase 07 moved cross-turn
progress into the `hint_progress` table (migration `c2bfb7a6c2b8`,
`app/memory/hint_progress.py`), read/written only by `dsa_agent`, keyed
`(user_id, conversation_id, topic)`. So "request the next hint" is *not* a new
API concept: it is the same problem re-sent with the same `conversation_id` +
`topic`, and the ladder climbs `min(last+1, ceiling)` server-side. The frontend
needs no new backend verb for it, only to keep both ids stable.

**LangSmith today** — `app/llm/client.py::Tracer` with `from_settings` /
`disabled`, `trace(fn)` (opens ambient `tracing_context` so LangChain's own run
is captured) and `run(name, run_type, inputs, fn, outputs=)` (creates a
standalone run; docstring already forbids raw user text in `inputs`). Used by
`LangChainLLMClient` and `app/llm/embeddings.py`. Config:
`langsmith_tracing: bool = False`, `langsmith_api_key: SecretStr | None`,
`langsmith_project = "adaptive-coding-agent"`; tracing is enabled only when
both the flag and a key are set. **Nothing traces the graph run itself** —
`run_graph` calls `get_graph().ainvoke(config={"recursion_limit": ..., "run_name":
"teaching_graph"})` with no tracing context and no metadata, so each LLM call
surfaces as its own root run instead of one tree per turn. `langsmith` is in
`requirements.txt`.

**`eval/` and `frontend/`** — both exist but contain only `.gitkeep`. No
`Makefile` anywhere in the repo. `streamlit` is **not** in `requirements.txt`
(`httpx` is). `scripts/` holds only `build_index.py`.

**Test/type baseline inherited from Phase 07** — `pytest tests -q` ->
1149 passed, 2 failed, 12 skipped; the 2 failures are pre-existing auth/DB
`MultipleResultsFound` errors unrelated to this phase. `pyright` strict -> 0
errors. `ruff check app tests` -> clean. Markers available:
`integration`, `live`, `db`, `sandbox` (`pyproject.toml`).

**Verified library APIs (context7, 2026-09-26)**
- LangSmith: `Client.create_dataset(dataset_name=...)` ->
  `client.create_examples(dataset_id=..., examples=[{"inputs":..., "outputs":...}])`;
  `client.evaluate(target, data=..., evaluators=[...], experiment_prefix=...,
  description=..., max_concurrency=...)` (`evaluate()` module-level is
  equivalent). Custom evaluators are plain functions taking any of
  `(inputs, outputs, reference_outputs)` (or legacy `(Run, Example)`) and
  returning `bool | int | dict{"key","score"}`.
- Streamlit: `st.chat_message(role)`, `st.chat_input(...)` — and
  `st.chat_input(accept_file=True, file_type=["png","jpg","jpeg"])` returns a
  `ChatInputValue` with `.text` and `["files"]`, which covers this phase's
  multimodal input in one widget; `st.session_state`, `st.sidebar`,
  button-plus-`session_state` for the hint control.

## Planned Changes
Work packets as agreed with the owner on 2026-09-26. P4 (LangSmith tracing) and
P5 (eval suite) from the original plan are **dropped from this phase** -- see
Out of Scope. Streaming, which the Scope called optional, is **in**, on the
owner's instruction ("implement streaming in frontend ui chat").

- **P0 -- carry the structured result through state.** Prerequisite discovered
  during inspection: `AgentState` had nowhere to put the rich agent result, so
  the response generator had nothing to format. Adds
  `AgentState.agent_result: AgentResult | None`.
- **P1 -- response generation.** `app/response/{format,generate}.py`; rewire
  `final_response`.
- **P2 -- `/chat` finalization + streaming + the two endpoints the frontend
  needs** (`POST /conversations`, `GET /profile`), plus `POST /chat/stream`.
- **P3 -- Streamlit frontend.** `frontend/app.py` + components; consumes the
  stream.
- **P6 -- tests.** Unit (generation refuses code below the gate), E2E `/chat`
  for one DSA-hint / one debug / one explain, playwright over the UI.

## Files Expected
- Create: `app/response/{generate,format}.py`, `app/schemas/response.py`,
  `frontend/app.py`, `frontend/components/*.py`,
  `tests/graph/test_agent_result_state.py`, `tests/response/test_*.py`,
  `tests/e2e/test_chat_flows.py`, `tests/frontend/test_ui_playwright.py`
- Modify: `app/schemas/agent_results.py` (`kind` discriminator + `AgentResult`),
  `app/graph/{state,nodes}.py`, `app/graph/api.py` (finalized `ChatResponse`,
  `/chat/stream`, `POST /conversations`, `GET /profile`), `app/main.py`,
  `requirements.txt` (streamlit), `README.md` (run the frontend)
- NOT created this phase: `eval/*` (deferred with LangSmith)
- Delete: None unless explicitly approved

## Architecture Decisions
- Response shape is derived from the plan, so a hint request can never render as
  a full solution -- enforced at generation, not just prompting.
- Evaluation measures *teaching* behaviour (hint appropriateness, verified fixes,
  groundedness), not just answer correctness -- matching the project's thesis.
  *(Carried to a later phase with the rest of LangSmith.)*

### Decisions locked at the start of Phase 08

1. **`AgentState` gains `agent_result`; `agent_output` stays.** They answer
   different questions and both are needed: `agent_output` (`AgentOutcome`) is
   the deliberately lossy projection the learning-event/profile path consumes,
   while `agent_result` is the full structured result the response layer
   formats. Collapsing them would either pollute the event path with display
   data or keep the response layer starved. `agent_result` is a **discriminated**
   union (`kind: Literal["dsa"|"debug"|"explain"|"review"]`, added with
   defaults so no existing constructor call changes) because `run_graph` calls
   `AgentState.model_validate()` on LangGraph's output and all four result
   models have exclusively optional fields -- an untagged union could silently
   revalidate a `ReviewResult` as an `ExplainResult`.
2. **Rendering is a pure function with no LLM call of its own.**
   `render(result, plan, verification) -> GeneratedResponse` is deterministic
   and synchronous: all prose already exists inside the Phase 07 result (hints,
   explanations, findings), so the response layer *selects and arranges*, it
   never generates. This keeps the teaching invariant testable without an LLM
   and adds zero cost to a turn.
3. **The code gate is structural, and it is the single enforcement point.**
   A code section is emitted only when the result itself carries code that
   Phase 07's own validators already permit: `DSAResult.code` is non-None only
   at `HintLevel.L6_FULL` (existing validator), and the renderer additionally
   refuses to emit code unless `plan.assistance_level == "full"`. Both
   conditions must hold. There is no prompt, no instruction, and no LLM
   involved in that decision -- which is the whole point: a hint-level turn
   cannot render full code even if an upstream agent misbehaves.
   `DebugResult.patched_code` is gated the same way and additionally requires
   `fixed=True` (which its validator already ties to a passing sandbox
   verdict), so a patch is never shown as a fix unless the sandbox confirmed it.
4. **Section set per assistance level** (`app/response/format.py` owns this
   table; `hint -> full` in `ASSISTANCE_ORDER`):
   `hint`: next-hint + next-steps only. `concept`: + understanding, key insight,
   common mistakes. `pseudocode`: + constraints, brute force, why it is slow,
   pseudocode. `partial`: + complexity. `full`: + code. Every level also gets
   citations when the result carries them. A section is omitted, never emitted
   empty.
5. **No empty response is reachable.** `GeneratedResponse.text` is validated
   `min_length=1`; the renderer falls back through a documented ladder and, in
   the worst case, to `SAFE_FALLBACK_RESPONSE`. This closes the Phase 07 Known
   Issue where an `ExplainResult` with no `complexity_rationale` produced a
   blank reply and a `CODE_REVIEW` turn dropped every finding.
6. **Streaming is progress + sections over SSE, not LLM tokens.** The response
   is assembled after the graph finishes, so there are no model tokens to
   forward; and `LLMClient` exposes no token-stream method (adding one is a
   Phase 07 provider-wrapper change, out of scope here). Instead
   `POST /chat/stream` drives `graph.astream(stream_mode="updates")` (confirmed
   present on the installed LangGraph 1.2.12) and emits one SSE `stage` event
   per completed node -- *planning*, *retrieving*, *running your code in the
   sandbox*, ... -- then the rendered sections, then a terminal `done` event
   carrying the same JSON body `POST /chat` returns. Stage events carry a fixed
   label chosen from a server-side table keyed by node name; they never contain
   user text, code, or model output. `POST /chat` is kept unchanged as the
   non-streaming path so tests and non-UI clients stay simple.
7. **Two small endpoints are added because "the frontend calls the API only"
   is otherwise unsatisfiable.** `start_conversation` and `get_profile` existed
   only as node/test-level functions, so a UI had no way to open a conversation
   (needed for a stable `conversation_id`, which is what makes the hint ladder
   climb) or read the profile panel's data. Both are thin, auth-scoped wrappers
   over existing Phase 03 functions -- no new domain logic.
8. **The hint-ladder control is not a new API verb.** Phase 07 already
   persists ladder position in the `hint_progress` table keyed
   `(user_id, conversation_id, topic)`. "Show me the next hint" therefore
   re-sends the same problem with the same `conversation_id` + `topic`, and the
   server climbs `min(last+1, ceiling)` on its own. The frontend's only
   obligation is to keep both ids stable across the turn.

## Dependencies
- PHASE-07 (agent results), PHASE-04 (plan/state), PHASE-05 (context for
  groundedness eval), PHASE-06 (verified fixes for debug eval).

## Implementation Notes
Keep this section short. Record eval dataset location, evaluator names, and the
baseline scores once measured.

## Manual Test Cases
### Test 1
Input: End-to-end `DSA_HINT` request via the Streamlit UI (playwright-driven).
Expected: UI shows a single next hint with a control to request the next level;
no full code until the top level. *(The original "trace appears in LangSmith"
clause is dropped -- LangSmith is out of this phase and verified manually by the
owner.)*
Actual: **PASS**, driven with the playwright MCP against the real stack
(uvicorn + Postgres + Qdrant + Docker sandbox, real LLM provider). Logged in
through the UI as a freshly registered user, submitted a DSA problem with topic
`sliding_window`, then clicked **Show me the next hint** twice. Recorded:
ladder indicator read `Hint 1 of 3` -> `Hint 2 of 3` -> `Hint 3 of 3 at this
assistance level`; at the ceiling the button was **gone** and the caption read
"This is as far as this hint level goes -- try implementing the idea and testing
it."; `pre code` block count was **0** at every rung and no `def ...(` appeared
anywhere in the page text; citations rendered on each turn (RAG grounding).

### Test 2 -- superseded
`make eval` on the seed dataset. **Deferred with the rest of LangSmith**; no
eval suite is built in this phase, so there is no baseline to record here.

### Test 3
Input: A `hint`-level turn whose upstream result nevertheless carries code, plus
one turn at each of the five assistance levels.
Expected: No code section is rendered below `full`/L6 at the generation layer,
and no turn renders an empty response.
Actual: **PASS**, verified independently of the packet's own tests by
constructing a `DSAResult` that legally carries `code` at L6 and rendering it at
all five assistance levels: `hint`/`concept`/`pseudocode`/`partial` produced
`reveals_code=False` with the code string absent from the **entire assembled
text**, and only `full` emitted a `code` section. Same for
`DebugResult.patched_code`, which additionally stayed hidden at `full` when
`fixed=False` (an unverified patch is never shown). `plan=None` behaved as the
most conservative level. Every empty-result x level combination still produced
non-empty text.

### Test 4
Input: `POST /chat/stream` for a DSA-hint turn, consumed by the Streamlit UI.
Expected: Stage events arrive in graph order and render progressively, the
terminal `done` payload equals what `POST /chat` returns for the same input, and
no stage event contains user-supplied text.
Actual: **PASS** against the running API. 11 stage frames arrived in exact graph
order -- Reading your input, Working out what you need, Recalling how you learn,
Planning how to help, Looking up references, Choosing an approach, Working
through the problem, Running your code in the sandbox, Checking the results,
Writing your answer, Updating what I know about you -- followed by exactly one
terminal `done` frame. A unique marker embedded in the submitted problem text
appeared in **no** stage frame, and neither did any submitted identifier.

## Commands Run
```text
./venv/Scripts/python.exe -m pyright                        # strict, whole repo incl. frontend
./venv/Scripts/python.exe -m ruff check app tests frontend  # clean
./venv/Scripts/python.exe -m pytest tests -q                # full suite
docker compose up -d                                        # Postgres + Qdrant for db/integration tests
./venv/Scripts/python.exe -m uvicorn app.main:app --port 8125
API_BASE_URL=http://127.0.0.1:8125 streamlit run frontend/app.py --server.port 8579
# playwright MCP drove the Streamlit UI for Manual Test 1
```

## Test Results
- `pytest tests -q` -> **1270 passed, 2 skipped, 0 failed**. This is the first fully green suite in the
  project's history: Phase 07 closed with 2 failures and 12 skipped. The 2
  remaining skips are the opt-in `live` LLM tests (`RUN_LIVE_LLM=1`).
- `pyright` (strict, now including `frontend/`) -> 0 errors, 0 warnings.
- `ruff check app tests frontend` -> clean.
- New tests this phase: `tests/response/test_{format,generate}.py`,
  `tests/graph/test_{agent_result_state,stages,stream_graph,chat_stream_api}.py`,
  `tests/memory/test_api.py`, `tests/execution/test_testgen.py`,
  `tests/e2e/test_chat_flows.py`, plus regression tests added to
  `tests/agents/test_debugger.py`.
- **End-to-end debug turn against the real stack** (the capability that was
  dead before this phase): `route=debug`, `errors=[]`,
  `verification=status=pass cases=2/2`. The extractor derived 2 cases from the
  statement's **markdown** examples, the sandbox ran them, `example_2` failed
  (`expected [1, 2], got [0, 0]`), the patch was applied and **re-verified**.
  `reveals_code=False` at `hint` assistance: the bug is explained, the patched
  code withheld.

### Bugs found and fixed while verifying this phase
All four were **pre-existing**; none was introduced by Phase 08. Each was found
by end-to-end verification rather than by the suite, which is the point worth
remembering: the suite was green through all of them.

1. **`visit_Compare` crashed on every comparison** (`app/agents/debugger.py`).
   It zipped `sides` (N+1 expressions) against `node.ops` (N) under
   `strict=True`, so `static_analysis` raised `ValueError` on *any* `>`, `in`,
   `is`, `!=` or chained comparison -- i.e. on essentially every real
   submission. `safe_node` degraded that into "Something went wrong on my end",
   so the project's headline debugging feature failed silently for all
   practical input while 12 debugger unit tests passed. Fixed to `sides[:-1]`;
   6 parametrized regression cases added plus one asserting the check still
   flags `x is 5` and still ignores `x is None`.
2. **Nothing populated `execution_request.tests`** (Phase 07 Known Issue,
   deferred here). `run_debug`/`review_code` read tests from state that no node
   ever set, so every real debug turn degraded to `no_tests`/`inconclusive` and
   `fixed` could never become `True`. Closed by `app/execution/testgen.py`.
3. **The extractor ignored markdown statements.** `**Input**:` and
   backtick-wrapped values parsed to nothing -- which is exactly what a learner
   gets pasting from a problem site, so the common case silently produced no
   tests. Fixed; 6 parametrized shapes now covered.
4. **Three tests asserted on global table emptiness.** `select(RefreshToken)`
   (x2) and `select(Message)` required the whole table to hold one/zero rows,
   so they passed only on a pristine DB and failed on any developer machine
   whose dev database had been used. All three scoped to the row under test.
   Separately, two Phase-5 integration tests handed the DSA agent a bare
   `FakeLLMClient()` (no canned response), so `safe_node` silently degraded the
   turn; one asserted `errors == []` and failed, its sibling hid the same bug
   by never asserting on errors. Both fixed, and the missing assertion added.

### Defects found by `code-review` (high effort) and fixed before commit
All five were real; two would have actively harmed the learner.

1. **Closures reported as dead code** (`app/agents/debugger.py`). The Phase 08
   nested-scope fix scoped the walk for *reads* as well as *writes*, so any
   variable assigned in an outer function and read only inside a nested `def`
   was reported "assigned but never used" -- i.e. every closure. `assigned` is
   now scoped (which is what fixes the double-report) while `loaded` comes from
   the full `ast.walk`.
2. **A mismatched test suite could fail correct code**
   (`app/execution/testgen.py`). The entrypoint was "the first top-level
   `def`", so a submission defining a helper first had its *helper* called with
   the solution's arguments: `TypeError` -> a real `fail` verdict -> the
   debugger patching a non-bug -> `solved=False` persisted against correct
   code. The entrypoint is now chosen *after* the cases and must actually
   accept them (`_accepts` checks keyword names against parameters and
   positional count against arity, honouring `*args`/`**kwargs`), preferring a
   public function and returning `None` when nothing fits.
3. **`reveals_code` under-reported, and a hint could leak code past the gate**
   (`app/response/generate.py`, `app/schemas/response.py`). The flag was
   derived from section *kinds* only, but at L5/L6 the hint's own body *is* the
   partial/full solution and renders as `next_hint` at every level. Two changes:
   the flag now accounts for a code-revealing hint, and -- more importantly --
   such a hint is **withheld** when the plan does not permit code, so a result
   built under a permissive plan cannot leak through a restrictive one. The
   `iff`-sections validator was relaxed to the directions that actually protect
   the learner (a code section requires the flag; the flag requires
   `partial`/`full`), and new tests cover the leak path both ways.
4. **A missing `session_factory` produced HTTP 200 with an empty body**
   (`app/graph/api.py`). `_get_session_factory` was called outside the `try`, so
   its `RuntimeError` escaped instead of yielding the terminal `error` frame the
   docstring promises; the frontend read that as success with zero frames.
5. **A 30s read timeout could desync the hint ladder**
   (`frontend/api_client.py`). A turn spending several LLM calls plus a sandbox
   run exceeds 30s, and the backend commits the turn regardless -- so the UI
   showed a failure for a turn that had succeeded, and the next "next hint"
   resent against an already-advanced ladder. Chat calls now use
   `httpx.Timeout(connect/write=30, read=None)`; the quick calls keep 30s.

Also brought `logger.exception` in the SSE handler in line with the codebase's
convention of logging the exception *type* only -- every other site does this
deliberately, because exception text can carry untrusted input or secrets.

### Product defects found via the UI and fixed
- **Every turn rendered twice.** `chat_view.render_turn` printed the assembled
  `response` markdown *and* the structured `sections`, which are the same
  content -- so each hint, next step and citation appeared twice. The
  structured form now wins; the flat markdown is used only for a degraded turn
  that has no `generated` payload.
- **A self-referential hint.** `_rung_text`'s L2 rung took the first retrieved
  label, which is often the topic itself, producing "For a sliding_window
  problem, sliding_window is often the right shape" -- and leaking an internal
  slug into the teaching voice. It now picks the first label that differs from
  the topic, falls back to generic phrasing, and humanizes slugs
  (`sliding_window` -> `sliding window`).

## Security / Reliability Notes
- Never send secrets or raw user PII to LangSmith metadata.
- Frontend calls the API only; it never executes code or hits the sandbox
  directly.

## Known Issues
- **LangSmith is deferred, not done.** No tracing changes, dataset, evaluators,
  `run_eval.py` or `make eval` exist; `eval/` is still a `.gitkeep`. The graph
  run itself is still untraced (each LLM call surfaces as its own root run
  rather than one tree per turn), and no run metadata is attached. Deferred at
  the owner's instruction, who verifies LangSmith manually.
- **No streaming of model tokens.** `POST /chat/stream` streams *stage* progress
  and then the finished sections; the reply is assembled after the graph
  completes, and `LLMClient` exposes no token-stream method. Token-level
  streaming needs a provider-wrapper change (Phase 07 surface).
- **The test extractor is deliberately narrow.** It parses `Input:`/`Output:`
  worked examples via `ast.literal_eval` and returns `None` rather than guess,
  so statements that describe examples in prose, or use non-literal values,
  still yield no tests and the debugger correctly reports it could not verify.
- `BugLocation.symbol` is still always `None` (Phase 07).
- `list_events` still pages 50 rows and filters in Python (Phase 07); off the
  hint path, but true for other callers.
- The complexity heuristic remains static and bounded (Phase 07).
- The frontend duplicates `CODE_SECTION_KINDS` from `app.schemas.response`
  rather than importing it, because `frontend/` must not import `app.*`. If the
  server's set ever changes, this needs changing with it.
- Streamlit caches imported modules, so editing `frontend/` requires a server
  restart; an edit alone will not take effect on the next interaction.
- **One observed transient failure of the Docker-backed e2e debug test.**
  `tests/e2e/test_chat_flows.py::test_debug_turn_reaches_real_sandbox_and_never_overclaims_correctness`
  failed once while another process was driving Docker concurrently, then
  passed 8 consecutive runs (4 alone, 4 in the same invocation that failed).
  Recorded rather than dismissed: it builds and tears down a real sandbox
  runner, so it is sensitive to Docker contention. If it recurs, suspect
  container start latency rather than the assertion.

## Git Commit
Not created yet.

## Next Phase
- v2 backlog: React/Next.js frontend, Redis short-term cache, Celery for heavy
  eval/execution, multi-language sandbox, preference-optimization from evaluated
  data.
