# PHASE-04 — LangGraph Orchestration & Teaching Planner

## Status
Done (2026-09-24), commit `d1e77eb`

## Goal
Wire the pieces from Phases 01–03 into a real LangGraph flow with a typed state,
and add the Teaching Planner node that decides *how* to help based on intent +
learner profile. Specialized agents are stubs here — this phase proves the graph.

## Scope
- `app/graph/state.py` — one typed `AgentState` (structured input, intent,
  profile snapshot, plan, retrieved context, execution result, response, events).
- `app/graph/nodes.py` — nodes: `understand_input`, `classify_intent`,
  `load_learner_profile`, `plan_teaching`, `route`, plus **stub** nodes for
  `dsa`, `debug`, `explain`, and a `final_response` node.
- `app/graph/build.py` — assemble nodes + conditional edges; compile the graph.
- `app/agents/planner.py` — Teaching Planner: given intent + profile + problem
  analysis, choose difficulty, assistance level (hint/concept/pseudocode/
  partial/full), and a solution strategy. Emits a typed `TeachingPlan`.
- `POST /chat` — run the graph end-to-end (stubbed agents), return response +
  the plan for inspection.

Graph skeleton:
```
START → understand_input → classify_intent → load_learner_profile
      → plan_teaching → route ─┬─ DSA ─┐
                               ├─ DEBUG┼→ (stub) → final_response
                               └─EXPLN─┘         → update_learner_model → END
```

## Out of Scope
- Real DSA/debug/explain logic (Phase 07), RAG (05), sandbox (06).
- Frontend, evaluation.
- Future phases.

## Current Implementation
Inspected 2026-09-24 (HEAD `b3193eb`; Phases 01–03 Done). Via token-savior
symbol lookups, postgres MCP (read-only) and the installed LangGraph source.

- **Graph / agents** — `app/graph/__init__.py` and `app/agents/__init__.py`
  exist and are empty. No state, nodes, planner, routing or `/chat`.
- **Input (Phase 02)** — `normalize_text(text, *, language_hint=None) ->
  StructuredInput` (pure, raises `ValueError` over `MAX_TEXT_CHARS=50_000`);
  `extract_from_image(llm, data, declared_mime=...)` (1 vision call; raises
  `ImageValidationError`/`LLMError`); `merge_inputs(image, text)`.
  `classify_intent(inp, client: LLMClient | None) -> IntentResult` — rules →
  1 LLM call → keyword fallback, **never raises**. `IntentResult{intent,
  confidence, source, rationale, low_confidence}` with
  `LOW_CONFIDENCE_THRESHOLD = 0.6`. `StructuredInput{source, question, code,
  error, problem, constraints, language, is_empty}`. All schemas extend
  `APIModel` (`extra="forbid"`, `frozen`).
- **`/understand`** (`app/input/api.py`) — multipart `text`/`language`/`image`;
  private helpers `_text_to_structured`, `_image_to_structured` (map errors to
  HTTP codes), `_combine`, `_valid_language_hint`; `get_llm` dependency reads
  `app.state.llm`. `BodySizeLimitMiddleware` guards the upload size.
- **Memory (Phase 03)** — `get_profile(session, user_id) -> LearnerProfileView
  {language, skill_levels: dict[str,float], learning_preferences:
  dict[str,bool], common_errors: list[str]}` (read-only; empty view if no row).
  `get_recent_context(session, user_id, conversation_id, limit=10) ->
  list[MessageView]` (raises `ConversationNotFoundError`). `add_turn(session,
  user_id, conversation_id, role, content, intent=None)`,
  `start_conversation(...)`. `record_event(session, user_id,
  LearningEventCreate) -> RecordEventResult{event, applied}` — idempotent, only
  `flush`es (caller commits). `LearningEventCreate` **requires `topic`
  (1–64 chars) and `solved: bool`**; `requested_help_for(intent)` maps intent →
  help category. `Difficulty = Literal["easy","medium","hard"]`.
- **DB** (`codingagent_db`, postgres MCP) — tables `users`, `conversations`,
  `messages`, `learner_profiles`, `learning_events` at head `c708bafc104e`.
  No `users` creation API (callers must create the row).
- **App** — `create_app()` lifespan puts `settings`, `engine`,
  `session_factory`, `qdrant`, `llm` on `app.state`; routers: `/health`,
  `/understand`.
- **LLM** — `LLMClient` Protocol (`chat`, `embed`, `vision`); every real call is
  traced by `Tracer` (LangSmith, off by default). Tests use
  `tests/input/fakes.py::FakeLLMClient` (records `chat_calls`/`vision_calls`).
- **Tests** — `db` marker + rolled-back `db_session`/`user_id` fixtures in
  `tests/memory/conftest.py`; `asyncio_mode = "auto"`.
- **Deps** — `langgraph 1.2.12` installed (not yet used). Confirmed API:
  `StateGraph(state_schema, context_schema=None)`,
  `add_conditional_edges(source, path, path_map)`, `compile()`,
  `ainvoke(input, *, context=..., version="v1"|"v2")`; nodes may take
  `runtime: Runtime[Ctx]` → `runtime.context`.
- **Stale doc** — README phase table still shows Phase 03 "Not Started".

## Planned Changes
1. Define `AgentState` and `TeachingPlan` schemas.
2. Implement the non-agent nodes using Phase 02/03 modules.
3. Implement `plan_teaching` with explicit rules (e.g. `prefers_hints` +
   weak skill → start hint ladder low; strong skill → concise).
4. Implement conditional routing on intent; stub agents return placeholder text.
5. Add clarifying-question branch when intent confidence is low.
6. Wire `update_learner_model` at graph end to emit a `LearningEvent`.
7. `POST /chat`; tests assert routing + plan for representative inputs.

## Files Expected
- Create: `app/graph/{__init__,state,nodes,build,routing}.py`,
  `app/agents/{__init__,planner}.py`, `app/schemas/plan.py`,
  `tests/graph/test_routing.py`, `tests/agents/test_planner.py`
- Modify: `app/main.py` (register `/chat`)
- Delete: None unless explicitly approved

**Actually created / modified (additions beyond the list are recorded here):**
- Created: `app/graph/{state,nodes,routing,build,api}.py`, `app/agents/planner.py`,
  `app/schemas/plan.py`, `app/llm/budget.py`,
  `tests/graph/{__init__,conftest,test_state,test_routing,test_nodes,test_build,test_chat_api,test_phase4_manual}.py`,
  `tests/agents/{__init__,test_planner}.py`, `tests/llm/test_budget.py`.
- Modified: `app/main.py` (chat router + a second `BodySizeLimitMiddleware` for
  `/chat`), `app/schemas/__init__.py` (plan exports), `README.md` (phase table).
- Additions beyond the plan:
  - `app/graph/api.py`: `/chat` router + `ChatResponse`, keeping `main.py` thin
    (same pattern as `app/input/api.py`).
  - `app/llm/budget.py`: `BudgetedLLMClient`, the per-run LLM call bound
    required by Security / Reliability Notes.
  - `tests/graph/conftest.py` re-exports the Phase 03 `db_session`/`user_id`
    fixtures; extra test files split by module.
  - Code-review de-duplication touched earlier phases (behaviour unchanged):
    `app/input/api.py` (`valid_language_hint`, `validate_request`,
    `IMAGE_VALIDATION_STATUS/DETAIL` made public), `app/schemas/event.py`
    (`_slug` -> public `slug_tag`, reused by the planner),
    `app/schemas/profile.py` (`LearnerProfileView.empty()`),
    `app/memory/profile.py` (`get_profile` uses it).

## Architecture Decisions
- State is a single typed object threaded through nodes (no hidden globals).
- Planner output is data (`TeachingPlan`), not prose, so agents and evaluation
  can consume it deterministically.
- Each specialized capability will become a **subgraph** in Phase 07; keep the
  route node's contract stable now.
- **State:** `AgentState` is a frozen Pydantic model (`extra="forbid"`); nodes
  never mutate it and return an `AgentStateUpdate` (TypedDict) partial update.
  `events`, `errors`, `events_persisted` are append-only (`operator.add`).
  Run-scoped dependencies (`llm`, `session`, `user_id`, `conversation_id`) live
  in `GraphContext`, passed via `ainvoke(context=...)` -> `runtime.context`.
  Verified against installed LangGraph 1.2.12 (frozen state is safe: LangGraph
  rebuilds the model from channels each step).
- **Planner is deterministic (no LLM).** `analyze_problem`: `topic` form hint,
  else profile skill keys matched on word boundaries in question/problem/error
  prose (never code), else unknown (skill = Phase 03 `PRIOR` 0.5).
  `difficulty_for`: < 0.4 easy, < 0.7 medium, else hard. `build_plan`:
  intent default (`INTENT_DEFAULTS`) -> weak skill (< 0.4) => hint ->
  `prefers_hints` => hint -> strong (>= 0.75, no hint pref) => one level up +
  `concise` -> cap at `partial` (never `full` on a first turn; the Phase 07
  ladder reaches it) -> `likes_step_by_step` overrides `concise`. Low/no intent
  => `clarify` plan. Each fired rule is a code in `TeachingPlan.rationale`.
- **Clarify, don't guess:** `select_route` -> `clarify` when input is missing/
  empty, intent is None or `low_confidence` (< 0.6), or the plan says clarify.
  The clarify node is deterministic and never echoes user text.
- **Learning events (owner decision A):** `update_learner_model` emits a
  `LearningEventCreate` into `state.events` for non-clarify turns with a known
  topic, but calls `record_event` only when the agent reports an observed
  outcome (`AgentOutcome.solved is not None`) **and** a session + user + topic
  exist. Stubs report `solved=None` => emitted, never saved; no skill drift
  from guesses. Saved ids go to `events_persisted`.
- **Conversation turns (owner decision B):** user + assistant turns are saved
  via `add_turn` only when `conversation_id` (and user + session) is set.
- **Failure isolation:** every node is wrapped by `safe_node(name, fn,
  FALLBACKS[name])`; any `Exception` => node-specific safe fallback + a
  `NodeError` holding only the exception class name and a fixed message (never
  the exception text); `CancelledError` propagates. Every DB read/write in a
  node runs in its own savepoint (`begin_nested`) so a failed statement never
  leaves the request transaction aborted. The graph never commits; `/chat` does.
- **LLM bound:** `run_graph` wraps the client in a fresh `BudgetedLLMClient`
  (`DEFAULT_MAX_LLM_CALLS = 3`: <= 1 vision + <= 1 intent today). Over budget
  raises `LLMBudgetExceededError(LLMError)`, which Phase 02 already degrades to
  the keyword fallback. `RECURSION_LIMIT = 20` guards against loops.
- **Image bytes** are dropped from state after `understand_input` so they are
  not serialized into every later step / trace.
- **`/chat`** is multipart like `/understand` (`text`, `language`, `image`,
  `user_id`, `conversation_id`, `topic`); same validation helpers and size
  middleware; `conversation_id` without `user_id` -> 422. Returns
  `ChatResponse{response, route, intent, plan, events, events_persisted,
  errors, llm_calls}`.

## Dependencies
- PHASE-02 (understand + intent), PHASE-03 (profile + events).

## Implementation Notes
Keep this section short. Record node names/contract and routing keys — these are
depended on by Phase 07.

**Node names (stable; `app/graph/build.py::NODE_FUNCTIONS` == `nodes.FALLBACKS`):**
`understand_input`, `classify_intent`, `load_learner_profile`, `plan_teaching`,
`route`, `dsa_agent`, `debug_agent`, `explain_agent`, `clarify`,
`final_response`, `update_learner_model`.

**Edges:** `START -> understand_input -> classify_intent -> load_learner_profile
-> plan_teaching -> route -?-> {dsa_agent | debug_agent | explain_agent |
clarify} -> final_response -> update_learner_model -> END` (conditional edge:
`route_after` + `ROUTE_NODES`).

**Routing keys** (`RouteKey`, `app/graph/routing.py::INTENT_ROUTES`):
- `dsa` -> `dsa_agent`: DSA_SOLVE, DSA_HINT, APPROACH_DISCUSSION
- `debug` -> `debug_agent`: CODE_DEBUG, ERROR_EXPLANATION, TEST_CASE_ANALYSIS
- `explain` -> `explain_agent`: CODE_EXPLAIN, CONCEPT_EXPLANATION,
  IMAGE_CODE_ANALYSIS, CODE_REVIEW, OPTIMIZATION
- `clarify` -> `clarify`: any low-confidence / missing intent or empty input

**Phase 07 contract:** replace the stub *implementations* behind
`dsa_agent`/`debug_agent`/`explain_agent` (signature
`async (state: AgentState, runtime: Runtime[GraphContext]) -> AgentStateUpdate`,
see `nodes.Node`) with subgraphs that set `agent_output: AgentOutcome`.
Setting `solved`/`topic`/`pattern`/`hints_used` is what makes
`update_learner_model` persist the event. Read `state.plan` (TeachingPlan) for
difficulty / assistance level; `retrieved_context` (Phase 05) and
`execution_result` (Phase 06) are reserved state fields.

## Manual Test Cases
### Test 1
Input: `CODE_DEBUG` request from a user weak in `sliding_window` who
`prefers_hints`.
Expected: Routes to debug stub; `TeachingPlan.assistance_level` starts low
(hint), difficulty matches profile.
Actual: Profile `{sliding_window: 0.3, arrays: 0.8}`, `prefers_hints: true`;
input = fenced python sliding-window function + `IndexError: list index out of
range` + question "my sliding window solution crashes, why?" (the question
sends Phase 02 to the LLM; the fake LLM answers CODE_DEBUG 0.9):
```
ACTUAL: route='debug' intent=CODE_DEBUG conf=0.90 source=llm plan(difficulty='easy' assistance='hint' strategy='guided_debugging' topic='sliding_window' rationale=['weak_skill', 'prefers_hints']) events=1 events_persisted=[] errors=[] llm_calls=1
```
Event emitted (topic `sliding_window`, requested_help `debug`, difficulty
`easy`) but not saved (stub `solved=None`); profile skills unchanged.
**Pass.** (`tests/graph/test_phase4_manual.py`, `db` marker; also via
`POST /chat` in `tests/graph/test_chat_api.py`.)

### Test 2
Input: Ambiguous message (low intent confidence).
Expected: Graph takes the clarifying-question branch instead of guessing.
Actual: "hmm can you look at this thing":
```
ACTUAL: route='clarify' intent=CODE_EXPLAIN conf=0.30 source=llm plan(strategy='clarify' rationale=['low_confidence']) events=0 events_persisted=[] errors=[] llm_calls=1
ACTUAL: route='clarify' intent=CONCEPT_EXPLANATION conf=0.20 source=fallback plan(strategy='clarify' rationale=['low_confidence']) events=0 events_persisted=[] errors=[] llm_calls=1
```
(first: LLM low confidence; second: unparsable LLM output -> keyword fallback).
Response is the deterministic clarifying question; user text is not echoed; no
event. **Pass.**

Live smoke (real Postgres/Qdrant, uvicorn :8768):
`curl -F "text=<code + IndexError traceback>" /chat` -> 200, `route: debug`,
intent CODE_DEBUG (rule), `llm_calls: 0`, `errors: []`, response `[debug stub] ...`.

## Commands Run
```text
venv/Scripts/python.exe -c "<inspect StateGraph / ainvoke / Runtime signatures>"   # LangGraph 1.2.12 API check
venv/Scripts/python.exe -m pyright                          # strict, after every packet
venv/Scripts/python.exe -m ruff check . && venv/Scripts/python.exe -m ruff format --check .
venv/Scripts/python.exe -m pytest -q
venv/Scripts/python.exe -m pytest tests/graph/test_phase4_manual.py -s -q -k manual
venv/Scripts/python.exe -c "from app.graph.build import get_graph; print(get_graph().get_graph().draw_mermaid())"
venv/Scripts/python.exe -m uvicorn app.main:app --port 8768 ; curl -F "text=..." /chat
```

## Test Results
- `pytest -q`: **615 passed, 2 skipped** (the 2 skips are the opt-in live-LLM
  tests; Postgres + Qdrant were up so `db`/`integration` tests ran).
- Phase 04 coverage: planner rule tables (incl. "never `full` on a first turn"
  across all intents x skills x prefs); all 11 intents routed end-to-end;
  low confidence -> clarify; every node forced to fail mid-run (run completes,
  `NodeError` recorded, secret-marker text never reaches state or logs); LLM
  budget (image + text <= 3; budget 1 -> vision consumes it, intent falls back,
  no extra call); decision A fake-outcome test (solved=True => persisted, skill
  rises; stub => emitted only); decision B turns saved only with
  `conversation_id`; a DB error in a read leaves the session usable.
- Graph edges asserted against the skeleton; `draw_mermaid()` matches.
- `pyright` (strict, app + tests): 0 errors. `ruff check` / `format --check`: clean.
- `code-review` (high): 10 findings. Fixed, each with a regression test:
  1. profile/context reads had no savepoint (aborted-transaction risk);
  2. an event-build failure skipped turn persistence;
  4. the plan fallback ignored low confidence;
  5. `/chat` duplicated `/understand`'s validation helpers;
  7. duplicated slug helper / empty-profile construction;
  8. image bytes carried through every node's state.
  Kept on purpose: (6) the unreachable `partial` cap (future-proof invariant,
  test-covered); (9) the `recent_context` load is in scope, consumers arrive in
  Phase 07/08; minor double image validation and two `add_turn` round trips.
  (3) unauthenticated `user_id` -> Known Issues. (10) recorded in Files Expected.

## Security / Reliability Notes
- Node failures must not crash the graph mid-run; return a safe fallback state.
- Bound total LLM calls per graph run to avoid runaway loops.

## Known Issues
- **No authentication:** `/chat` trusts a caller-supplied `user_id` (as the
  Phase 03 memory layer assumes). Anyone with a user's UUID can infer that
  user's skill/preferences from the returned plan and append turns to their
  conversation. Must be addressed before any non-local deployment; no current
  phase owns it.
- Agents are stubs: events are emitted but never persisted until Phase 07
  agents report `solved`.
- Topic detection only matches topics already in the learner's profile (or the
  `topic` form field); a new learner's first turn has topic `None` => no event.
- Graph-run tracing relies on LangGraph's own LangSmith integration (env vars);
  it is not separately wrapped by `Tracer`.
- `langgraph` stubs leak `Unknown` types: narrow
  `pyright: ignore[reportMissingTypeStubs|reportUnknownMemberType]` on the
  imports/`add_node`/`compile`/`ainvoke` calls in `build.py` and tests.
- `recent_context` is loaded into state but not yet consumed (Phase 07/08).

## Git Commit
`d1e77eb` — feat: phase 4 langgraph orchestration + teaching planner (not pushed)

## Next Phase
- PHASE-05 — Knowledge Base & Hybrid RAG.
