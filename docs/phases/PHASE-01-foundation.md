# PHASE-01 — Project Foundation & Core Infrastructure

## Status
Not Started

## Goal
Stand up a clean, typed FastAPI application skeleton with configuration,
database, a traced LLM client wrapper, and a health endpoint — the base every
later phase builds on.

## Scope
- Repository structure per `CLAUDE.md` (app/, tests/, docs/, frontend/).
- `app/config.py` — Pydantic v2 `Settings` loading env (LLM keys, DB URL,
  Qdrant URL, LangSmith keys). `.env.example` with all keys.
- `app/main.py` — FastAPI app factory, `/health` endpoint, CORS, lifespan.
- `app/db/` — async SQLAlchemy 2.0 engine + session dependency; Alembic init.
- `app/llm/` — provider-agnostic client wrapper for chat + embeddings + vision
  (single place that talks to the model SDK; stubbed vision/embeddings ok).
- `app/schemas/` — shared base Pydantic types.
- `docker-compose.yml` — PostgreSQL + Qdrant for local dev.
- `requirements.txt` / `pyproject.toml`, pyright config, ruff config, pytest.
- `docs/architecture.md` — commit the architecture diagram + spec here.

## Out of Scope
- Any graph/agent logic, intent classification, RAG, sandbox, frontend.
- Future phases.

## Current Implementation
Inspected 2026-09-22. **No application code exists yet.** Project folder has:

- Docs/config only: `CLAUDE.md`, `README.md`, `docs/TOOLING.md`,
  `docs/phases/PHASE-01..08`, `.gitignore`, `.env` (local secrets, not committed),
  `.mcp.json`, `.claude/settings{,.local}.json`, `.claude/agents/CodeExecutor.md`,
  `.token-savior-cache.json`, `.idea/` (PyCharm).
- **Missing:** `app/`, `tests/`, `frontend/`, `docs/architecture.md`,
  `pyproject.toml`, `requirements.txt`, `.env.example`, `docker-compose.yml`,
  Alembic setup.
- `.env` holds these keys: `DATABASE_URL`, `CLAUDE_DATABASE_URL`, `QDRANT_URL`,
  `QDRANT_API_KEY`, `QDRANT_TIMEOUT`, `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`,
  `LANGSMITH_PROJECT`, `GROQ_API_KEY`, `GROQ_API_KEY1`, `OPENROUTER_API_KEY`,
  `AGENTROUTER_API_KEY`, `TAVILY_API_KEY`, `SECRET_KEY`, `ALGORITHM`. There are no
  OpenAI or Anthropic keys.
- Toolchain: Python 3.11.9, Docker 29.5.3, and `alembic` are available globally.
  `pyright`, `ruff`, and `uv` are **not installed**. There is no project venv.
- Git: **the enclosing repo root is `C:/Users/srava` (the home directory)**, on
  branch `master` with no commits and files from another project already staged.
  This project folder shows up there as untracked.
- Config defects found: `.token-savior-cache.json` is invalid JSON (trailing
  comma). The `.gitignore` entries `../.claude/settings.local.json` and
  `coding-agent tools/.token-savior-cache.json` don't match anything in this
  repo. `settings.local.json` `WORKSPACE_ROOT` is still a `<you>` placeholder.
  `.mcp.json` defines only context7 + playwright; token-savior, github, and
  postgres come from user-scope config.

## Planned Changes
1. Scaffold package layout and tooling (ruff, pyright strict, pytest).
2. Implement `Settings` and `.env.example`; fail fast on missing required env.
3. Add async DB engine/session + Alembic; create an empty initial migration.
4. Build the LLM client wrapper interface (`chat`, `embed`, `vision`) with one
   concrete provider and a tracing hook.
5. Add FastAPI app factory, `/health` (checks DB + Qdrant reachability).
6. Add `docker-compose.yml` (Postgres + Qdrant) and a `make dev` / README run path.

## Files Expected
- Create: `app/__init__.py`, `app/main.py`, `app/config.py`,
  `app/db/{__init__,base,session}.py`, `app/llm/{__init__,client,base}.py`,
  `app/schemas/__init__.py`, `.env.example`, `docker-compose.yml`,
  `requirements.txt`, `pyproject.toml`, `docs/architecture.md`,
  `tests/test_health.py`
- Modify: `README.md` (setup steps)
- Delete: None unless explicitly approved

**Actually created (additions beyond the list are recorded here):**
- `app/health.py`: `ping_qdrant` helper, so `main.py` stays thin.
- Empty package `__init__.py` files for every `app/` subpackage in the target
  layout (`agents, execution, graph, input, knowledge, memory, response`), plus
  `.gitkeep` placeholders in `frontend/ eval/ scripts/ docker/`. This is layout
  only, with no logic.
- `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`,
  `alembic/versions/0bfa97139592_initial_empty.py`.
- `tests/conftest.py`, `tests/test_config.py`, `tests/test_health.py`,
  `tests/db/test_session.py`, `tests/llm/test_client.py`.
- Modified: `.gitignore` (fixed invalid entries; ignore `venv/`, `.idea/`,
  `.claude/settings.local.json`), `.token-savior-cache.json` (fixed invalid
  JSON; the file is gitignored), `README.md`.

## Architecture Decisions
- LLM/vision/embeddings sit behind one wrapper so providers are swappable and
  every call is traceable.
- **Default LLM provider: Groq** (`openai/gpt-oss-120b`). OpenRouter
  (`qwen/qwen3.8-27b:free`) is selectable via `LLM_PROVIDER=openrouter`. Both
  use LangChain chat models (`ChatGroq` / `ChatOpenRouter`) so Phase 4+
  LangGraph nodes can use them natively. Only `app/llm/client.py` imports a
  provider SDK. Business logic depends on the `LLMClient` Protocol.
- **Tracing** goes through a `Tracer` hook built from Settings. When enabled,
  it enters LangSmith `tracing_context(client=..., project_name=...)`, and each
  call passes `run_name="llm.chat"` + `{provider, model}` metadata via the
  LangChain `config`. That gives one LLM run per call, with inputs, outputs
  and token usage (no wrapper run, no double counting). The client and
  project are passed explicitly, since Settings never exports to
  `os.environ`. It is a no-op when `LANGSMITH_TRACING` is false or the key is
  missing.
- **`DatabaseSettings`** (DB URL only) sits alongside the full `Settings`.
  Alembic uses it, so migrations need only `DATABASE_URL`, not LLM keys.
  libpq `sslmode=` is translated to asyncpg `ssl=`.
- **DB driver: asyncpg.** `Settings` normalizes `postgresql://`, `postgres://`,
  `+psycopg` and `+psycopg2` URLs to `postgresql+asyncpg://`, because psycopg
  async is unreliable on Windows' Proactor event loop.
- **Fail-fast config:** required vars are `DATABASE_URL`, `QDRANT_URL`, and the
  active provider's API key. `hide_input_in_errors=True` keeps secrets out of
  validation errors. `create_app()` resolves Settings at construction, so a
  missing var stops the process at import.
- **No module-level engine/client globals.** The engine, session factory and
  Qdrant client are created in the FastAPI lifespan and stored on `app.state`.
  The app starts even when infra is down, and `/health` reports it (`503`
  degraded).
- Alembic takes its URL only from `DatabaseSettings` (`alembic.ini` has no URL).

## Dependencies
- None (this is the base phase).

## Implementation Notes
- Venv is `venv/` (not `.venv`). Run tools as `venv/Scripts/python.exe -m <tool>`.
- Local dev DB: the host Postgres on 5432, database `codingagent_db` (lowercase).
  The compose Postgres is the alternative, on host port **5433**
  (`coding_agent_db`).
- Postgres MCP role: `claude_codingagent_mcp_readonly` (verified read-only on
  `codingagent_db`).
- Migrations: `venv/Scripts/python.exe -m alembic upgrade head`. New revision:
  `alembic revision -m "..."` (use `--autogenerate` once models exist, from Phase 3).
  Current head: `0bfa97139592`.
- The integration tests (`-m integration`) need the real Postgres + Qdrant and
  are skipped automatically when they're unreachable.
- Provider model IDs go stale. Check `GET /models` on the provider before
  changing defaults.

## Manual Test Cases
### Test 1
Input: `GET /health` with Postgres + Qdrant running via compose.
Expected: `200` with `{"status":"ok","db":"ok","qdrant":"ok"}`.
Actual: ✅ `200 {"status":"ok","db":"ok","qdrant":"ok"}`. Checked live with
`uvicorn app.main:app` + curl, and in `tests/test_health.py::test_health_ok_with_real_infra`
against the local Postgres (`codingagent_db`) and Qdrant (6333).

### Test 2
Input: Start app with a required env var missing.
Expected: App refuses to start with a clear validation error naming the var.
Actual: ✅ `import app.main` without `.env` or required vars exits 1 with
`pydantic_core.ValidationError: 2 validation errors for Settings / database_url
Field required [type=missing]` (and `qdrant_url`). A missing provider key gives
`GROQ_API_KEY is required when LLM_PROVIDER=groq`. Covered by
`tests/test_health.py::test_main_import_fails_fast_without_required_env`.

## Commands Run
```text
venv/Scripts/python.exe -m pip install -r requirements.txt
docker compose config -q
venv/Scripts/python.exe -m alembic init -t async alembic
venv/Scripts/python.exe -m alembic revision -m "initial empty"
venv/Scripts/python.exe -m alembic upgrade head      # -> 0bfa97139592 (head)
venv/Scripts/python.exe -m uvicorn app.main:app --port 8765 ; curl /health
venv/Scripts/python.exe -m pytest -v
venv/Scripts/python.exe -m pyright
venv/Scripts/python.exe -m ruff check . && venv/Scripts/python.exe -m ruff format --check .
```

## Test Results
- `pytest`: **45 passed**, 0 failed, 0 skipped (integration tests ran against
  real infra). Also passes with bogus DATABASE_URL/LLM_MODEL exported (env isolation).
- `code-review` (high): 10 findings. 7 fixed (env isolation, sslmode, list content, tracing double-run, alembic-only-DB, lifespan cleanup, key rewrap); 3 accepted or deferred (see Known Issues).
- `pyright` (strict, `app` + `tests`): 0 errors, 0 warnings.
- `ruff check` / `ruff format --check`: clean.
- Live LLM smoke test (tracing off): Groq `openai/gpt-oss-120b` → `"OK"` with
  token usage.

## Security / Reliability Notes
- Secrets only via env/`Settings`; never commit `.env`.
- `/health` must not leak connection strings or versions of internal services.

## Known Issues
- OpenRouter free model (`qwen/qwen3.8-27b:free`) returned 429 (rate-limited)
  during the smoke test. The key is valid (`/models` returns 200). This is
  free-tier throttling, and Groq is the default.
- `app/main.py` builds `app = create_app()` at import, so importing it (and
  `tests/test_health.py`) needs a valid env or `.env`. That's fine locally; CI
  will need env vars set. Unit tests clear those vars themselves (autouse
  fixture), so exported CI vars don't leak into them. (Code-review finding,
  accepted.)
- `.env` is resolved relative to the working directory (the pydantic-settings
  default). Run uvicorn, alembic and pytest from the repo root. (Code-review
  finding, accepted.)
- `requirements.txt` is unpinned and includes heavy packages for later phases
  (torch, sentence-transformers, fastembed) plus dev tools. It was kept
  intentionally per the owner. Follow-up: pin versions or add a lock file, and
  split out `requirements-dev.txt`. (Code-review finding, deferred.)
- Embeddings and vision are stubs (`NotImplementedError`) until Phases 5 and 2.
- The local Qdrant uses an API key over plain HTTP, so qdrant-client emits an
  "insecure connection" warning. Harmless on localhost.
- `docs/architecture.md` is a placeholder. The full source diagram/spec is
  pending from the project owner.
- The real `.env` has `LANGSMITH_PROJECT=deep-research-app-agent`. The owner
  should set it to `adaptive-coding-agent` so traces don't mix with the other
  project.

## Git Commit
Not created yet.

## Next Phase
- PHASE-02 — Input Understanding & Multimodal Intent Classification.
