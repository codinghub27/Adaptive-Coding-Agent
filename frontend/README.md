# Adaptive Coding Agent UI

The web frontend for the Adaptive Coding Agent. Vanilla HTML, CSS and
JavaScript, built by Vite as a four-entry multi-page app. It talks to the
FastAPI backend and nothing else — it never executes code and never touches the
sandbox.

## Run locally

The API and the UI are served from **one origin**, which is what lets the
httpOnly refresh cookie work without any CORS credential handling.

Development — Vite serves the pages and proxies the API:

```bash
# terminal 1, from the repo root
.\venv\Scripts\python.exe -m uvicorn app.main:app --reload

# terminal 2
cd frontend
pnpm install && pnpm dev          # or: npm install && npm run dev
```

Open <http://127.0.0.1:5173/>. `API_TARGET` overrides the proxy target
(default `http://127.0.0.1:8000`).

Production — FastAPI serves the built bundle itself:

```bash
cd frontend && pnpm build         # writes frontend/dist
.\venv\Scripts\python.exe -m uvicorn app.main:app
```

Open <http://127.0.0.1:8000/>. The bundle is mounted last, after every API
route, so it can never shadow one. If `frontend/dist` does not exist the mount
is skipped and the API runs on its own.

## Pages

- `login.html` — username + password sign-in
- `register.html` — username + password registration
- `chat.html` — the workspace: conversations, composer, learning profile

`index.html` just forwards to `/chat.html`; the workspace's own route guard
exchanges the refresh cookie for an access token and bounces to `/login.html`
when there is no session.

## Project structure

```text
css/
  variables.css    Design tokens and light/dark themes
  base.css         Shared foundations, brand and toasts
  auth.css         Authentication layouts
  chat.css         Workspace, messages, profile and responsive states
  chat-scale.css   Density and type scale, loaded after chat.css
js/
  api.js           API client: auth, conversations, profile
  auth.js          Login and register behaviour
  sessions.js      Conversation list controller
  streaming.js     SSE reader for one teaching turn
  ui.js            Rendering, markdown, modals and feedback
  chat.js          Workspace state and interaction orchestration
```

## Auth model

- The **access token lives in memory only** — never in `localStorage`, so an XSS
  payload cannot read it back out.
- The **refresh token lives in an httpOnly, SameSite=Strict cookie** scoped to
  `/auth`, set by the server. JavaScript can neither read nor forge it.
- A hard refresh loses the access token, so the client silently exchanges the
  cookie for a new pair on first use.
- A 401 mid-flight triggers one single-flight refresh and one retry. If that
  refresh fails the session is over and the user is sent to the login page.

`localStorage` holds only the theme and the active-conversation id. Conversation
history always comes from the backend, so a hard refresh restores exactly what
the server stored.

## The hint ladder is server state

`hint_level`, `hint_ceiling` and `more_help_available` come from the response.
There is no client-side counter: "Get next hint" sends **another turn** naming
the same topic, and the backend decides what the next rung says and whether it
may contain code. A hint-level turn structurally cannot render a full solution,
because the response generator never puts one in the payload.

## Streaming

`POST /chat/stream` emits SSE `stage` frames as each graph node starts, then one
terminal `done` frame carrying the finished response (or `error`). Stage labels
come from a fixed server-side table, so no user text or model output can ride
out on a progress event. The graph does not stream tokens, so the finished
answer is typed out client-side after the working steps complete.
