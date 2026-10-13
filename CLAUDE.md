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

## How a turn actually works (corrected 2026-10-09, see `docs/AUDIT_REPORT.md` and
`docs/BEHAVIOR_GAP.md`)

- **Graph order:** `understand_input → load_learner_profile → classify_intent →
  decide_turn → retrieve_knowledge → plan_teaching → agent → execute_code →
  verify → final_response → update_learner_model`. The conversation is loaded
  BEFORE classification; retrieval runs before planning. There is no `route`
  node.
- **ONE decision per turn.** `decide_turn` (`nodes._decide`) is the only place
  that settles what the turn is about, which agent handles it, and the
  `TurnDecision` record (`app/schemas/decision.py`): `subject`, `evidence`
  (what the learner showed, target behaviour section 2.2 labels), `move`
  (`step`, `code`, `debug`, `explain`, `discuss`, `review`, `plan`, ...),
  `scaffold`, `wants_code`, `withhold`. Retrieval, the planner, every agent
  prompt, the response layer and the learner model READ it. Do not work any of
  it out again downstream, and do not add a second place that relabels a turn.
  `evidence` is refined in place by whoever knows more (the grader, the
  solver's reading of a reply, the sandbox verdict).
- **Three rules inside the decision.** (1) An unsure reading never drops the
  conversation: a message that only mentions a technique stays on the open
  problem; only a question shaped as a question about a concept stands alone.
  (2) An agent must have its subject: a solve, hint or debug turn that brings
  no problem, code or error and was not tied to the conversation is decided
  again as a follow-up on the conversation's subject; with no subject it is
  asked about, and the question says what is missing. (3) A response is not a
  request: a message the model called both an answer and a demand for the code
  is an answer unless the message itself carries a listed ask.
- **"Don't give me the code yet"** is the classifier's `no_solution`. It sets
  `withhold`, caps help at `concept`, forbids stating the fix in the prompt,
  and holds for that subject (`SessionProgress.withhold_key`) until the code
  is asked for.
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
- **The classifier reads the conversation.** It is shown the active subject,
  the one before it, the pending question, the learner's skill and the last six
  messages (`nodes.classifier_context`, sent as delimited data), and returns
  `refers_to_previous`, `earlier_subject`, `asks_for_code` and
  `about_conversation` with its label. Those flags are set only on a confident
  model answer. The phrase lists (`explicit_ask_phrase`, the meta regexes, the
  corpus-vocabulary check) are the FALLBACK for a rule label, the keyword
  heuristic or an unsure answer -- do not add phrases to them to fix a
  misrouted turn; fix what the classifier is shown or told.
- **A follow-up belongs to the tutor's LAST reply.** The conversation records
  what that reply was (`SessionProgress.last_thread`: a step on the problem, a
  study plan, an explanation). "Where should I start?" after a roadmap is about
  the roadmap, not about a problem stored a day earlier. Such a turn is
  answered by the same agent with the recent exchange in its prompt
  (`<conversation_so_far>`). An agent that answers a follow-up must be given
  the conversation; one that is not will answer it as a first message.
- **Every agent prompt carries the same two blocks**
  (`app/tutoring/adaptation.py`): the trusted `<tutor_state>` (the decision,
  the pitch, the representation) and the untrusted `<conversation_so_far>`.
  The debugger has its own instruction for a reply to an earlier diagnosis
  (`_REPLY_SYSTEM`), for a question about passing code (`_ANSWER_SYSTEM`) and
  for an error with no code (`_TRACEBACK_SYSTEM`); a follow-up on an
  explanation uses the short-answer prompt. A general "answer follow-ups
  briefly" rule loses to a prompt's main instruction: when a turn is a
  follow-up, make answering it the main instruction.
- **The learner model gets evidence from ordinary use.** Besides a sandbox
  verdict on the learner's own code and a graded curated question there are two
  soft sources: `tutor_reply` (the tutor's reading of a reply to its own
  question) and `help_needed` (the full solution handed over, first time on
  that problem, with no attempt run). Soft evidence lands on the family
  estimate at a low weight and can never reach the hard band; only the sandbox
  sets `solved`.
- **Adaptation is what changes, not a badge.** `adapt()` gives the pitch, the
  representation (plain → worked example → drawn-out trace → smaller question
  after repeated struggle, from `SessionProgress.evidence_log`) and skip-ahead
  after two right answers. `Adaptation.notes` are fixed sentences saying what
  changed; the API returns them (`adaptations`), they are stored per message
  (`messages.adaptation`), and the UI shows "Adapted to your level" only with
  them. A note is added only when the change really happens in the reply: a
  repeated miss on a curated question is handed to the solver's own step, and
  a mistake from an earlier conversation counts only once the reply links it.
- **A reply is spoken.** `app/response/voice.py` renders every section as the
  tutor would say it: no `##` headers, the sandbox's lines verbatim, one
  question last. It changes presentation only; which sections a turn may carry
  is still decided in code before it. The UI joins `section.spoken` and shows
  the numbered hint card only when `generated.hint_card` is true.
- **Guards that stay deterministic** because they are policy, not reading:
  Challenge mode, a client `assistance_cap`, an explicit study-plan ask,
  user-code-first, and `planner.wants_the_code` (the model's "this asks for the
  code" is believed only for a short message with no learning ask in it -- a
  wrong yes hands a beginner the answer).
- **Showing code (owner decisions A-10, A-15).** Outside Challenge mode an
  explicit ask for the code is never refused. Verification is always attempted;
  only a sandbox pass may be called checked. Code that could not be verified is
  still shown, labelled "Not verified in sandbox" with the reason. A debug
  patch that ran cleanly after the learner's code failed is "executed, not
  verified". With no sandbox the debugger runs its `ast` checks and ONE model
  call, labelled "Not executed". An ask for the code with no fix to show
  returns the learner's own code. Never invent a verdict. The revealed code
  and what is said about it (key idea, cost) come from the SAME model reply
  (`synth._REFERENCE_SYSTEM`), which also honours an asked-for approach
  ("using a stack") as a choice of algorithm. A program that waits for
  `input()` is reported as not run to the end, never as a failure.
- **Generated code is plain Python, in the learner's interface.** No type
  hints (`app/response/plain_python.py` strips them from code the tutor wrote;
  a fix to the learner's own code keeps hints they wrote). The class and
  method of the learner's own code (`snippet.submission_interface`) and their
  earlier messages go to the code writer, so "LeetCode format" and "don't use
  nonlocal" hold across turns. A `class Solution` is run through an added
  entry point and shown as the class.
- **A reported error the sandbox does not reproduce** (a SyntaxError from
  LeetCode's Python 2 on code that parses here) is answered first and by
  itself (`debug._environment_note`), then the logic separately.
- **Code the tutor wrote is not evidence about the learner**
  (`nodes._is_the_tutors_code`), and one failed run is one bug: two reach
  `easy` (`UNSOLVED_SCORE` 0.25).
- **A miss changes the presentation on the same turn.** After the opening
  message every tutoring prompt carries what to switch to if the learner did
  not follow (`Adaptation.if_stuck`). Do not put that line on an opening
  message: a model applies it to a first explanation.
- **Explanations.** Any concept the learner asks about is explained in detail
  with one or two examples that were run in the sandbox first
  (`app/agents/concept.py`). A retrieval hit below `MIN_CONCEPT_TOPIC_SCORE`
  (2.5, reranker scale) is no hit: it gives no topic label and no references,
  and the tutor answers from its own knowledge with a one-line note. Never a
  refusal, and never "the references don't cover this".
- **A question about the learner's code is answered, with the code run first**
  (`app/agents/code_answer.py`, 2026-10-12). "What is the output of this
  code" runs the code as a script in the sandbox and one model call answers
  what was asked with the real output in its prompt: the answer first, then
  why, then one prediction question. The output shown is the sandbox's, never
  the model's. The outline / line-by-line / cost report (`run_explain`) is
  only the fallback when no model answers. The reply is recorded as
  `last_thread == "code"`; a further question ("why does b change too?") is a
  follow-up on THAT code (`_continues_code_answer`) and is answered with the
  exchange in the prompt. A code-only turn that nobody read as a bug report,
  with no error and a clean run, is answered the same way from the debug node
  (`_question_about_working_code`): user-code-first means the code is read,
  not that a bug is assumed. A direct answer gets the pitch but not the
  step-tutoring "if they did not follow" line (`_answer_pitch`).
- **A knowledge check is its own move** (`KNOWLEDGE_CHECK` -> route `assess`,
  `app/agents/assess.py`). "Ask me 10 basic questions" is not a practice
  request. One call writes the questions from the profile
  (`standing_note`) and the ask; they are stored on the conversation
  (`SessionProgress.quiz`) with their marking keys, which are never shown
  before an answer. The quiz is open only while it is the last thing the
  tutor said (`last_thread == "quiz"`); the next message is its answers
  unless it is confidently a request for something else
  (`routing.answers_open_quiz`). A second call marks them. Each MARKED answer
  is one learning event on its own topic with `evidence_source="quiz"`: soft
  evidence, capped below the hard band, but it does move the topic's own
  skill key (it was asked on that topic, to find the level). A question that
  was not answered is no evidence. Topics are a closed vocabulary: corpus
  patterns plus `FOUNDATION_TOPICS`. With no model the built-in fundamentals
  set is asked; answers that could not be marked record nothing and the quiz
  stays open.
- **Talk is its own move** (`discuss` -> route `discuss`, `app/agents/dialogue.py`,
  2026-10-13). Most messages after the first are talk about what is already on
  the table: a question about the step just taken, the learner's own
  reasoning, a restatement to check they understood. `nodes._is_talk` settles
  it inside `decide_turn`, after the route is chosen: a message that BRINGS
  something (a statement, code, an error) is its specialist's; a hint, "I
  don't know" and an ask for the code keep the ladder; a request to be TAUGHT
  a topic (`IntentResult.wants_lesson`, fallback `opens_as_a_lesson_ask`) is a
  lesson; an answer to the tutor's open curated question is graded.
  Everything else in words is ONE model call with the subject, ten messages of
  conversation and `<tutor_state>` in front of it. It never writes solution
  code (`writes_the_solution`; code that comes back twice is cut and the words
  kept). It returns what the learner showed, which lands on
  `decision.evidence` like the solver's reading. A turn no specialist takes is
  talk when it is inside a conversation or was read with confidence; only a
  FIRST message nobody could read is still asked about (`clarify`). The
  representation switch applies to talk only when THIS message says the
  learner did not follow, never on an opening message and never while the
  answer is withheld (`_talk_pitch`).
- **A conversation that opens on a problem has a subject from its first
  message** (`nodes._described_problem`). Only a CURATED title used to be
  stored; "how should I solve Longest Substring Without Repeating
  Characters?" stored nothing and the next unsure turn got "there is no
  problem or code in this conversation yet". The learner's own description is
  stored as the statement when nothing is.
- **"The answer" is a word, not an ask.** The bare phrase was in
  `_EXPLICIT_ASK_RE`: "why can the answer use both sides?" handed over the fix
  after "don't give full code yet". Offering to write the code ("can I try it
  and you review it?", `_OFFERS_TO_WRITE_RE`) is never an ask for it. The
  fixed phrase lists read typographic apostrophes (`grader.plain_quotes`).
- **An attempt at the open problem is checked first.** Code sent about the
  active problem goes to the debugger even when it was called a review; a
  passing attempt gets "this is correct, and why", not a style report.
- **A debugging exercise** ("give me code with a bug and I will fix it":
  `IntentResult.wants_buggy_code`, fallback `asks_for_buggy_code`, settled
  first in `decide_turn`; `app/agents/bug_exercise.py`). A reference solution
  must PASS its cases in the sandbox, one model call plants ONE bug, and the
  result must FAIL the same cases or nothing is handed out. The failing case
  shown is the sandbox's. The problem becomes the conversation's subject, its
  cases are cached, `withhold` is set, and the learner's fix is their own work
  (`SessionProgress.bug_exercise_key` exempts it from `_is_the_tutors_code`).
- **A challenge names no technique.** A practice request with `withhold`
  ("don't tell me the algorithm") is rendered without the topic, the pattern
  signal, the bank's opening question or the watch-outs.
- **Part of a function is read, never run** (`snippet.is_partial_fragment`).
  "Here's the end of my function" is wrapped so it parses, but it reads
  variables its missing lines set: run, it fails on every input and that was
  reported as the learner's bug. The debugger takes its not-executed path with
  a fixed note, and its reader is told to judge the code against the problem
  the learner NAMED and the failure they reported.
- **A shared image is stored with its message** (`messages.image`,
  `image_mime`, `image_text`; `GET /conversations/{id}/messages/{mid}/image`,
  owner only). It used to live only in the browser tab. `image_text` is what
  was read from it; `get_recent_context` appends it to the message, so history
  no longer says "[image]". The bytes travel in `GraphContext.image`, never in
  the traced state.
- **No reference list is printed.** `generated.citations` is still returned as
  data; the UI does not render it, and `concept.strip_markers` removes
  "(ref 14)" and "Reference: 11" lines a model writes into an answer.
- **A short message gets a topic only from a strong hit or a named problem.**
  With no problem statement the floor is `MIN_CONCEPT_TOPIC_SCORE`; between
  the two floors a topic is taken only when the message names a problem the
  corpus lists under a pattern retrieval returned (`planner._named_problem`).
  Measured: "how to solve 3sum" scores -1.60 and "what is the output of this
  code" -3.71; the score cannot separate them, the name does. A wrong topic
  is a wrong key on the learner's profile and steers later turns.
- **A practice request that names no topic is given a problem**: the weakest
  area the profile has evidence on, else an easy starter. The tutor never
  hands the choice back.
- **Tests are deterministic where they can be.** A statement's own examples are
  always in the suite. Sandbox-validated cases are cached per learner and
  subject (`test_suite_cache`, `app/memory/test_suites.py`) and reused.
- **Tree and linked-list code runs.** `app/execution/node_adapter.py` wraps
  node-taking functions in the code SENT to the sandbox so test cases can use
  LeetCode level-order lists and plain lists. The harness and image are
  untouched; do not add node handling to `docker/harness/run.py`.
- **Rate limits are the latency.** Groq's limit is tokens per minute per key
  and per model. With several keys the SDK's own retries are off and
  `FailoverLLMClient` moves on, waiting out only a short `retry-after`.
  `LLM_HTTP_LOG_PATH` records every provider call (metadata only);
  `LLM_CLASSIFIER_MODEL` (off) moves classification to a smaller model.
- **Measuring behaviour.** `eval.behavior.target_replay --out FILE` replays the
  owner's target conversations (`adaptive_examples.jsonl`,
  `docs/target_behavior2.md`, the learner turns of `docs/current-behavior.md`)
  and writes each reply beside the target reply; its checks are a floor, the
  score is the reading (`docs/TARGET_REPLAY.md`). The earlier reference set is
  `reference_examples.jsonl`. `eval.behavior.live_scenarios --runs N` gives a pass
  rate per scenario and per check. One live transcript proves little: the
  classifier's reading varies between runs. Tests with a scripted model prove
  less: every defect in section 14 of the audit was found by reading one live
  run of a scenario whose unit tests were green. After changing a prompt or
  the decision, run the affected scenarios live and READ the replies.
- **Infra.** `docker compose up -d` starts Postgres and Qdrant with
  `restart: unless-stopped`. If the server logs "knowledge dense retrieval
  failed: ResponseHandlingException (ConnectError)", Qdrant is not running; the
  turn continues on keyword search only.

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
