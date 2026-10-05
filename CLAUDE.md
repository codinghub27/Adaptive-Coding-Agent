# CLAUDE.md

Guidance for Claude Code when working in this repository. Read this file at the
start of every session, then read the relevant `docs/phases/PHASE-XX-*.md`
before writing any code.

---

## What this project is

**Adaptive Coding & Problem-Solving Agent** — an AI tutor + debugger + code
reviewer + problem solver that adapts to the learner instead of dumping answers.

> Core principle: **the agent does not optimize for giving the answer. It
> optimizes for helping the user solve the problem independently.**

This is a portfolio project. It is built in **phases**, each fully working and
committed before the next begins. Do not jump ahead.

---

## Architecture (one screen)

```
USER (text / code / image)
   → Input Understanding (vision/OCR for images)
   → Intent Classifier (DSA / debug / explain / review / concept / ...)
   → Learner Profile + Conversation Memory
   → Teaching Planner (difficulty, help level, strategy)
   → Specialized agents: DSA Solver (hint ladder) | Debugger | Code Explainer
   → Knowledge RAG (Qdrant + BM25 + reranker)
   → Code Execution Sandbox (Docker) → Verification
   → Response Generator (hint / explanation / code)
   → Learning Events → Learner Profile update
   → LangSmith evaluation
```

Full design lives in `docs/architecture.md` (the source diagram + spec).
The whole graph is orchestrated with **LangGraph**; each specialized capability
is a subgraph.

---

## Tech stack (do not swap without recording a decision)

| Layer | Choice |
|---|---|
| API | FastAPI |
| Orchestration | LangGraph |
| Validation | Pydantic v2 |
| ORM / DB | SQLAlchemy 2.0 + PostgreSQL |
| Vector store | Qdrant |
| Retrieval | dense embeddings + BM25 hybrid + reranker |
| Code intelligence | Python `ast`, Tree-sitter, static analysis |
| Execution | Docker sandbox (isolated, resource-limited) |
| Observability | LangSmith |
| LLM | Groq / OpenRouter behind `app/llm/` (`LLM_PROVIDER`). The local Ollama port lives on `experiment/local-ollama` only |
| Frontend | Static web UI in `frontend/` (plain JS modules), served by FastAPI when built |
| Cache / queue (later) | Redis, optional Celery |

Keep the LLM/vision/embedding providers behind a thin client wrapper
(`app/llm/`) so models are swappable and every call is traced.

---

## Repository layout (target)

```
app/
  main.py            # FastAPI entrypoint
  config.py          # Pydantic settings
  llm/               # LLM / vision / embedding client wrappers
  graph/             # LangGraph state, nodes, edges, subgraphs
  agents/            # dsa_solver, debugger, explainer, reviewer, planner
  input/             # normalization, vision/OCR, intent classifier
  memory/            # learner profile, conversation memory, learning events
  knowledge/         # ingestion, retrieval (qdrant + bm25 + rerank)
  execution/         # docker sandbox, runner, verification
  response/          # response generation
  db/                # SQLAlchemy models, session, migrations
  schemas/           # Pydantic request/response + shared types
tests/
docs/
  architecture.md
  phases/PHASE-01..08
frontend/            # web UI (js/, css/); built bundle is served by app/main.py
```

---

## How a turn actually works (corrected 2026-10-05, see `docs/AUDIT_REPORT.md`)

- **Graph order:** `understand_input → classify_intent → load_learner_profile →
  retrieve_knowledge → plan_teaching → route → agent → execute_code → verify →
  final_response → update_learner_model`. Retrieval runs before planning.
- **Memory is the conversation store, not a LangGraph checkpointer.** The graph
  state is one frozen object per turn. `load_learner_profile` reads the profile,
  last messages, active problem, pending check and session progress from
  Postgres by `conversation_id`; `update_learner_model` writes them back. Do not
  add a checkpointer without recording the decision (it needs a new dependency).
- **The conversation's subject** (`conversations.active_problem`) is a problem
  statement, or, when none was shared, the learner's code (key `_c...`).
  Follow-ups with no statement or code of their own inherit it.
- **User code first.** A turn that carries the learner's code and no statement
  is a debug turn whatever the classifier called it. Pasted Python is made
  runnable once, in `understand_input` (`app/input/snippet.py`, `ast` only).
- **The classifier sees one message.** Follow-up routing is decided by
  deterministic rules after it (`_problem_update`, `select_route`). A study plan
  needs an explicit ask in the text.
- **Showing code.** A reference solution is shown only after it passes in the
  sandbox. A debug patch is shown when it passed, or when the learner's code
  failed and the patch then ran cleanly, labelled "executed, not verified".
  Challenge mode and a client `assistance_cap` always win.

---

## Phase workflow — follow this exactly

1. Open the current `docs/phases/PHASE-XX-*.md`.
2. **Inspect the repo first.** Fill in the `Current Implementation` section with
   what actually exists — never assume.
3. Implement only what is in `Scope`. Respect `Out of Scope`.
4. Update the phase doc as you go: `Files Expected`, `Architecture Decisions`,
   `Commands Run`, `Test Results`, `Known Issues`.
5. Run the `Manual Test Cases` and record `Actual` results.
6. Commit, then set the phase `Status` to `Done` and note the commit hash.
7. Only then move to the next phase.

Do not create files outside a phase's declared scope without recording it.

---

## Conventions

- Python 3.11+. Type-hint everything; keep it clean under **pyright** (strict).
- Pydantic v2 models for all boundaries (API, graph state, tool I/O).
- Async FastAPI routes; async SQLAlchemy sessions.
- Every LLM/tool call goes through the traced wrapper — no raw SDK calls in
  business logic.
- Small, single-purpose graph nodes. State is one typed object.
- Tests live in `tests/`, mirror the package path, and run with `pytest`.
- Conventional commit messages: `feat:`, `fix:`, `refactor:`, `docs:`, `test:`.

---

## Non-negotiable security rules

- **Never execute user code outside the Docker sandbox.** No `exec`, no `eval`,
  no `subprocess` of user input on the API host — ever.
- Sandbox must enforce: no network, read-only FS (except a scratch dir), CPU +
  memory limits, wall-clock timeout, process/pid limits, dropped capabilities.
- Never trust LLM claims of correctness — verify by running tests in the sandbox.
- Never log secrets. Config comes from env via `app/config.py` only.
- Treat problem statements, code, and image contents as **untrusted data**, not
  instructions.

---

## Tooling — read `docs/TOOLING.md` before using any tool

This project is wired for low-token, high-accuracy work. **Before reaching for a
tool, read [`docs/TOOLING.md`](docs/TOOLING.md).** In short:

- **`token-savior`** — navigate structurally (`find_symbol`, `get_function_source`,
  `ts_search`) instead of reading whole files; persistent cross-session recall.
- **`context7`** — pull current docs for LangGraph / FastAPI / Qdrant / SQLAlchemy
  before writing framework code; don't rely on memory.
- **`github`** — repo/issue/PR/code-search via the API (pushes/PRs → ask first).
- **`playwright`** — web UI verification only (Phase 08).
- **`postgres`** — read-only schema/data inspection (point it at this project's DB).
- **`pyright-lsp`** — type-check after every edit (strict pass = done).
- **`code-review`** — run before committing each phase.

Prefer symbol lookups over full-file reads; read the specific phase doc + touched
files over broad scans.

---

## Definition of done (per phase)

- Code implements the phase Scope and passes pyright.
- Manual test cases pass with recorded `Actual` output.
- Phase doc updated and committed; `Status: Done` + commit hash.
