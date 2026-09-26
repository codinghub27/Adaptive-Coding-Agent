# FEATURE — Web UI Integration (replaces the Phase 08 Streamlit frontend)

## Status
Done — verified end to end against Postgres + Qdrant + a live LLM provider.

## Goal
Wire the Figma-designed web frontend (`frontend/Adaptive Coding Agent UI/`) to
the existing FastAPI + LangGraph backend, without changing the agent's graph
logic or the frontend's visual design. The Streamlit UI shipped in Phase 08
(`frontend/app.py`, `frontend/api_client.py`, `frontend/components/`) is removed;
the web UI moves to `frontend/` directly.

---

## PART A — Agent flow map

### Request path (`POST /chat`, `POST /chat/stream`)

```
HTTP multipart (text?, language?, image?, conversation_id?, topic?)
  |  auth: get_current_user  (app/auth/deps.py) -- Bearer access token -> AuthUser
  |  validation: _build_raw_input (app/graph/api.py) -> RawInput  (untrusted data)
  v
run_graph / stream_graph  (app/graph/build.py)   context = GraphContext
  v
START
 -> understand_input      "Reading your input"          normalize + vision/OCR -> StructuredInput
 -> classify_intent       "Working out what you need"   -> IntentResult (intent + confidence)
 -> load_learner_profile  "Recalling how you learn"     -> LearnerProfileView + recent_context[MessageView]
 -> plan_teaching         "Planning how to help"        -> TeachingPlan (difficulty, assistance_level,
 |                                                         solution_strategy, topic, skill_level, ...)
 -> retrieve_knowledge    "Looking up references"       -> retrieved_context[RetrievalHit]  (fail-soft)
 -> route                 "Choosing an approach"        route in {dsa, debug, explain, clarify}
 |- dsa_agent             "Working through the problem" hint ladder (hint_engine.next_hint)
 |- debug_agent           "Debugging your code"
 |- explain_agent         "Reading your code"
 \- clarify               "Asking for a bit more"  ----------------+
 -> execute_code          "Running your code in the sandbox"       |
 -> verify                "Checking the results"     -> Verdict    |
 -> final_response        "Writing your answer"  <-----------------+
        app/response/generate.py -> GeneratedResponse {text, sections[], assistance_level,
        hint_level, hint_ceiling, more_help_available, reveals_code, next_steps, citations}
 -> update_learner_model  "Updating what I know about you"   persists LearningEvents,
                                                             EWMA profile update, hint_progress
 -> END
```

Stage labels come from a **fixed server-side table**
(`app/graph/stages.py::STAGE_LABELS`, 14 entries + `DEFAULT_STAGE_LABEL`). No
user text or model output may ride out on a progress event — this is a security
invariant, not a convenience.

### Streaming surface that exists today
`POST /chat/stream` (`app/graph/api.py::chat_stream`) returns
`text/event-stream` and emits:

| SSE event | data | when |
|---|---|---|
| `stage` | `{node, label}` | once per graph node entered |
| `done`  | the full `ChatResponse` JSON | exactly once, terminal |
| `error` | `{detail: "the request could not be completed"}` | terminal, on any failure |

Exactly one terminal frame is always emitted. The stream owns its own DB
session (`_get_session_factory`) and commits inside the generator.
**There is no token-level streaming today** — `final_response` is synchronous
and the answer arrives whole inside the `done` frame.

### `ChatResponse` shape
`{response, route, intent, plan, verification, events, events_persisted,
errors, llm_calls, generated: GeneratedResponse|null, conversation_id}`

### Hint ladder (server-enforced)
- Progress is stored per `(user_id, conversation_id, topic)` in `hint_progress`
  (`app/db/models/hint_progress.py`), keyed by `slug_tag(plan.topic)`.
- `hint_engine.next_hint` advances **at most one rung per turn**, capped by
  `MAX_HINT_LEVEL_FOR_ASSISTANCE[plan.assistance_level]`; `reveals_code` only at
  `L5_PARTIAL`/`L6_FULL`.
- `GeneratedResponse` validates the invariant: a code-bearing section requires
  `reveals_code`, and `reveals_code` is only legal at assistance level
  `partial`/`full`.
- **Consequence for the UI:** "next hint" is *not* a client-side counter. It is
  another `/chat` turn in the same conversation on the same topic. The UI must
  render `generated.hint_level` / `hint_ceiling` / `more_help_available` from the
  server, never advance a local level.

### Auth
- `access` token: JWT, **45 min** (`access_token_expire_minutes=45`).
- `refresh` token: **7 days**, hashed + stored in `refresh_tokens`, rotated on
  every use, with reuse detection (reuse outside the grace window revokes every
  session for that user).
- `get_current_user` 401s with `WWW-Authenticate: Bearer` and one of:
  `not authenticated` / `token expired` / `invalid credentials` /
  `session revoked`.
- Identity is **username/handle**, not email — `users.handle` (<= 64 chars).
  There is no email column and no display-name column on `User`.

### Data models the UI binds to
| Model | Fields |
|---|---|
| `User` | `id`, `handle`, `created_at`, `password_hash` |
| `Conversation` | `id`, `user_id`, `title (<=200, nullable)`, `created_at` |
| `Message` | `id`, `conversation_id`, `user_id`, `seq`, `role (user\|assistant)`, `content`, `intent`, `created_at` |
| `HintProgress` | `user_id`, `conversation_id`, `topic`, `level`, `solved` |
| `LearnerProfileView` | `language`, `skill_levels{str->0..1}`, `learning_preferences{str->bool}`, `common_errors[str]` |

Conversation store functions that exist: `start_conversation`,
`get_owned_conversation`, `add_turn`, `get_recent_context(limit)`.
**No list / rename / delete / full-history function exists.**

---

## PART A — Endpoint inventory

### Exists
| Method | Path | Auth | Notes |
|---|---|---|---|
| `POST` | `/auth/register` | public | body `{username, password}` -> 201 `{id, username}`; 409 taken |
| `POST` | `/auth/login` | public | JSON **or** form -> `{access_token, refresh_token, expires_in}` |
| `POST` | `/auth/refresh` | public | body `{refresh_token}` -> new pair (rotation + reuse detection) |
| `POST` | `/auth/logout` | public | body `{refresh_token}` -> 204, idempotent |
| `POST` | `/understand` | Bearer | normalize + intent only (not needed by the UI) |
| `POST` | `/chat` | Bearer | multipart; one full turn -> `ChatResponse` |
| `POST` | `/chat/stream` | Bearer | multipart; SSE `stage`* -> `done`/`error` |
| `POST` | `/conversations` | Bearer | body `{title?}` -> `{conversation_id, title}` |
| `GET` | `/profile` | Bearer | -> `LearnerProfileView` |
| `GET` | `/health` | public | `{status, db, qdrant}` |

### Missing (needed by the UI)
| Method | Path | Why the UI needs it |
|---|---|---|
| `GET` | `/auth/me` | sidebar user chip, route guard, avatar initials |
| `GET` | `/conversations` | sidebar conversation list (+ last-activity ordering for Today/Yesterday grouping) |
| `PATCH` | `/conversations/{id}` | rename from the sidebar context menu |
| `DELETE` | `/conversations/{id}` | delete from the sidebar context menu |
| `GET` | `/conversations/{id}/messages` | history reload on hard refresh (the invariant: history comes from the backend, not localStorage) |

### Also missing / to decide in PART B
- **CORS**: `cors_origins` defaults to `["http://localhost:8501"]` (Streamlit) and
  `allow_methods=["GET","POST"]`. The web UI dev origin and `PATCH`/`DELETE` are
  both currently blocked.
- **Static serving**: no `StaticFiles` mount for a built frontend.
- **Token streaming**: only stage-level events exist; a token-by-token final
  answer needs `final_response` to stream, or a documented decision to render the
  final answer from the `done` frame with a client-side typewriter.
- **Assistant-turn persistence**: `/chat` persists turns only when a
  `conversation_id` is supplied — the UI must always create and pass one.

---

## PART A — Notes and mismatches found
1. **Email vs handle.** The Figma UI's login/register forms collect *email* and
   *name*; the backend authenticates on `handle` only. Resolution needed (map
   email -> handle, or keep the field visually identical and treat it as the
   username). Design must not change; only the binding.
2. **The "Challenge"/"Balanced" mode pill and "Hint mode" toggle** are pure UI
   state in the mock. The backend derives `assistance_level` from the plan; the
   closest honest binding is the `topic` form field plus letting the planner
   decide. Needs a decision in PART B.
3. **Demo workspace button** has no backend equivalent.
4. The UI folder also contains an unused React scaffold (`src/App.tsx` is an
   empty stub, `package.json` names React + Tailwind). The *actual* design is
   vanilla HTML/CSS/JS across `login.html`, `register.html`, `chat.html`, built
   by Vite as a 4-entry multi-page app.

---

## PART B — Locked decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Stack kept as-is**: vanilla HTML/CSS/JS, Vite 4-entry MPA (`index/login/register/chat`). The unused React scaffold (`src/`) stays untouched. | "Do not change the stack or the design." |
| D2 | **Email -> username.** The `Email address` field on login becomes `Username` (same markup, classes, and layout; `type="text"`, `autocomplete="username"`). Backend authenticates on `users.handle` only. | Owner decision; DB stores username + password. |
| D3 | **Register collects username + password only** (owner decision, 2026-09-26). The `Your name` and `Email address` fields are removed; the first field becomes `Username`, followed by `Create password` + `Confirm password`. Same field markup, classes and layout -- only the field set shrinks. Avatar initials derive from the username. | Owner decision; matches `users` (handle + password_hash) exactly. |
| D4 | **No token streaming.** The client renders the final answer from the SSE `done` frame with a typewriter, so `token` events stay in the UI's event contract while the graph is untouched. | Owner decision (recommended option). |
| D5 | **Hint ladder is server state.** The UI renders `generated.hint_level` / `hint_ceiling` / `more_help_available` and "Get next hint" sends a **new `/chat` turn** on the same conversation+topic. No client-side level counter, ever. | Owner decision: implement the hint model as the agent is designed. |
| D6 | **`Hint mode` / `Challenge` map to an `assistance_cap` form field** on `/chat` + `/chat/stream`, which can only **lower** the planner's `assistance_level` (by `ASSISTANCE_ORDER`), never raise it. `Balanced` sends no cap. | The only honest binding; monotonically safe ("teach, don't dump" can only get stronger). |
| D7 | **Same-origin everywhere.** Vite dev proxies `/auth`, `/chat`, `/conversations`, `/profile`, `/understand`, `/health` to the API; production serves `frontend/dist` from FastAPI `StaticFiles`. | Makes the httpOnly refresh cookie work with no CORS credential juggling. |
| D8 | **Refresh token in an httpOnly cookie**, access token in memory only. Cookie: `HttpOnly`, `SameSite=Strict`, `Path=/auth`, `Secure` off only for localhost. Existing body-based `/auth/refresh` + `/auth/logout` keep working (body wins, cookie is the fallback), so no existing test changes. | Integration contract; XSS-safe. |
| D9 | **`Enter demo workspace`** registers/logs in a per-browser demo account (`demo_<rand>`), a real backend user. | Keeps the button and its design; no fake auth path. |
| D10 | **Conversation auto-title** is a client `PATCH` after the first user turn of an untitled conversation. | No change to graph or memory-store behaviour. |

## PART B — Work packets

**P0 — Repo surgery.** Delete `frontend/app.py`, `frontend/api_client.py`,
`frontend/components/`, `frontend/__init__.py`, `__pycache__`; drop `streamlit`
from `requirements.txt`; drop `"frontend"` from pyright `include` in
`pyproject.toml` (no Python left there); move
`frontend/Adaptive Coding Agent UI/*` (incl. dotfiles, `.figma/`) to `frontend/`;
update `README.md` + a pointer note in `docs/phases/PHASE-08-*.md`.
No test references the Streamlit frontend (verified: `grep -rl frontend tests/`
is empty), so the 1270-test baseline must stay 1270.

**P1 — Backend gaps** (`app/memory/conversation.py`, `app/memory/api.py`,
`app/schemas/conversation.py`, `app/auth/routes.py`, `app/main.py`,
`app/config.py`, `app/graph/api.py`, `app/graph/nodes.py`):
- `GET /auth/me` -> `{id, username, created_at}`.
- `GET /conversations` -> `[{id, title, created_at, updated_at, message_count}]`,
  newest activity first (`updated_at` = max message `created_at`, else
  `created_at`).
- `PATCH /conversations/{id}` `{title}` -> 200 / 404 (ownership-scoped).
- `DELETE /conversations/{id}` -> 204 / 404 (ownership-scoped, cascades).
- `GET /conversations/{id}/messages?limit&after_seq` -> `[MessageView]` ascending.
- CORS: `allow_methods` += `PATCH`, `DELETE`, `OPTIONS`; `allow_credentials=True`;
  default `cors_origins` -> the Vite dev origin.
- httpOnly refresh cookie, additive (D8).
- `StaticFiles` mount of `frontend/dist` at `/`, only when the directory exists,
  registered after every API router.
- Optional `assistance_cap` form field (D6), clamped in `plan_teaching`.
- No schema change -> no Alembic migration.

**P2 — Frontend API layer.** Rewrite `frontend/js/api.js` keeping **every exported
method name and normalized return shape** (`login, register, refresh, logout,
getMe, getConversations, createConversation, renameConversation,
deleteConversation, getMessages, sendMessage, streamChat, getProfile`) so
`chat.js` / `sessions.js` / `auth.js` bind unchanged where possible. Adds a
`request()` wrapper: Bearer header, single-flight 401 -> refresh -> retry once,
refresh failure -> `location.replace("/login.html")`. Access token in a module
variable only. `updateMessage` disappears from the ladder path (D5).

**P3 — Auth screens.** Field relabel (D2), validation switched to username rules
(1-64 chars), register -> `/auth/register` -> redirect to login with the username
prefilled, login -> `/auth/login` -> `/chat.html`, demo button (D9), route guard
on `chat.html` via `getMe()`.

**P4 — Sessions.** `sessions.js` on the real endpoints; `Today / Yesterday /
Previous 7 days / Older` computed client-side from `updated_at`; history loaded
from `GET /conversations/{id}/messages` so a hard refresh reloads from the
backend; `localStorage` keeps only the active-conversation id and the theme.

**P5 — Chat + input.** `sendMessage` posts real multipart (`text`, `image` as the
actual `File`, `conversation_id`, `topic`, `assistance_cap`); backend
`MessageView` / `GeneratedResponse` normalized into the UI's message shape;
markdown + code highlighting untouched; title `PATCH` on the first turn (D10).

**P6 — Streaming.** `streaming.js` becomes a real SSE reader over
`POST /chat/stream` (fetch + `ReadableStream`, Bearer, multipart body), emitting
the UI's existing event contract: server `stage` -> `step` (appended
dynamically, same `.work-step` markup), `done` -> `content_start` + typewriter
`token`s over `generated.text` -> `final`; `error` -> throw. Mid-stream 401 ->
refresh + one retry. After close: `GET /profile` + `GET /conversations` refresh
in the background.

**P7 — Hint ladder + profile view.**
- Hint card bound to the server fields; "Get next hint ->" sends a new turn;
  at the ceiling it shows the final-guidance state. Code renders only when
  `generated.reveals_code` is true.
- **New feature:** clicking the sidebar user chip opens a detailed **learning
  profile modal** (existing `showModal` chrome + profile-panel classes and
  tokens): username, member since, overall fluency, the full `skill_levels` map,
  `learning_preferences`, `common_errors`, session count, and `Sign out` moved
  inside it.

**P8 — Tests + verification.** pytest for the new endpoints, pyright strict,
ruff; Playwright MCP for the end-to-end matrix in Step 4; `code-review` before
commit.

## Files Expected

**Removed (Streamlit, superseded)**
`frontend/app.py`, `frontend/api_client.py`, `frontend/__init__.py`,
`frontend/components/` (4 modules), `frontend/.gitkeep`, the `streamlit` pin in
`requirements.txt`, and `"frontend"` from pyright's `include` (no Python remains
under `frontend/`). No test referenced any of it, so the suite baseline was
unaffected by the removal.

**Moved** — every file of `frontend/Adaptive Coding Agent UI/` up into
`frontend/` (pages, `css/`, `js/`, `src/`, `.figma/`, Vite + TS config, dotfiles).

**Backend added/changed**
| File | Change |
|---|---|
| `app/schemas/conversation.py` | `ConversationSummary`, `ConversationRenameRequest` |
| `app/schemas/auth.py` | `MeResponse` |
| `app/memory/conversation.py` | `list_conversations`, `rename_conversation`, `delete_conversation`, `list_messages` |
| `app/memory/api.py` | `GET /conversations`, `PATCH`/`DELETE /conversations/{id}`, `GET /conversations/{id}/messages` |
| `app/auth/routes.py` | `GET /auth/me`; httpOnly refresh cookie set on login/refresh, read as a fallback by refresh/logout, cleared on logout |
| `app/config.py` | `cors_origins` default → Vite origins; `frontend_dist_dir`; `refresh_cookie_secure` |
| `app/main.py` | CORS `PATCH`/`DELETE`/`OPTIONS` + credentials (off if a wildcard origin); conditional `StaticFiles` mount of `frontend/dist`, registered last |
| `app/graph/state.py` | `RawInput.assistance_cap` |
| `app/agents/planner.py` | `clamp_assistance` (monotonic: can only lower assistance) |
| `app/graph/nodes.py` | `plan_teaching` applies the cap; `DEFAULT_HINT_TOPIC` + `_hint_topic_key` so the hint ladder persists when the planner infers no topic |
| `app/graph/api.py` | `assistance_cap` form field on `/chat` and `/chat/stream`, validated in the shared `_build_raw_input` |

**Frontend rewritten** — `js/api.js` (real client), `js/streaming.js` (SSE reader),
`js/auth.js`, `js/chat.js`, parts of `js/ui.js`, `login.html`, `register.html`,
`index.html`, `vite.config.ts` (dev proxy), plus the new `css/chat-scale.css`
(density/type scale) and additive profile-modal rules in `css/chat.css`.

## Architecture Decisions
See "PART B — Locked decisions" above. Amendments made during implementation:

- **A1 — Demo workspace removed entirely** (owner, 2026-09-26), superseding D9.
  The `or explore instantly` divider went with the button it introduced; no
  other element of the login page changed.
- **A2 — The hint-ladder follow-up carries the topic.** Hint progress is keyed
  `(user, conversation, topic)`, so "Get next hint" posts the same `topic` the
  turn reported, otherwise the server starts a fresh ladder. The card carries it
  in `data-topic`.
- **A3 — The answer body excludes the hint section** when a hint card is shown,
  so the rung is not printed twice.
- **A4 — Headings.** The response generator emits `##`; the renderer only knew
  `###`. All heading levels now render as the design's single heading size.

## Streaming event contract

Server (`POST /chat/stream`, `text/event-stream`), unchanged by this work:

| event | data |
|---|---|
| `stage` | `{node, label}` — label from the fixed `STAGE_LABELS` table only |
| `done` | the full `ChatResponse` JSON |
| `error` | `{detail}` — a fixed, safe string |

Client (`js/streaming.js`) translates that into the UI's existing contract:

| event | when |
|---|---|
| `start` | request accepted; the working card renders empty |
| `step` | one per `stage`; the previous step flips to completed and the new one is appended |
| `content_start` | on `done`; concept card built from `route` + `plan.topic` |
| `token` | typewriter over the finished answer (the graph has no token stream) |
| `final` | the assembled message: sections, hint rung, next steps, citations |

## Mock → endpoint mapping

| mock method | real call |
|---|---|
| `login` | `POST /auth/login` (username + password) |
| `register` | `POST /auth/register` |
| `refresh` | `POST /auth/refresh` (httpOnly cookie, no body) |
| `logout` | `POST /auth/logout` |
| `getMe` | `GET /auth/me` |
| `getConversations` | `GET /conversations` (grouping computed client-side from `updated_at`) |
| `createConversation` | `POST /conversations` |
| `renameConversation` | `PATCH /conversations/{id}` |
| `deleteConversation` | `DELETE /conversations/{id}` |
| `getMessages` | `GET /conversations/{id}/messages` |
| `sendMessage` | none — the optimistic bubble only; `/chat` persists both turns |
| `streamChat` | `POST /chat/stream` |
| `getProfile` | `GET /profile` |
| `updateMessage` | **deleted** — the hint level is server state |
| `enterDemo` | **deleted** |
| `adaptive_mock_db_v1` seed | **deleted**; `localStorage` keeps only the theme and the active-conversation id |

## Commands Run

```
docker compose up -d
.\venv\Scripts\python.exe -m alembic upgrade head      # already at head
.\venv\Scripts\python.exe -m alembic check             # no new operations
.\venv\Scripts\python.exe -m pyright app tests         # 0 errors, 0 warnings
.\venv\Scripts\python.exe -m ruff check .              # All checks passed
.\venv\Scripts\python.exe -m ruff format --check .     # all formatted
.\venv\Scripts\python.exe -m pytest tests -q
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
cd frontend && npm install && npm run build               # pnpm is not installed here
```

## Test Results

**Automated** — `pytest tests -q`: **1354 passed, 2 skipped** (baseline before
this work: 1270 passed, 2 skipped; +84 new tests, no regressions). pyright
strict: 0 errors across `app` + `tests`. ruff check and format: clean.
Alembic: no migration needed — nothing in this work changed the schema.

**Manual / Playwright, against the built bundle served by FastAPI at
`http://127.0.0.1:8000`**

| # | Case | Expected | Actual |
|---|---|---|---|
| 1 | `GET /health` | db + qdrant ok | `{"status":"ok","db":"ok","qdrant":"ok"}` — PASS |
| 2 | Logged-out `/chat.html` | redirect to login | redirected — PASS |
| 3 | Register → login | redirect with username prefilled | `/login.html?username=learner_e2e` — PASS |
| 4 | Login → chat | workspace with real user chip | chip shows `learner_e2e`, real empty profile — PASS |
| 5 | Login sets cookie | httpOnly, SameSite=Strict, /auth | `refresh_token=…; HttpOnly; Max-Age=604800; Path=/auth; SameSite=strict` — PASS |
| 6 | Refresh with cookie only, no body | 200 + new pair | 200 — PASS |
| 7 | Hard refresh | session survives, history reloads from backend | both turns reloaded, no re-login — PASS |
| 8 | Session create | new row in sidebar | created — PASS |
| 9 | Session rename | persists across hard refresh | "Renamed by test" after reload — PASS |
| 10 | Session delete | gone after hard refresh | removed — PASS |
| 11 | Auto-title | first turn names the session | titled from the first message — PASS |
| 12 | DSA turn | working steps, then a hint, no full code | hint card, `hasCode: false` — PASS |
| 13 | Hint ladder climb | rung increases per turn | Hint 1 of 3 → 2 of 3 → "Final guidance", code never shown — PASS |
| 14 | Hint ceiling | button changes, no code | `more_help_available=false`, "Ask for more help →" — PASS |
| 15 | Debug turn | static findings + sandbox verdict | all 9 real stages streamed, then findings + sandbox result — PASS |
| 16 | Streaming order | steps before the answer | working card completed before content — PASS |
| 17 | Profile modal on the user chip | full profile + sign out | username, member since, fluency, skill map, preferences, errors — PASS |
| 18 | Static mount vs API routes | API still wins | `/health` 200 JSON, `/`, `/login.html`, `/chat.html` 200 HTML — PASS |
| 19 | Console errors | none | 0 errors across the flows — PASS |

**Not exercised manually:** image upload through the composer, and the
"Challenge"/hint-mode `assistance_cap` round trip in the browser (both are
covered by backend tests; the image path is unchanged from the verified
`/chat` multipart contract).

## Known Issues

1. **Stored history renders as plain markdown.** `messages` keeps the rendered
   text only, so reopening an older conversation shows the answer without the
   working-steps card, concept card or hint ladder — those are live-stream
   decoration. Persisting them would need a new column.
2. **The fallback hint-topic key is a practical, not a cryptographic,
   sentinel.** `hint_progress.topic` is `String(64)` and `topic_hint` has no
   charset restriction, so no string is both guaranteed unreachable by client
   input and guaranteed to fit the column. A learner whose topic is literally
   `__no_topic_inferred__` would share the fallback ladder bucket — no crash and
   no cross-user leak. A collision-free scheme needs a schema change (e.g. an
   `is_fallback` column).
3. **The profile panel only moves when a learning event is recorded.** A pure
   hint turn reports `solved=None`, so no event is persisted and the skill map
   legitimately stays empty. This is the agent's design, not a wiring gap.
4. **`.hint-head` runs the rung label into the title** ("Hint 1 of 3" then the
   bold line, with no separator). This is inherited from the original design's
   markup and CSS, unchanged here; it needs a design decision, not a bug fix.
5. **`Keep me signed in`** no longer changes anything: refresh-token lifetime is
   a server setting (7 days). The control was left in place rather than altering
   the login layout.
6. **pnpm is not installed on this machine**, so `npm` was used despite the
   committed `pnpm-lock.yaml`. The lockfiles may drift until someone builds with
   pnpm again.
