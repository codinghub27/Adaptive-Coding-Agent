# PHASE-03 — Learner Profile, Memory & Learning Events

## Status
Not Started

## Goal
Give the agent memory: a structured, evolving learner profile, conversation
memory, and a learning-event system that updates the profile from observed
activity (not guesses).

## Scope
- `app/db/models/` — SQLAlchemy models: `User`, `Conversation`, `Message`,
  `LearnerProfile`, `LearningEvent`.
- `app/memory/profile.py` — read/update learner profile: `skill_levels` per
  topic (0–1), `learning_preferences`, `common_errors`.
- `app/memory/conversation.py` — persist + fetch recent conversation memory
  (short-term context window assembly).
- `app/memory/events.py` — record a `LearningEvent` per interaction and apply a
  deterministic profile-update rule (e.g. EWMA on skill levels).
- Alembic migration for the new tables.

Profile shape (target):
```json
{
  "language": "Python",
  "skill_levels": { "arrays": 0.75, "sliding_window": 0.55, "dp": 0.30 },
  "learning_preferences": { "prefers_hints": true, "likes_step_by_step": true,
                            "wants_line_by_line_explanations": true },
  "common_errors": ["off_by_one", "incorrect_window_shrinking", "edge_cases"]
}
```

Learning event shape (target):
```json
{ "problem": "...", "topic": "arrays", "pattern": "sliding_window",
  "difficulty": "easy", "requested_help": "explanation", "hints_used": 2,
  "needed_full_solution": false, "errors": [], "solved": true,
  "time_spent": 12, "concepts": ["Chebyshev distance"] }
```

## Out of Scope
- Deciding *how* to teach from the profile (Teaching Planner, Phase 04).
- Fine-tuning any model — explicitly not doing this; profile + retrieval only.
- Future phases.

## Current Implementation
Inspected 2026-09-23 (after `ea0c677`, Phase 01 `39db1cf` + Phase 02 `aadb117` Done).

- **DB base** — `app/db/base.py`: `Base(DeclarativeBase)` with a `MetaData`
  naming convention (ix/uq/ck/fk/pk). No ORM models exist; no `app/db/models/`.
- **Session** — `app/db/session.py`: `create_engine(settings)` (async,
  `pool_pre_ping`), `create_session_factory(engine)` (`expire_on_commit=False`),
  `get_session(request)` FastAPI dependency reading `app.state.session_factory`,
  `ping_db`. Re-exported from `app/db/__init__.py`.
- **Alembic** — async `alembic/env.py`, `target_metadata = Base.metadata`, URL
  from `get_database_settings()`. One revision `0bfa97139592` (initial empty);
  local DB is at head. `alembic/` is excluded from pyright.
- **Phase 02 types** — `app/schemas/intent.py`: `Intent(StrEnum)` with 11 values
  (`DSA_SOLVE, DSA_HINT, CODE_DEBUG, CODE_EXPLAIN, CODE_REVIEW,
  ERROR_EXPLANATION, OPTIMIZATION, CONCEPT_EXPLANATION, IMAGE_CODE_ANALYSIS,
  TEST_CASE_ANALYSIS, APPROACH_DISCUSSION`), `IntentResult`. `app/schemas/input.py`:
  `StructuredInput` (`source, question, code, error, problem, constraints,
  language`). All boundary schemas extend `APIModel` (`extra="forbid"`, `frozen`).
- **Memory** — `app/memory/__init__.py` exists and is empty. No profile,
  conversation, or event code.
- **Tests** — `tests/conftest.py` skips `integration` tests unless **both**
  Postgres and Qdrant are reachable. Right now Postgres (from `.env`) is up and
  Qdrant is down, so any DB test marked `integration` would be skipped.
- **Deps** — SQLAlchemy 2.0.54, Alembic 1.20.0, Pydantic 2.12.5, asyncpg present;
  `aiosqlite` not installed.

## Planned Changes
1. Add SQLAlchemy models + Alembic migration.
2. Implement profile read/update with a documented, deterministic update rule.
3. Implement conversation persistence and a `get_recent_context()` assembler.
4. Implement learning-event creation and profile application; make it idempotent
   per event id.
5. Tests: profile update math, event → profile propagation, context assembly.

## Files Expected
- Create (done): `app/db/models/{__init__,user,conversation,message,profile,event}.py`,
  `app/memory/{profile,conversation,events}.py` (`__init__` populated),
  `app/schemas/{profile,event}.py`, `alembic/versions/c708bafc104e_memory.py`,
  `tests/memory/{__init__,conftest,test_profile_math,test_events,test_conversation}.py`
- Added beyond the original list (recorded):
  - `app/schemas/conversation.py` — `Role`, `MessageView` (return type of
    `get_recent_context`).
  - `tests/memory/conftest.py` — transactional `db_session` + user fixtures.
  - Test files split as `test_profile_math.py` (pure) / `test_events.py` /
    `test_conversation.py` instead of `test_profile.py` + `test_events.py`.
- Modified: `alembic/env.py` (imports `app.db.models` to register models —
  **instead of** `app/db/base.py`, which would be a circular import),
  `tests/conftest.py` (`_postgres_reachable` helper; `db` marker skip),
  `pyproject.toml` (register `db` marker).
- Delete: none.

## Architecture Decisions
- **Skill update rule (EWMA):** `new = (1 − α)·old + α·outcome`, **α = 0.2**,
  clamped to [0, 1], rounded to 6 dp. Unseen topic prior = **0.5**.
  Outcome score: solved without full solution → `max(0.4, 1.0 − 0.15·hints_used)`;
  solved but needed full solution → 0.3; unsolved → 0.1. One event updates both
  `topic` and `pattern` (if distinct).
- **Events are the source of truth.** `skill_levels` and `common_errors` are a
  derived projection; `rebuild_profile()` replays a user's events in `seq`
  order (BigInteger identity column — `created_at` is identical within one
  transaction, so it can't order a replay). `learning_preferences` and
  `language` are user-declared, not derived, and survive a rebuild.
- **Idempotency:** `LearningEvent.id` is caller-supplied.
  `INSERT … ON CONFLICT (id) DO NOTHING RETURNING id`; the profile is only
  updated when a row was actually inserted. Concurrent events for one user
  serialize on `SELECT … FOR UPDATE` of the profile row.
- **`common_errors`** stored as tag → count (JSONB); exposed as a list of the
  top 5 tags, count desc then alphabetical.
- **Intent → `requested_help`** (filled when the caller doesn't set it):
  `DSA_HINT`→hint, `DSA_SOLVE`→solution, `CODE_DEBUG`/`ERROR_EXPLANATION`→debug,
  `CODE_EXPLAIN`/`CONCEPT_EXPLANATION`/`IMAGE_CODE_ANALYSIS`→explanation,
  `CODE_REVIEW`/`OPTIMIZATION`→review, `TEST_CASE_ANALYSIS`→test_analysis,
  `APPROACH_DISCUSSION`→approach. **Topic/pattern are not derivable from
  intent**; they are supplied by the caller (Phase 07 agents).
- **User scoping:** every table except `users` has a `user_id` FK; every store
  function takes `user_id` and filters by it. `messages` has a composite FK
  `(conversation_id, user_id) → conversations(id, user_id)`, so a message can't
  belong to a conversation its user doesn't own.
- **Transactions:** memory functions only `flush`; the caller commits.
- **Conversation order** uses a per-conversation `seq` (assigned under a row lock
  on the conversation), not timestamps. Window = `DEFAULT_CONTEXT_WINDOW = 10`
  (module constant; overridable per call; no `config.py` change).
- Units/limits: `time_spent` in minutes; `problem` truncated to 200 chars.

## Dependencies
- PHASE-01 (DB/session), PHASE-02 (intent + structured input feed events).

## Implementation Notes
α = 0.2, prior 0.5 (see Architecture Decisions). Intent→help mapping lives in
`app/memory/events.py::INTENT_TO_HELP`. Tests use a `db` marker (Postgres only)
and a per-test transaction joined with `join_transaction_mode="create_savepoint"`,
rolled back at teardown — nothing persists.

## Manual Test Cases
### Test 1
Input: Record a solved `sliding_window` event with `hints_used: 0`.
Expected: `sliding_window` skill level increases; event stored; re-applying the
same event id does not double-count.
Actual: Event `topic=arrays, pattern=sliding_window, solved, hints_used=0` →
`applied=True`, `skill_levels={'arrays': 0.6, 'sliding_window': 0.6}` (from
prior 0.5); `list_events` shows 1 row. Re-submitting the same event id →
`applied=False`, skills unchanged, still 1 event. **Pass.**
(`tests/memory/test_events.py::test_manual_1_solved_sliding_window_increases_skill_and_is_idempotent`)

### Test 2
Input: Two turns in one conversation, then `get_recent_context()`.
Expected: Returns both turns in order within the configured window.
Actual: `seq=1 user 'How do I find the max sum subarray of size k?'`,
`seq=2 assistant 'What happens to the window sum when you slide one step?'` —
both returned, oldest→newest. **Pass.**
(`tests/memory/test_conversation.py::test_manual_2_two_turns_returned_in_order`)

## Commands Run
```text
venv/Scripts/alembic revision --autogenerate -m "memory"   # → c708bafc104e
venv/Scripts/alembic upgrade head && venv/Scripts/alembic downgrade -1 && venv/Scripts/alembic upgrade head
venv/Scripts/alembic check                                  # No new upgrade operations detected
venv/Scripts/python -m pytest tests/memory -v -s
venv/Scripts/python -m pytest -q
venv/Scripts/pyright
venv/Scripts/ruff check . && venv/Scripts/ruff format --check .
```

## Test Results
- `pytest tests/memory`: 46 passed (pure unit + `db` tests, after the
  code-review fix packet).
- Full suite: 185 passed, 4 skipped (Qdrant-dependent `integration` + opt-in
  live LLM — unchanged from Phase 02).
- Leak check after the run: `SELECT count(*) FROM users WHERE handle LIKE 'test-%'` → 0.
- `pyright` (strict, app + tests): 0 errors. `ruff check` / `ruff format --check`: clean.
- `code-review` (high): 10 findings, all addressed before commit:
  1. `db` skip matched the `tests/db/` package keyword → now `get_closest_marker`
     (Phase 01 unit tests no longer skipped when Postgres is down); Postgres
     probed once per collection.
  2. EWMA apply order could diverge from `seq` under concurrency → profile row
     is locked **before** the event insert.
  3. `get_profile` wrote (INSERT) on read → now read-only; empty view if absent.
  4. `list_events` limit < 1 → `ValueError`.
  5. Bounds: `hints_used ≤ 1000`, `time_spent ≤ 100000`, conversation title ≤ 200.
  6. Validators no longer hide bad input: non-string `errors` items rejected,
     blank `pattern` → None, blank `concepts` dropped.
  7. Added `ix_learning_events_conversation_id`.
  8. Event→conversation ownership is now DB-enforced: composite FK
     `(conversation_id, user_id) → conversations(id, user_id)`
     `ON DELETE SET NULL (conversation_id)` (PG15+ syntax; local PG 18,
     compose PG 16).
  9. View schemas rely on Pydantic config merge (`from_attributes=True` only).
  10. Kept intentionally: the extra `Message.user_id` filter in
      `get_recent_context` (defence in depth).
- `alembic check` after fixes: no drift; downgrade/upgrade cycled twice clean.
- Migration reviewed by the planner: 5 tables, convention-named constraints,
  composite FK, identity column, JSONB server defaults; downgrade drops in
  reverse FK order.

## Security / Reliability Notes
- Scope every read/write to the authenticated user id; no cross-user reads.
- Store only learning signals, not raw sensitive content beyond what's needed.

## Known Issues
- The migration requires PostgreSQL ≥ 15 (`ON DELETE SET NULL (col)`).
- No `users` creation API yet — callers must create the `User` row; write paths
  on an unknown `user_id` raise an FK `IntegrityError` (read paths don't).
- `rebuild_profile` replays a user's full event log in one pass — fine now,
  may need snapshotting once logs are large.
- `db`-marked tests need a migrated local Postgres (`alembic upgrade head`);
  they skip cleanly if Postgres is unreachable.
- Topic/pattern taxonomy is free-form (slug-normalized); a controlled vocabulary
  is deferred to the knowledge/agent phases.

## Git Commit
Not created yet.

## Next Phase
- PHASE-04 — LangGraph Orchestration & Teaching Planner.
