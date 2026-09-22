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

The full diagram and specification are in [`docs/architecture.md`](../docs/architecture.md).
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
**Frontend:** Streamlit (→ React/Next.js later)

---

## Project status

Built in phases; each is shippable and committed before the next begins.

| Phase | Title | Status |
|---|---|---|
| 01 | Project Foundation & Core Infrastructure | Not Started |
| 02 | Input Understanding & Multimodal Intent | Not Started |
| 03 | Learner Profile, Memory & Learning Events | Not Started |
| 04 | LangGraph Orchestration & Teaching Planner | Not Started |
| 05 | Knowledge Base & Hybrid RAG | Not Started |
| 06 | Code Execution Sandbox & Verification | Not Started |
| 07 | Specialized Agents (DSA / Debug / Explain) | Not Started |
| 08 | Response Generation, Frontend & Evaluation | Not Started |

Phase specs live in [`docs/phases/`](../docs/phases/).

---

## Getting started

> Setup is finalized in Phase 01. Target flow:

```bash
# 1. clone and enter
git clone <repo-url> && cd coding-agents

# 2. env
cp .env.example .env          # add LLM / DB / Qdrant / LangSmith keys
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. infra (Postgres + Qdrant)
docker compose up -d

# 4. run
uvicorn app.main:app --reload
```

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
frontend/     # Streamlit app
```

---

## Security

User-supplied code is **only** ever executed inside the Docker sandbox with no
network, resource limits, and a wall-clock timeout. Correctness is established by
running tests — never by trusting the model. See `CLAUDE.md` for the full rules.

---

## License

TBD.
