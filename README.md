# Adaptive Coding & Problem-Solving Agent

An AI **tutor + debugger + code reviewer + problem solver** that adapts to the
learner instead of dumping answers. It accepts text, code, and images
(screenshots of problems / code / errors), figures out what you actually want,
models what you know, and guides you with progressive hints — verifying code by
**running it in a sandbox** rather than trusting the model.

> **Design principle:** the agent does not optimize for giving the answer. It
> optimizes for helping you solve the problem independently.

This distinguishes it from a code-generation chatbot: the interesting
engineering is in **adaptive tutoring, code reasoning, multimodal understanding,
safe execution, learner modeling, and verification.**

---

## Features

- **Multimodal input** — text, pasted code, error logs, and screenshots (vision
  extracts problem/code/error).
- **Intent-aware** — the same code produces different help depending on whether
  you want a hint, a fix, an explanation, a review, or a concept.
- **Progressive hint ladder** — from "what pattern fits?" down to full solution;
  stops the moment you solve it.
- **Debugging agent** — static analysis → run tests → find the failing case →
  trace → localize bug → explain → patch → re-verify.
- **Code explainer** — AST-driven, line-by-line, with complexity analysis.
- **Adaptive learner model** — skill levels, preferences, and common mistakes,
  updated from real learning events (not guesses).
- **Verified coding** — generated/patched code is executed against tests in an
  isolated Docker sandbox.
- **Knowledge RAG** — DSA patterns, algorithms, and Python practices retrieved
  via hybrid search (dense + BM25 + reranker).
- **Observability & evaluation** — full tracing and eval with LangSmith.

---

## Architecture

```
USER (text / code / image)
   → Input Understanding (vision/OCR for images)
   → Intent Classifier
   → Learner Profile + Conversation Memory
   → Teaching Planner
   → DSA Solver (hint ladder) | Debugger | Code Explainer
   → Knowledge RAG (Qdrant + BM25 + reranker)
   → Code Execution Sandbox (Docker) → Verification
   → Response Generator (hint / explanation / code)
   → Learning Events → Learner Profile update
   → LangSmith evaluation
```

The full diagram and specification are in [`docs/architecture.md`](docs/architecture.md).
The whole flow is orchestrated with **LangGraph**, with each capability as a
subgraph.

---

## Tech stack

**Backend:** FastAPI · LangGraph · Pydantic v2 · SQLAlchemy 2.0 · PostgreSQL
**AI:** provider-agnostic LLM + vision + embeddings (behind a traced wrapper)
**RAG:** Qdrant · BM25 · reranker
**Code intelligence:** Python `ast` · Tree-sitter · static analysis
**Execution:** Docker sandbox (isolated, resource-limited)
**Observability:** LangSmith
**Frontend:** vanilla HTML/CSS/JS, Vite-built (→ React/Next.js later)

---

## Project status

Built in phases; each is shippable and committed before the next begins.

| Phase | Title | Status |
|---|---|---|
| 01 | Project Foundation & Core Infrastructure | Done |
| 02 | Input Understanding & Multimodal Intent | Done |
| 03 | Learner Profile, Memory & Learning Events | Done |
| 04 | LangGraph Orchestration & Teaching Planner | Done |
| 05 | Knowledge Base & Hybrid RAG | Done |
| 06 | Code Execution Sandbox & Verification | Done |
| 07 | Specialized Agents (DSA / Debug / Explain) | Not Started |
| 08 | Response Generation, Frontend & Evaluation | Not Started |

Phase specs live in [`docs/phases/`](docs/phases/).

---

## Getting started

Requires Python 3.11+ and Docker.

```bash
# 1. clone and enter
git clone https://github.com/codinghub27/Adaptive-Coding-Agent.git
cd Adaptive-Coding-Agent

# 2. virtualenv + dependencies
python -m venv venv
source venv/Scripts/activate      # Windows (Git Bash); on macOS/Linux: source venv/bin/activate
pip install -r requirements.txt

# 3. env: required: DATABASE_URL, QDRANT_URL, and GROQ_API_KEY (or
#    OPENROUTER_API_KEY with LLM_PROVIDER=openrouter). The app refuses to
#    start if any required value is missing.
cp .env.example .env

# 4. infra: Postgres (host port 5433) + Qdrant (6333)
docker compose up -d

# 5. migrations
alembic upgrade head

# 6. run  (note: `python -m` via the venv, so the project's deps are on the path --
#          a bare `uvicorn` may resolve to a global install that lacks them)
./venv/Scripts/python.exe -m uvicorn app.main:app --reload   # Windows
# python -m uvicorn app.main:app --reload                    # macOS/Linux (venv activated)
curl http://127.0.0.1:8000/health   # {"status":"ok","db":"ok","qdrant":"ok"}
```

Checks:

```bash
pytest                  # unit + integration (integration auto-skips if infra is down)
pyright                 # strict
ruff check . && ruff format --check .
```

---

## Running the frontend

The web UI (vanilla HTML/CSS/JS, built with Vite) talks to the FastAPI backend
over HTTP only. Start the API first.

**Development** — the Vite dev server proxies API requests, so run both in two
terminals:

```bash
# terminal 1: the API (from "Getting started" above)
./venv/Scripts/python.exe -m uvicorn app.main:app --reload

# terminal 2: the web UI dev server
cd frontend && pnpm install && pnpm dev
```

Both go through the venv's interpreter on purpose. A bare `uvicorn` takes
whichever copy is first on `PATH`; if that is a global install, the app fails
immediately with `ModuleNotFoundError: No module named 'qdrant_client'`. With
the venv activated, plain `python -m uvicorn ...` works too.

**Production** — build the static assets once and let the API serve them:

```bash
cd frontend && pnpm build
./venv/Scripts/python.exe -m uvicorn app.main:app
```

FastAPI serves the built `frontend/dist` directory directly, so there is only
one server to run.

On first run, use the **Register** page to create an account, then log in —
every endpoint the UI calls requires a bearer token. Two things are worth
knowing:

- **Keep the conversation and topic stable to climb the hint ladder.** Progress
  is keyed on `(user, conversation, topic)` server-side, so "Show me the next
  hint" re-sends the same problem on the same conversation. Starting a new
  conversation restarts the ladder at the first rung.
- **Debugging needs Docker.** Verified fixes come from running your code in the
  sandbox; without a reachable Docker daemon the agent still explains the bug but
  will say it could not verify a fix, rather than claiming one.

Vite's dev server hot-reloads most edits under `frontend/` automatically; a
full restart is only needed after config changes (`vite.config.ts`,
`package.json`).

---

## Repository layout

```
app/          # FastAPI + LangGraph application
  llm/        # model client wrappers (traced)
  graph/      # state, nodes, edges, subgraphs
  agents/     # dsa_solver, debugger, explainer, reviewer, planner
  input/      # normalization, vision/OCR, intent classifier
  memory/     # learner profile, conversation memory, learning events
  knowledge/  # ingestion + hybrid retrieval
  execution/  # docker sandbox, runner, verification
  response/   # response generation
  db/         # models, sessions, migrations
tests/
docs/         # architecture.md + phases/
frontend/     # web UI (Vite, vanilla HTML/CSS/JS)
```

---

## Security

User-supplied code is **only** ever executed inside the Docker sandbox with no
network, resource limits, and a wall-clock timeout. Correctness is established by
running tests — never by trusting the model. See `CLAUDE.md` for the full rules.

---

## License

TBD.
