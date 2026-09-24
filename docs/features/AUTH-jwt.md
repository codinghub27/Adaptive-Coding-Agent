# FEATURE — JWT Access + Refresh Authentication

## Status
Done (2026-09-24), commit `42fb6a9`

## Goal
Replace the caller-supplied, unauthenticated `user_id` (PHASE-04 Known Issue)
with real authentication: password login issuing a short-lived stateless access
JWT plus a long-lived refresh token that is persisted (hashed) so it can be
validated and revoked. Every learner-scoped read/write is bound to the
authenticated user.

This is a cross-cutting feature, not a numbered phase. It is tracked here in
the same template as `docs/phases/`.

## Scope
- `users.password_hash` (bcrypt) + a way to create a user with a password.
- `refresh_tokens` table + persistence helpers (`app/db/auth.py`): create,
  get_valid, revoke, revoke_all_for_user. Only a hash of the token is stored.
- `app/auth/security.py` — password hash/verify; create/decode access and
  refresh JWTs (HS256) with explicit expiry handling.
- `app/auth/deps.py` — OAuth2 bearer scheme + `get_current_user` (401 on
  missing/invalid/expired access token).
- `app/auth/routes.py` — `POST /auth/login`, `POST /auth/refresh`,
  `POST /auth/logout`.
- Protect `/chat` and `/understand`; `/health` stays public. `/chat` takes the
  user from the token, not from a form field.

Requirements (locked by the owner):
- Access token: stateless JWT, **45 min**; refresh token: **7 days**; HS256.
  Secret + expiries come from `Settings`, never hardcoded.
- Expired access token → protected routes return 401 until the user refreshes
  or logs in again.
- Refresh tokens persisted as a **hash**, with `user_id`, `expires_at`,
  `revoked`; logout revokes.
- Passwords stored as **bcrypt** hashes.

## Out of Scope
- OAuth/social login, email verification/reset flows, roles/permissions,
  rate limiting / lockout, access-token denylist.
- Future phases.

## Current Implementation
Inspected 2026-09-24 (HEAD `de33108`, Phases 01–04 Done). Via token-savior,
postgres MCP (read-only) and the installed packages.

- **User model** — `app/db/models/user.py::User`: `id` (UUID pk, default
  uuid4), `handle` (String(64), unique, not null), `created_at`. **No
  password column.** Live table `users` (postgres MCP, `codingagent_db`, head
  `c708bafc104e`) matches: `id`, `handle`, `created_at`.
- **No user-creation API** — tests create `User(handle=...)` rows directly
  (`tests/memory/conftest.py::_make_user`).
- **DB** — `app/db/base.py::Base(DeclarativeBase)` with naming convention;
  models registered via `app/db/models/__init__.py` (imported by
  `alembic/env.py` for autogenerate). `get_session(request)` yields a
  request-scoped `AsyncSession` and rolls back on exception; callers commit.
- **Settings** — `app/config.py::Settings(BaseSettings)`, `.env` file, secrets
  typed `SecretStr` (`groq_api_key`, `openrouter_api_key`, `qdrant_api_key`,
  `langsmith_api_key`), `hide_input_in_errors=True`; cached `get_settings()`.
  Settings are resolved in `create_app(settings=None)` and put on
  `app.state.settings`. No auth settings.
- **App** — `create_app()` lifespan puts `settings`, `engine`,
  `session_factory`, `qdrant`, `llm` on `app.state`; routers `/health`,
  `/understand` (`app/input/api.py`), `/chat` (`app/graph/api.py`).
  `/chat` currently accepts `user_id` and `conversation_id` as form fields.
  Conversation ownership is already enforced by Phase 03
  (`get_owned_conversation`).
- **Existing API tests** — `tests/input/test_understand_api.py`,
  `tests/graph/test_chat_api.py`, `tests/test_health.py` build the app and
  override `get_llm` / `get_session`; they will need an auth override.
- **Packages** — `python-jose 3.5.0` and `bcrypt 5.0.0` are installed and
  listed in `requirements.txt`, but **nothing imports them**. `passlib`,
  `PyJWT` and `pwdlib` are **not** installed. `bcrypt` 5 rejects passwords
  longer than 72 bytes with `ValueError` (checked locally).
- **Docs** — FastAPI's current security tutorial (context7) uses **PyJWT**
  (`jwt.encode/decode`, `InvalidTokenError`, `ExpiredSignatureError`) and
  `OAuth2PasswordBearer(tokenUrl=...)` + `OAuth2PasswordRequestForm`, raising
  401 with `WWW-Authenticate: Bearer`.

## Planned Changes
Delivered as packets P1–P8 (planner + CodeExecutor):
1. P1 settings + PyJWT + `users.password_hash` + bcrypt passwords + `create_user`.
2. P2 `refresh_tokens` table + persistence helpers (hash only).
3. P3 JWT issue/decode (HS256, required claims, typed errors).
4. P4 `get_current_user` (token → user → live session).
5. P5 `/auth/register|login|refresh|logout` (+ fix: `revoked_reason`).
6. P6 protect `/chat` + `/understand`; identity from the token only.
7. P7 owner's four manual cases end-to-end.
8. P8 code-review fixes (races, grace window, async bcrypt, no idle txn,
   no secrets in 422s, handle normalization, leeway, cleanup).

## Files Expected
- Create: `app/auth/{__init__,security,deps,routes}.py`, `app/db/auth.py`,
  `app/db/models/refresh_token.py`, `app/schemas/auth.py`,
  2 Alembic revisions, `tests/auth/*`
- Modify: `app/config.py`, `app/db/models/{__init__,user}.py`, `app/main.py`,
  `app/graph/api.py`, `app/input/api.py`, `requirements.txt`, existing API
  tests (auth override), `.env.example` note (owner)
- Delete: None unless explicitly approved

**Actually created / modified:**
- Created: `app/auth/{__init__,security,deps,routes,timeutil}.py`,
  `app/db/auth.py`, `app/db/models/refresh_token.py`, `app/schemas/auth.py`,
  `alembic/versions/5aef92ee2f6b_user_password_hash.py`,
  `alembic/versions/b6d2249030ca_refresh_tokens.py`,
  `tests/auth/{__init__,conftest,helpers,test_passwords,test_users_db,test_refresh_tokens_db,test_tokens,test_deps,test_routes,test_protected_routes,test_auth_manual,test_concurrency,test_validation_errors,test_timeutil}.py`,
  this doc.
- Modified: `app/config.py` (JWT settings), `app/db/models/{__init__,user}.py`,
  `app/main.py` (auth router + `/auth/*` 422 handler), `app/graph/api.py`
  (`/chat` auth, `user_id` form field removed), `app/input/api.py`
  (`/understand` auth), `requirements.txt` (`python-jose[cryptography]` →
  `pyjwt`), `alembic/script.py.mako` (project code style),
  `tests/conftest.py` (test JWT secret + env isolation),
  `tests/test_config.py`, `tests/graph/test_chat_api.py`,
  `tests/input/test_understand_api.py`, `tests/input/test_manual_live.py`
  (auth overrides).
- Additions beyond the plan: `app/auth/timeutil.py` (single `resolve_now`),
  `tests/auth/helpers.py` (shared API-test helpers), the `/auth/*`
  validation-error handler, `alembic/script.py.mako` restyle.
- `.env.example` not edited (planner cannot read `.env*`); owner adds
  `JWT_SECRET_KEY=` (≥ 32 chars) — done locally by the owner.

## Architecture Decisions
Owner-approved 2026-09-24:
- **A. Libraries:** PyJWT (FastAPI's current recommendation) + the `bcrypt`
  package used directly. `python-jose` (unused) removed from requirements;
  `passlib` not used (unmaintained, incompatible with bcrypt ≥ 4.1).
  Passwords must be 8–72 UTF-8 bytes (bcrypt 5 rejects > 72 bytes; we reject
  explicitly rather than truncate).
- **B. Registration:** public `POST /auth/register` (handle + password) → 201;
  duplicate handle → 409.
- **C. Refresh rotation + reuse detection:** `/auth/refresh` revokes the
  presented refresh token and returns a new access **and** refresh token. A
  revoked refresh token presented again ⇒ `revoke_all_for_user` + 401.
  **Refined during P5 verification:** only a token whose `revoked_reason` is
  `rotated` triggers reuse detection. A token revoked by `logout` (or already
  by `reuse_detected`) is simply invalid: plain 401, no side effects.
  Otherwise, presenting a logged-out token would revoke the user's other,
  innocent sessions (caught by
  `test_logout_revokes_session_blocking_refresh_and_access`).
  `refresh_tokens.revoked_reason` ∈ {rotated, logout, reuse_detected}, enforced
  by a CHECK constraint.
- **D. Immediate access-token revocation (session binding):** every refresh
  token row carries a `session_id` (stable across rotation); access tokens
  carry it as `sid`. `get_current_user` verifies signature/exp/type (no DB),
  then one indexed query: the user exists and the session has a non-revoked
  refresh token. Logout revokes the session ⇒ its access tokens stop working
  immediately; reuse detection revokes all sessions ⇒ all access tokens die.
  Trade-off: one DB lookup per authenticated request.
- **Secret:** `JWT_SECRET_KEY` is a required `SecretStr`, ≥ 32 chars; the
  algorithm is fixed to HS256 in code (not configurable to `none`/RS*). A
  proposed value of `"HS256"` was rejected — it is the algorithm name, not a
  secret, and would let anyone forge tokens.
- Refresh tokens are stored as **SHA-256** hex digests (high-entropy random
  JWTs — a fast hash is safe and enables indexed lookup); passwords use
  **bcrypt** (slow, salted).

## Dependencies
- PHASE-01 (Settings, DB session, app factory), PHASE-03 (User, memory
  scoping), PHASE-04 (`/chat`).

## Implementation Notes
- **Settings** (`app/config.py`): `jwt_secret_key` (SecretStr, required,
  ≥ 32), `jwt_algorithm` = HS256 (Literal), `access_token_expire_minutes` = 45,
  `refresh_token_expire_days` = 7, `refresh_reuse_grace_seconds` = 10,
  `jwt_leeway_seconds` = 10.
- **Claims:** `sub` (user id), `sid` (session id), `jti`, `type`
  (`access`|`refresh`), `iat`, `exp` — all required on decode.
- **Endpoints:** `POST /auth/register` (JSON) → 201 / 409 / 422;
  `POST /auth/login` (OAuth2 password form, `username` = handle) → `TokenPair
  {access_token, refresh_token, token_type, expires_in}`;
  `POST /auth/refresh` (JSON) → new `TokenPair`; `POST /auth/logout` (JSON) → 204.
  Token responses send `Cache-Control: no-store`, `Pragma: no-cache`.
- **401 details:** `not authenticated`, `token expired`, `invalid credentials`,
  `session revoked`; refresh: `refresh token expired`, `invalid refresh token`,
  `refresh token already used` (grace window), `refresh token reuse detected`.
- **Refresh flow:** decode → `lock_user(sub)` (`SELECT … FOR UPDATE` on
  `users`, serializes every session mutation for that user) → token row
  `FOR UPDATE` → claim/row match → revoked? (`rotated` within grace ⇒ "already
  used"; `rotated` outside grace ⇒ revoke all + commit + "reuse detected";
  other reasons ⇒ "invalid") → rotate by row id (`reason="rotated"`), new pair
  with the same `sid`, commit.
- **Logout:** hash lookup → `lock_user` → `revoke_session(reason="logout")` →
  commit; always 204.
- **`get_current_user`:** decode access → user exists → `session_is_active`
  (one indexed EXISTS) → commits to end its read-only transaction (no pooled
  connection held "idle in transaction" during LLM calls).
- **Protected:** `/chat`, `/understand`. **Public:** `/health`, `/auth/*`,
  OpenAPI docs.

## Manual Test Cases
### Test 1
Input: Valid login.
Expected: access + refresh returned; access decodes with ~45 min `exp`.
Actual: ✅
```
ACTUAL: status=200 token_type=bearer expires_in=2700 access exp-iat (min)=45.0 refresh exp-iat (days)=7 sid equal=True db row: token_hash matches=True revoked=False
```
**Pass.** (`tests/auth/test_auth_manual.py`)

### Test 2
Input: Protected route with valid / expired / missing access token.
Expected: 200 / 401 / 401.
Actual: ✅ (real `/chat`; the expired token is fabricated with the same user +
session so only expiry differs)
```
ACTUAL: valid status=200 route=debug | expired status=401 detail=token expired | missing status=401 detail=not authenticated www-authenticate=Bearer
```
**Pass.**

### Test 3
Input: Refresh with a valid persisted refresh token; with a revoked or expired one.
Expected: new access token / 401.
Actual: ✅ (`refresh_reuse_grace_seconds=0` so the reuse is genuine reuse
detection, not the duplicate-use grace path)
```
ACTUAL: (refresh_reuse_grace_seconds=0, so the reuse below is genuine reuse detection, not the grace-window duplicate-use path) rotate status=200 new access differs=True new access /chat status=200 | reuse status=401 detail=refresh token reuse detected (revoked all of the user's sessions -- rotated session's /chat now status=401 detail=session revoked) | fresh-login expired-refresh status=401 detail=refresh token expired
```
**Pass.**

### Test 4
Input: Logout, then refresh with the same refresh token.
Expected: 401.
Actual: ✅
```
ACTUAL: logout status=204 | refresh-with-logged-out-token status=401 detail=invalid refresh token (plain 401, no reuse side effect) | /chat with revoked session status=401 detail=session revoked | /chat with a second, independent login session status=200
```
**Pass.**

Live smoke (real Postgres/Qdrant, uvicorn :8769, owner's real `.env`):
register 201 → login 200 (`cache-control: no-store`) → `/chat` without token
401 → `/chat` with token 200 (`route=debug`, `llm_calls=0`, `errors=[]`) →
wrong password 401 → 422 body contains the password: 0 matches → logout 204 →
`/chat` with the same access token 401 `session revoked` → refresh with the
logged-out token 401 `invalid refresh token` → server log contains the refresh
token: 0 matches. The smoke user was deleted afterwards.

## Commands Run
```text
venv/Scripts/pip install pyjwt                                   # 2.15.0 (ships py.typed)
venv/Scripts/alembic revision --autogenerate -m "user password hash"   # 5aef92ee2f6b
venv/Scripts/alembic revision --autogenerate -m "refresh tokens"       # b6d2249030ca
venv/Scripts/alembic upgrade head && venv/Scripts/alembic downgrade -2 && venv/Scripts/alembic upgrade head
venv/Scripts/alembic check                                        # No new upgrade operations detected
venv/Scripts/python.exe -m pyright                                # strict, after every packet
venv/Scripts/python.exe -m ruff check . && venv/Scripts/python.exe -m ruff format --check .
venv/Scripts/python.exe -m pytest -q -p no:warnings
venv/Scripts/python.exe -m pytest tests/auth/test_auth_manual.py -s -q -p no:warnings
venv/Scripts/python.exe -m pytest tests/auth/test_concurrency.py -q   # repeated runs
venv/Scripts/python.exe -m uvicorn app.main:app --port 8769 ; curl …/auth/* …/chat
```

## Test Results
- `pytest -q`: **755 passed, 2 skipped** (the 2 skips are the opt-in live-LLM
  tests). Auth tests: passwords, users, refresh-token persistence, tokens
  (alg=none / HS512 / tampered / wrong type / each missing claim / leeway),
  deps, routes, protected routes, manual cases, concurrency, 422 redaction,
  timeutil.
- `pyright` strict: 0 errors. `ruff check` / `format --check`: clean.
- Migrations: `downgrade -2` / `upgrade head` clean; `alembic check` no drift.
  Live schema (postgres MCP): `users.password_hash varchar(128) NULL`;
  `refresh_tokens` with unique `token_hash`, FK `ON DELETE CASCADE`, CHECK
  `revoked_reason IN (rotated, logout, reuse_detected)`, composite index
  `(user_id, session_id)` only.
- DB after the full suite: 0 users, 0 refresh rows, 0 `idle in transaction`
  connections (no test leakage).
- Secret handling: no logging/print in auth code; the JWT secret is read only
  at `jwt.encode`/`jwt.decode`; tokens never stored raw (row-scan test).
- `code-review` (high, auth-focused): 10 findings, all addressed in P8:
  1–2. logout / reuse revocation could miss a concurrently rotated token
       (READ COMMITTED) → per-user row lock (`lock_user`) before reading/
       mutating; proven by spy tests + a real two-connection test that fails
       without the lock;
  3. duplicate refresh (two tabs / retry) logged the user out everywhere →
     `refresh_reuse_grace_seconds` window;
  4. bcrypt blocked the event loop → `anyio.to_thread` wrappers;
  5. `get_current_user` held a connection idle-in-transaction → commits;
  6. passwords/refresh tokens echoed in 422s/reprs → `repr=False` + `/auth/*`
     422 redaction handler;
  7. register stripped handles but login didn't → shared `normalize_handle`;
  8. no clock-skew leeway → `jwt_leeway_seconds`;
  9. refresh re-hashed/re-queried the locked row → revoke by id, dead branch removed;
  10. two `_resolve_now` with different rules → one `resolve_now` (rejects
      naive). `get_valid_refresh_token` kept (owner-required helper).
- Found during P5 verification (before review): a logged-out refresh token
  triggered reuse detection and revoked the user's other sessions → fixed with
  `revoked_reason` (Decision C refinement).

## Security / Reliability Notes
- Never store or log raw refresh tokens, passwords, or the JWT secret.
- Fixed algorithm list on decode (no `alg` confusion); `type` claim prevents
  using a refresh token as an access token.

## Known Issues
- No rate limiting / account lockout on `/auth/login` and `/auth/register`
  (out of scope); bcrypt now runs off the event loop but can still be
  CPU-exhausted by volume.
- One extra DB query per authenticated request (session check, decision D).
- Expired / revoked `refresh_tokens` rows are never purged (needs a periodic
  cleanup job).
- Handles are case-sensitive (`Alice` ≠ `alice`); surrounding whitespace is
  normalized.
- Duplicate refreshes inside the grace window get a 401 ("already used"); the
  losing tab must log in again or use the winning tab's tokens (no successor
  hand-off).
- Access tokens are revoked via the session check, so a token is only as fresh
  as the DB; HS256 means every service that verifies tokens holds the secret.
- `.env.example` still lacks a `JWT_SECRET_KEY=` line (planner cannot edit
  `.env*`).

## Git Commit
`42fb6a9` — feat: JWT access + refresh auth with db-backed refresh tokens
