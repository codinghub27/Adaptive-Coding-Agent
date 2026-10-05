# Audit report: adaptive behaviour failures

Branch `experimental`. File and line references are as of commit `957684d`
(the audit baseline). Status of each finding is in section 6.

## 1. Scope and two corrections to the brief

The brief described a local Ollama stack and asked for the LangGraph Postgres
checkpointer. The code says otherwise, and the owner confirmed both points on
2026-10-05:

| Brief | Code on `main` / this branch | Decision |
|---|---|---|
| Local Ollama (`qwen3.5:9b`, `qwen2.5-coder:7b`), Groq/OpenRouter commented out | `app/config.py:19` `LLMProvider = Literal["groq", "openrouter"]`; no `app/llm/ollama.py`. The Ollama port lives only on `experiment/local-ollama`, which has diverged 5 commits each way | Target the cloud stack. The fixes here are in routing, state and input handling, so they port |
| Session memory through `AsyncPostgresSaver` | `app/graph/build.py:165` compiles with no checkpointer; `langgraph-checkpoint-postgres` is not installed. State is one frozen object per turn; memory is the conversation store keyed by `conversation_id` | Keep the conversation store, fix its gaps (A-02). No new dependency |

Two failing sessions were audited:

- **Session M** (the brief): LeetCode 678 from a screenshot, turns T1-T5 and the
  `'*'` rendering bug.
- **Session C** (`docs/current-behavior.md`, conversation
  `648416bd-5c4d-48ae-a956-b15abb5239ea` in Postgres): Trapping Rain Water code
  pasted with no statement, turns C1-C5.

Session M no longer reproduces: its fixes were already in the working tree
(committed here as `09824d2`, logged as ERR-005 to ERR-007 in `fixedErrors.md`)
and `tests/graph/test_conversation_regression.py` replays T1-T5 green. Session C
reproduces offline, turn for turn, through the real graph with a scripted model.
Every new finding below comes from it.

Baseline before any fix: `pytest tests` 1802 passed, 16 skipped; `pyright app
tests` 0 errors.

## 2. Request path for one chat turn

| Hop | Where |
|---|---|
| Frontend builds the multipart body, sends `conversation_id` when it has one | `frontend/js/streaming.js:175-204` |
| `POST /chat/stream` (or `/chat`) | `app/graph/api.py:385` (`:251`) |
| User from the bearer token only, never from the body | `app/auth/deps.py` `get_current_user` |
| Conversation resolved or created, ownership checked, 404 otherwise | `app/graph/api.py:209` `_ensure_conversation` |
| Graph run; `user_id` and `conversation_id` travel in `GraphContext` | `app/graph/build.py:359` `stream_graph`, `:186` |
| Text normalised; image read by the vision model, bytes dropped from state | `app/graph/nodes.py:175` `understand_input`, `app/input/vision.py` |
| Intent: rules, then the model, then a keyword fallback | `app/graph/nodes.py:220`, `app/input/intent.py:474` |
| Profile, last messages, active problem, pending check, session progress loaded | `app/graph/nodes.py:240` `load_learner_profile` |
| Retrieval (gated), then this turn's relation to the active problem | `app/graph/nodes.py:613`, `:684` `_problem_update` |
| Teaching plan from intent, profile and hint progress | `app/graph/nodes.py:339`, `app/agents/planner.py:545` |
| Route decision | `app/graph/routing.py:224` `select_route` |
| Agent: `dsa_agent` `:1261`, `debug_agent` `:1480`, `explain_agent` `:1684`, `practice_agent` `:1515`, `grade_answer` `:1622`, `clarify` `:1904` | `app/graph/nodes.py` |
| Sandbox run and verdict | `app/graph/nodes.py:1763`, `:1804`, `app/execution/` |
| Reply assembled without a model call; symbols protected | `app/graph/nodes.py:1937`, `app/response/generate.py:307`, `app/response/format.py` |
| Messages, active problem, pending check, learning event, profile written | `app/graph/nodes.py:2627` `update_learner_model` |
| `ChatResponse` in the SSE `done` frame | `app/graph/api.py:229`, `:375` |
| Message shape, hint card, markdown | `frontend/js/streaming.js:124`, `frontend/js/ui.js:193`, `:356` |

## 3. The graph

```mermaid
flowchart TD
    START([START]) --> understand_input
    understand_input --> classify_intent
    classify_intent --> load_learner_profile
    load_learner_profile --> retrieve_knowledge
    retrieve_knowledge --> plan_teaching
    plan_teaching --> route
    route -->|dsa| dsa_agent
    route -->|debug| debug_agent
    route -->|explain| explain_agent
    route -->|practice| practice_agent
    route -->|grade| grade_answer
    route -->|clarify, meta| clarify
    dsa_agent --> execute_code
    debug_agent --> execute_code
    explain_agent --> execute_code
    practice_agent --> execute_code
    grade_answer -->|don't know on a problem| dsa_agent
    grade_answer -->|otherwise| final_response
    clarify --> final_response
    execute_code --> verify
    verify -->|pass, fail, inconclusive, skipped| final_response
    final_response --> update_learner_model
    update_learner_model --> END([END])
```

Every node is wrapped in `safe_node` with a per-node fallback
(`app/graph/nodes.py:2714`), so an exception degrades that node and records a
`NodeError` instead of failing the turn. `debug_agent`, `dsa_agent` and
`explain_agent` each run their own compiled subgraph. No checkpointer, no
interrupts; `RECURSION_LIMIT` is 20.

## 4. Audit by area

**A. Session memory.** No checkpointer, by design. A turn's memory is loaded
from Postgres in `load_learner_profile` and written back in
`update_learner_model`. Verified against the database: the Session C
conversation holds all ten messages, in order, under one `conversation_id`, and
the frontend sends that id on every turn. Storage was never the fault. The gap
is what gets stored as the conversation's subject (A-02).

**B. Problem context.** A statement from text or an image is stored in
`conversations.active_problem` and re-attached to follow-ups
(`inherit_active_problem`). The image itself is read once and its bytes are
dropped. A turn that carries only code stores nothing (A-02).

**C. Profile.** Loaded every turn, read by the planner (skill per topic and
family, preferences, `watch_errors`), passed to the solver as `skill_level`, and
updated from the turn's learning event by EWMA in `update_learner_model`. Working.

**D. Router.** The classifier sees only the current turn
(`app/input/intent.py:295`). Deterministic rules after it repair the known
misreads (meta questions, study plans, "I don't know"). Session C found the next
holes in those rules (A-01, A-03, A-04) and the underlying cause is A-08.

**E. Prompts.** A guided hint is the solver's own problem-specific step and
renders without headers. The fixed ladder template is the fallback only.

**F. Rendering.** `'*'` is fixed at both layers (`protect_symbols`, and the
frontend emphasis regex). A new duplication bug is in the frontend (A-07).

**G. Sandbox.** Nothing is called "verified" without a sandbox pass; the patch
and the reference solution are both gated on one. The gate works; what reaches
it does not, because a pasted snippet often is not a runnable module (A-05).

**H. RAG.** Retrieval is gated by intent and a score floor. The "corpus dump" in
Session C is not retrieval: it is the pattern's canned teaching sections,
rendered because the plan was `full` on a turn that had nothing to reveal (A-06).

**I. General.** `.gitignore` gaps fixed in `957684d`. `fixedErrors.md` still
tells the reader to run a Streamlit frontend that no longer exists (A-11).

## 5. Findings

| ID | Sev | Where | Root cause | Evidence | Fix plan |
|---|---|---|---|---|---|
| A-01 | critical | `app/graph/routing.py:42` `INTENT_ROUTES`; `app/graph/nodes.py:684` `_problem_update` | A turn that carries the learner's code and no problem statement is routed by the classifier's label alone. "give correct code of this" + code is labelled `DSA_SOLVE`, so it goes to the hint ladder and the code is never read. Target behaviour section 10: the code is the primary evidence | Stored intents for the session: `DSA_SOLVE` on turns 1, 3, 5. Offline replay: route `dsa`, reply is Hint 1 | Code with no statement on a DSA-route intent is a debug turn, decided by rule after classification |
| A-02 | critical | `app/graph/nodes.py:2514` `active_problem_update` | Only a problem statement becomes the conversation's subject. A code-only turn stores nothing, so every follow-up has `problem_relation == "none"` and starts from zero | Replay: `ACTIVE: None` after C1; C2 and C3 see no code and no problem | Store the code as the conversation's subject. A follow-up with no code of its own inherits it and stays on the debug route |
| A-03 | high | `app/agents/planner.py:116` `_EXPLICIT_ASK_RE` | The explicit-ask phrase list does not match "give python code", "give correct code", "i asked for the code" | Replay C3: plan rationale `escalation_denied_no_explicit_ask`; reply is Hint 2 | Widen the phrase list (still a fixed list, never a model) |
| A-04 | high | `app/graph/nodes.py:1920` `clarify`; `app/graph/routing.py:170` `_guidance_without_ask` | Any `GENERAL_GUIDANCE` turn with no plan ask is answered with the greeting, including a complaint in the middle of a session | Replay C4: "i asked for python code" gets "Hi! Share a problem statement..." | With a subject in the conversation, such a turn continues it. Without one, the reply asks what is needed and is not a greeting |
| A-05 | high | `app/agents/debugger.py:68` `extract_learner_code`; `app/execution/testgen.py:67` | Pasted code is used as typed. The three common paste shapes are not runnable modules: a method body whose first line lost its indentation, a body with a top-level `return`, and a LeetCode `class Solution`. The debugger then reports the paste as the bug | Live C5: "syntax error ... line 5", "0/0 test cases passed", no diagnosis of `left -= 1` | Repair the snippet once, when input is understood: re-align, wrap a bare body in a function, give `Solution` methods a module-level entry point. Parsing only, never execution |
| A-06 | high | `app/graph/nodes.py:1312`; `app/response/generate.py:100` | When a reveal is granted but no reference can be verified, the turn keeps `assistance_level == "full"` and renders every survey and corpus section around a one-line refusal | Live C2: recognition, intuition, brute force, pseudocode and "Final guidance" for a turn that asked where the bug is | A granted reveal with nothing to show renders the honest note and nothing else |
| A-07 | medium | `frontend/js/streaming.js:132` | When the hint is the whole answer, `answerBody` is empty and the code falls back to `payload.response`, which is the same hint. It then renders in the body and again in the hint card | Live C1 and C3: the hint paragraph appears twice | No fallback when a hint card already shows the text |
| A-08 | medium | `app/graph/build.py:132`; `app/input/intent.py:295` | The classifier runs before the conversation is loaded and sees one message. Every follow-up misroute so far traces back to this | Graph order; stored intents | Load the conversation first; give the classifier a short, delimited context (active subject, pending question, last messages) |
| A-09 | medium | `app/graph/build.py:165` | No LangGraph checkpointer | See section 1 | Won't fix (owner decision) |
| A-10 | medium | `app/execution/synth.py:322`; target behaviour section 9 | An explicit ask for code is refused when the sandbox cannot verify a reference (no runner, or the model's reference fails its own cases). The docs conflict: target behaviour says give the code; AD-4 and `CLAUDE.md` say never show unverified code | Live C2 | Deferred. The refusal stays; A-06 makes it short and honest. See section 7 |
| A-11 | low | `fixedErrors.md:36` | Run instructions name `frontend/app.py` (Streamlit), which is gone | File listing | Correct the instructions |
| A-12 | low | `.gitignore` | `node_modules`, data volumes, model files and logs were not ignored | File | Fixed in `957684d` |
| A-13 | low | `app/response/format.py` `render_verdict` | A run with no test cases that crashed reads "0/0 test cases passed" | Live C5 | Say that the code crashed and that no cases were available |

### Failing turns mapped to findings

| Turn | Symptom | Findings |
|---|---|---|
| M T1 | Template sections, generic "pin down inputs" hint | ERR-005 (fixed in `09824d2`) |
| M T2 | "I'm not sure which problem" | ERR-005 (fixed in `09824d2`), A-08 |
| M T3, T4 | Study plan in reply to a follow-up | ERR-005 (fixed in `09824d2`), A-08 |
| M T5 | Rigid sections, generic "Hint 2 of 4" | ERR-005 (fixed in `09824d2`) |
| M `'*'` | Rendered as `''` | ERR-005 (fixed in `09824d2`) |
| C1 | Code pasted, asked for the correct code, got Hint 1, shown twice | A-01, A-05, A-07 |
| C2 | Asked for the code and the bug, got a pattern survey and a refusal | A-01, A-02, A-06, A-10 |
| C3 | "give python code" got Hint 2 | A-02, A-03, A-07 |
| C4 | "i asked for python code" got a greeting | A-02, A-04 |
| C5 | "fix this code" got an indentation complaint, bug not found | A-05, A-13 |
