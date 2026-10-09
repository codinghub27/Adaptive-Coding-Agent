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

A finding added while fixing:

| ID | Sev | Where | Root cause | Evidence | Fix plan |
|---|---|---|---|---|---|
| A-14 | high | `app/agents/debugger.py` `_EXPLAIN_SYSTEM`; `app/graph/subgraphs/debug.py` `_explain` | The explanation prompt tells the model "a sandbox has established that the code fails; that failure is FACT" on every debug turn, including turns where the code passed or nothing was proven. On working code the model is instructed to find a bug that is not there | Prompt text; `_explain` is deliberately not gated on an established failure | A second prompt for the unproven case that allows "no bug found"; no explanation call at all when the learner's code passed |
| A-15 | medium | `app/graph/subgraphs/debug.py` `run_debug` | With no sandbox (Docker not running) the debugger returns before reading the code: no static findings, no explanation, only "no code was executed" | Offline replay with `runner=None` | Deferred, see section 7 |

## 6. Status

| ID | Status | Commit | What changed |
|---|---|---|---|
| A-01 | fixed | `f4ea2ac` | Code with no statement on a hint-ladder or catch-all label is a debug turn (`_problem_update`). Example input alone (`nums = [2, 7, 11, 15]`) does not count as the learner's code |
| A-02 | fixed | `f4ea2ac` | Code shared with no statement is stored as the conversation's subject (`_shared_code_subject`, key `_c...`) and re-attached to follow-ups (`inherit_active_problem`). It never replaces a stored statement |
| A-03 | fixed | `f4ea2ac` | Phrase lists widened in `planner._EXPLICIT_ASK_RE` and `grader._HELP_RE`; new `asks_for_fix`. A debug turn that asks for the fix is planned at `full` (`fix_requested`), except in Challenge mode |
| A-04 | fixed | `f4ea2ac` | Such a turn continues the conversation's subject. With no subject the reply asks what is needed; only a greeting gets the greeting |
| A-05 | fixed | `5a12e1a`, wired in `f4ea2ac` | New `app/input/snippet.py`, applied once in `understand_input`. `ast` only |
| A-06 | fixed | `f4ea2ac` | A granted reveal with nothing verified renders the note alone |
| A-07 | fixed | `2166207` | No fallback to `payload.response` when a hint card shows the text |
| A-08 | fixed (round 2) | `7009f51`, `c69e1b7` | See section 10 |
| A-09 | won't fix | | Owner decision, section 1 |
| A-10 | fixed (round 2, owner decision) | `7009f51` | See section 10 |
| A-11 | fixed | this commit | Run instructions corrected |
| A-12 | fixed | `957684d` | |
| A-13 | fixed | `06bc066` | "failed in the sandbox before any test case ran", with the reason |
| A-14 | fixed | `06bc066` | `_READ_SYSTEM` prompt when no failure is established; no explanation call when the code passed |
| A-15 | fixed (round 2, owner decision) | `7009f51` | See section 10 |

Also changed: a debug turn's patch is shown when the learner's code failed in
the sandbox and the patch then ran to completion with no test cases to judge
it. It is labelled "executed, not verified" and `fixed` stays false, so no
skill is credited. The sandbox findings are one section instead of two under
the same title. "what's its name" is recognised as a question about the
conversation.

## 7. Deferred, and where the docs disagree

_Superseded on 2026-10-05: A-08, A-10 and A-15 were decided by the owner and done in round 2 (section 10). The text below is the state after round 1._

**A-08, classifier context.** Giving the classifier the conversation means
reordering two graph nodes and changing the prompt that every routing eval was
measured against. The deterministic rules now decide every case measured so
far, and the replay suite (`eval.behavior.replay`) should be re-run against the
live model before and after such a change. Not done in this pass.

**A-10, explicit ask against the verification rule.** `docs/target_behavior.md`
section 9 says an explicit ask for the code is honoured. `CLAUDE.md` ("never
trust LLM claims of correctness") and decision AD-4 say a reference solution is
shown only after the sandbox passes it. The brief says the docs and
`target_behavior.md` win, but both sides here are docs. The refusal stays for a
reference solution nothing could run. Where the sandbox did run the code, the
debug route now shows a patch that executed cleanly with an honest label. Owner
decision needed on whether an unrun reference may be shown with a warning.

**A-15, no sandbox.** Reading the code without a sandbox would cost two model
calls for an answer that cannot be checked. Nine tests pin the current
zero-call behaviour as a deliberate choice, so it was left alone. On a machine
where Docker is not running the debugger is close to useless; worth revisiting.

**Stack.** `CLAUDE.md` said Streamlit and listed no LLM provider. Corrected.

## 8. Verification

| Check | Before | After |
|---|---|---|
| `pytest tests` (Postgres and Qdrant up) | 1802 passed, 16 skipped (infra down) | 1867 passed, 2 skipped (opt-in live LLM) |
| `pyright app tests` (strict) | 0 errors | 0 errors |
| `ruff check .`, `ruff format --check .` | clean | clean |

New tests: `tests/graph/test_code_first_regression.py` (Session C on one
conversation id, Challenge mode, refused reveal, cross-session profile, phrase
lists, vague follow-ups) and `tests/input/test_snippet.py`. Session M is
`tests/graph/test_conversation_regression.py`, unchanged and green.

### Live sessions, 2026-10-05

`POST /chat` against the running API (Groq), real Docker sandbox, a fresh
account each time, one conversation per run. Run on the final code:
conversation `62aa64e9-1698-43ce-af01-79689734811c`. An earlier run, before the
review fixes: `464600e0-5fb5-490d-af74-127b1aed0f93`. Full replies are in the
`messages` table.

| Turn | Route, plan | Sandbox | Reply |
|---|---|---|---|
| C1 "give correct code of this" + working code | debug, `full` (`fix_requested`) | your code passed 6/6 | Names the approach, then "yours passed every test case in the sandbox, so there is no fix to show -- the version you shared is the one to keep." |
| C2 "give full code and tell me where is the bug" | debug, follow-up on the stored code | passed 6/6 | Same finding. No hint, no refusal, no survey |
| C3 "give python code" | debug, follow-up | passed 6/6 | Same finding |
| C4 "i asked for python code" | debug, follow-up | passed 6/6 | Same finding, no greeting |
| C5 "fix this code" + the broken variant | debug, `full` | your code: IndexError on line 10 | Names `left -= 1`, shows the corrected function |

C5 took two different paths in the two runs, and both are correct:

- Earlier run: the model's test cases validated, so the fix was run against
  them. "A fix was found and verified in the sandbox: all 6 case(s) passed."
  39.5 s, 5 model calls.
- Final run: test synthesis produced no usable suite. The fix was still run,
  shown, and labelled "executed, not verified"; the turn's verdict is
  `inconclusive`. 58.8 s, 7 model calls.

So whether a fix is verified or only executed depends on test synthesis
succeeding, which varies between runs on the same input.

Not run live: Session M (no LeetCode 678 screenshot fixture in the repo) and
the browser UI (the A-07 change is a one-line guard and has no JS test).

## 9. Remaining risks

- Test synthesis is not deterministic (see the two live runs), so the same
  broken code can get a verified fix one time and an executed-only fix the next.
- C2 to C4 repeat the same analysis for the same code. Correct, but a learner
  who asks three times probably wants the code printed back; the reply only
  says which version to keep.
- A wrapped snippet's line numbers are one higher than the learner's paste when
  no sample-input line preceded the body.
- `repair_snippet` handles Python only. Other languages pass through untouched.
- The widened ask phrases are still fixed lists. Phrasings outside them fall
  back to the classifier's label (A-08).

## 10. Round 2 (2026-10-05)

### Git state before the work

`git fetch origin` moved nothing: `git log main..origin/main` is empty, and
`origin/main` was last pushed on 2026-10-03. Local `main` is 8 commits ahead of
`origin/main`. `main..experimental` at the start of round 2 was 9 commits: one
from `fix/conversation-memory-tutor` (`09824d2`, the work that was uncommitted
when round 1 began) and eight from round 1 (`957684d` to `19c1b23`).

### Owner decisions applied

| Decision | What changed | Commit |
|---|---|---|
| A-10: never refuse an explicit code ask | Outside Challenge mode the ask is honoured on any turn. `verified_reference` always tries to verify and returns the candidate either way; an unverified one is shown under "**Not verified in sandbox**" with the reason. AD-4 revised in `docs/features/ADAPTIVE-upgrade.md`, `CLAUDE.md` updated | `7009f51` |
| A-15: static review with no sandbox | `ast` checks plus ONE model call that reads the code, labelled "Not executed". No patch, no verdict. Nine pinned tests updated | `7009f51` |
| A-09: no checkpointer | Unchanged | |

### Fixes

| Fix | What changed | Commit |
|---|---|---|
| F1 (A-08) | Graph order is now `understand_input -> load_learner_profile -> classify_intent`. The classifier is shown the active subject, the earlier one, the pending question, the learner's skill on the topic and the last six messages, and returns `refers_to_previous`, `earlier_subject`, `asks_for_code`, `about_conversation` with its label. Those flags decide follow-up, meta, subject switch and code ask. The phrase lists from `f4ea2ac` run only when the model gave no confident reading | `7009f51`, `c69e1b7` |
| F2 | An ask for the code on a stored code subject returns code: the fix when a bug was proven, otherwise the learner's own code, tidied and commented by one model call and re-run in the sandbox. If the tidied version does not hold up, their code is returned as shared | `7009f51` |
| F3 | The statement's examples are always in the suite. Synthesis stays at temperature 0 with one retry. Sandbox-validated cases are cached in Postgres (`test_suite_cache`, migration `c6d7e8f9a0b1`), keyed by problem title when the statement leads with one, else by a hash, and reused. An attempt pasted for the active problem is judged by that problem's examples | `7009f51` |
| F4 | The approach and the bug explanation are one call instead of two. A cached suite removes the synthesis call on later turns | `7009f51` |
| F5 | `CodeBlock.line_offset` records the lines the snippet repair put above the learner's code; the debugger reports line numbers the learner typed | `7009f51` |

Deterministic guards kept, because they are policy: Challenge mode, the client
assistance cap, the explicit study-plan ask, user-code-first, and
`planner.wants_the_code`.

### Findings made during round 2

| ID | Sev | Root cause | Fix |
|---|---|---|---|
| A-16 | high | The Qdrant container was down for the whole of the owner's session (`Exited (255)` when Docker Desktop stopped; compose had no restart policy). Every turn logged "knowledge dense retrieval failed: ResponseHandlingException" and ran on keyword search only | `restart: unless-stopped` on both services; the log now names the underlying cause and says retrieval continues on keywords. `1acc9dd` |
| A-17 | high | Since round 1 a follow-up on a code subject carries the stored code, and "this turn has code" blocked grading and meta questions. This was the cause of the two failing examples in the baseline replay | `routing._own_code`. `7009f51` |
| A-18 | high | The model returned `asks_for_code: true` for a beginner's "help me solve ... I don't understand how to start", and for "give me step by step" one run in two | `planner.wants_the_code`: believed only for a short message with no learning ask. `7009f51`, `c69e1b7` |
| A-19 | high | Session progress was stored with a computed field that the next read rejected, so the WHOLE record read back empty | `conversation.progress_payload`. `7009f51` |
| A-20 | medium | `tests/auth/test_tokens.py::test_tampered_signature_is_invalid` checked the last character of the signature but replaced the first, so it failed about one run in 64 | Corrected. `7009f51` |

### Routing accuracy: `eval.behavior.replay`, live

| | Checks | Examples |
|---|---|---|
| Before round 2 (`19c1b23`) | 126 / 130 | 3 / 5 |
| After the first F1 cut | 115 / 130 | 2 / 5 |
| After round 2 (`c69e1b7`) | 130 / 130 | 5 / 5 |

Before: `adaptive_002` and `adaptive_004` each failed two checks on turn 2, the
learner's reply to the tutor's question went to the explain agent instead of
being graded (A-17). The first cut of F1 made it worse: with A-10 applied, the
model's `asks_for_code` handed the beginner in `adaptive_001` the full solution
on turn 1 (A-18). The replay is what caught that.

### Latency

Per-node timing from `/chat/stream`; one run each, same messages.

| Turn | Before: calls, time | After: calls, time |
|---|---|---|
| Tutor turn 1 (Two Sum, beginner) | 2, 2.9 s | 2, 4.3 s to 34.6 s |
| Tutor turn 2 (answer graded) | 2, 3.5 s | 1 to 2, 2.4 s to 8.1 s |
| Tutor turn 3 ("I don't know") | 1, 1.2 s | 1, 2.0 s to 17.5 s |
| Debug turn (KeyError) | 5, 10.2 s | 4, 10.2 s to 27.0 s |
| Debug follow-up, suite cached | not possible | 3, 6.9 s to 33.2 s |
| Code reveal (reference, verified) | 3 | 3 to 4, 12 s to 60 s |

Model calls are what the code controls, and they went down or stayed equal: a
tutor turn is 1 to 2 calls (target 3 or fewer: met), a debug turn went from 5
to 4, and to 3 once the suite is cached. Wall-clock time did NOT improve and
cannot be read as a regression either: the same single classifier call took
0.8 s in the morning baseline and up to 16.9 s in the afternoon, after several
hundred calls against the same Groq keys. Where a turn ran unthrottled the
target holds (tutor turns of 2 to 5 s in scenarios 1, 6 and 7); under
throttling it does not. A code reveal is the slow turn: reference plus up to
one repair call plus two sandbox runs.

Not done under F4: classification and relation detection were already one call
after F1 (the relation comes from the classifier's flags). The classifier
prompt grew by about 250 tokens.

### Live behaviour

All eight scenarios pass. Transcripts, per-behaviour scoring and the earlier
failing runs are in `docs/LIVE_BEHAVIOR.md`.

### Verification

| Check | Result |
|---|---|
| `pytest tests` (Postgres, Qdrant up) | 1885 passed, 2 skipped (opt-in live LLM) |
| `pyright app tests` (strict) | 0 errors |
| `ruff check .`, `ruff format --check .` | clean |
| `alembic upgrade head`, `alembic check` | applied, no pending operations |
| code-review on `19c1b23..c69e1b7` | 3 findings, all fixed in the commit that follows it: a long message with a listed phrase lost its code ask; the "nothing to fix" note could contradict the label above it; `explain_bug` duplicated `read_code` |

The three review fixes landed after the live scenarios were run. They change `wants_the_code` only for a message that contains a listed phrase, which none of the scenario messages do, so the scenarios were not re-run.

### Remaining risks after round 2

- The classifier's flags are a model's reading and vary between runs on the
  same message. `wants_the_code` bounds the costly direction (a false "asks for
  the code"). A false "does not refer to the previous subject" still drops the
  problem for that turn, except for "I don't know" and replies to the tutor.
- Latency depends on the provider's throttling far more than on this code.
- Tree and linked-list problems cannot be run: no test suite is built for code
  that takes a node (scenario 4 is `inconclusive`).
- The suite cache is per learner, deliberately, so a first-time learner on a
  problem without examples still depends on test synthesis succeeding.
- "Not verified in sandbox" code can be wrong. The label and the real
  Execution line are the only protection; that is the owner's decision.
- One earlier subject is remembered, not a history of them.
- Scenario 8: the profile carries across sessions and is cited, but the level
  of help did not change because no skill crossed a threshold.
- Round 2 is two large commits rather than one per fix.

## 11. Round 3 (2026-10-05): reliability, adaptivity, tree execution, latency

### R1: reliability -- NOT at the merge bar's sample size

**The five-runs-per-scenario measurement on one final commit was not
completed.** The Groq quota for the main model ran out part-way: three of the
five keys began refusing normal-sized requests with a `retry-after` of 7 to 15
minutes (a one-token request still succeeded, so it is a token quota, not an
outage). A full 5x pass is about 320 turns and roughly 1.6 million tokens,
which is more than the keys had left after the day's testing. Three attempts at
the full run were started and stopped; each also found a real defect, so the
code changed between them.

What was measured, all on round-3 code, with `eval/behavior/live_scenarios.py`
(the hand-scored checks of `docs/LIVE_BEHAVIOR.md` made programmatic):

| # | Scenario | Passed / runs, all round-3 commits | On the final commit `6a5877f` or its parent | Merge-bar check |
|---|---|---|---|---|
| 1 | Beginner, Two Sum | 6 / 7 | 2 / 2 | no full code on turn 1: **7 / 7** |
| 2 | KeyError debug | 5 / 6 | 1 / 2 (the failure is the regression fixed in `6a5877f`) | |
| 3 | Challenge mode | 5 / 5 | 2 / 2 | solution withheld on every turn: **5 / 5** |
| 4 | Max Path Sum | 3 / 6 | 2 / 2 | |
| 5 | BFS vs DFS | 5 / 5 | 2 / 2 | |
| 6 | Session M, 678 as text | 5 / 6 | 2 / 2 | no study plan on follow-ups: **5 / 5** completed runs |
| 7 | Vague phrasings | 2 / 2 | 1 / 1 | |
| 8 | Cross-session | 1 / 1 valid | 1 / 1 | |

Reference replay (`eval.behavior.replay`, 130 checks): 130 / 130 on `c69e1b7`
and 130 / 130 on `3bcd6ce` with the classifier on the smaller model. One run
each, not five.

So: the three must-be-5/5 checks did hold on every run that completed (7, 5
and 5 runs). Scenarios 7 and 8 have too few runs to say anything about a 4/5
bar. The table mixes commits and is evidence, not the certification the brief
asked for. To finish it, on a day with quota:

```powershell
.\venv\Scripts\python.exe -m eval.behavior.live_scenarios --runs 5 --pace 12 --base-url http://127.0.0.1:8000
```

It prints the per-scenario and per-check table and exits 1 if any check is
below the bar (all runs for the three strict checks, all but one otherwise).

Every failure, and what it was:

| Failure | Cause | Kind | Fix |
|---|---|---|---|
| Scenario 1, turn 4: no code after "I don't know" (1 run) | The reference-solution call was rate limited on every key, so "did not return usable code" | Capacity | `018faa9`, `d210d4d`: fail over at once, wait out a short throttle on the main model |
| Scenario 4: "no test cases" (3 runs) | The learner names the problem and pastes only code, so the turn depended on test synthesis, which is one large model call | Design, exposed by throttling | `3bcd6ce`: a named curated problem supplies its own examples; no model call |
| Scenario 2: fewer than 4 cases (1 run) | Side effect of the fix above: Two Sum's curated statement has two examples | Regression, caught by the next run | `6a5877f`: a suite of fewer than 4 example cases is topped up once with validated cases, then cached |
| Scenario 6: a turn timed out at 300 s (1 run) | Every Groq key refused; the turn went to the last-resort OpenRouter free model and hung | Capacity | Partly: `d210d4d` returns to the main model after 60 s instead of 300. A hung fallback call still has no timeout of its own |
| Scenario 8: 0 / 2 | Not the agent. The runner ran scenario 8 on scenario 7's account | Measurement bug | `6a5877f`. Those two runs are excluded above |

No failure in round 3 came from classifier flag variance. The guards added in
round 2 (`wants_the_code`, the subject-switch rules) held on every run.

### R2: can the profile change the level of help?

Yes for code the sandbox has judged; no for answers to questions alone.

Constants: `ALPHA` 0.3 (sandbox outcomes), `CONCEPT_ALPHA` 0.1 with
`CONCEPT_CEILING` 0.67 (graded answers), prior 0.50. Planner thresholds: weak
below 0.42, strong from 0.75, hard difficulty from 0.68.

| Evidence on one topic, starting at 0.50 | Events to cross | Lands at |
|---|---|---|
| Solved with no hints (score 1.0) | 2 to become strong | 0.755 |
| Solved with one hint (0.85) | 4 to become strong | 0.766 |
| Solved with two hints (0.70) | never: the estimate converges on 0.70 | 0.70 |
| A failed sandbox run (0.10) | 1 to become weak | 0.38 |
| Needed the full solution (0.30) | 2 to become weak | 0.398 |
| Correct answers to the tutor's questions (0.85) | never strong, never hard: capped at 0.67 by design | 0.67 |
| Wrong answers (0.20) | 3 to become weak | 0.419 |
| "I don't know" (0.25) | 4 to become weak | 0.414 |

So five short sessions CAN cross a threshold: two clean verified solutions on a
topic make a learner strong on it, and a single failing run makes them weak.
Scenario 8 did not cross one because scenarios 1 to 5 were mostly answers to
questions (capped at 0.67) spread over four topics, with at most one verified
run per topic. The adaptation is not too slow; what scenario 8 measured was
five conversations that produced little sandbox evidence.

The test the brief asked for is
`test_a_weak_and_a_strong_learner_get_different_help_on_the_same_problem`: the
same Two Sum ask for a learner seeded at 0.2, 0.5 and 0.85 on `hashing`.

| Skill | Assistance per turn | Difficulty | Concise | Hint ladder ceiling |
|---|---|---|---|---|
| 0.20 | `hint` | easy | no | lower |
| 0.50 | `concept` | medium | no | |
| 0.85 | `pseudocode` | hard | yes | higher |

Nothing was tuned. Two things for the owner to decide, neither applied:

1. **One failed run makes a learner "weak"** (0.50 to 0.38). That is fast for a
   first attempt. Raising `UNSOLVED_SCORE` from 0.10 to 0.25 would make it two
   failures (0.425, then 0.37). It would also slow the drop for a learner who
   really is weak by one event.
2. **A learner who always needs two hints can never be "strong"** (the score
   for that is 0.70, below the 0.75 threshold). Lowering `HINT_PENALTY` from
   0.15 to 0.10 makes two-hint solves score 0.80, reaching strong in 6. Whether
   a learner who needs two hints every time should be called strong is a
   teaching judgement, not a bug.

### R3: tree and linked-list execution

Done in `app/execution/node_adapter.py`, applied in `SandboxRunner.run` to the
code about to be sent. A function with a tree or list parameter (by annotation,
by what it reads on the parameter, or by a conventional name in a module that
touches `.left`/`.right`/`.next`) is wrapped so it can be called with a
LeetCode level-order list (`None` for a missing child, `[]` for the empty tree)
or a plain list, and a returned tree or list comes back in the same form. Only
the outermost call converts, so recursive solutions work. `TreeNode` and
`ListNode` are supplied when the submission does not define them.

The brief said to wire this into the harness. It is wired into the code sent to
the harness instead: the harness is baked into the sandbox image, and changing
it means rebuilding and re-tagging the image on every machine. The effect is
the same and the image is untouched. Statement examples written with `null`,
`true` and `false` are now read, and the synthesis prompt asks for the list form.

Tests: `tests/execution/test_node_adapter.py`, ten cases, the behavioural ones
in the real sandbox: empty tree, single node, right-skewed, left-skewed, a hole
in the middle, a returned tree, empty and non-empty linked lists in and out,
and the scenario 4 code getting a real `fail` (2 of 3) instead of
`inconclusive 0/0`.

Not covered: functions that modify a list or tree in place and return nothing,
N-ary trees, graphs given as node objects, and cyclic lists.

### R4: the latency cause

Confirmed: rate limiting, and specifically the tokens-per-minute limit.

Every provider HTTP attempt is now recorded (`app/llm/telemetry.py`): status,
seconds, credential index and Groq's rate-limit headers. Eleven turns
(scenarios 1, 6 and 4, one run) on five Groq keys, before any fix:

| | |
|---|---|
| HTTP attempts | 27: 17 succeeded, 8 were `429`, 2 were `503` |
| Limit reported by the headers | `x-ratelimit-limit-tokens: 8000` per minute per key; `x-ratelimit-limit-requests: 1000` |
| `retry-after` on the 429s | 2, 2, 12, 178, 5, 4, 13 and 688 seconds |
| Provider time in successful calls | 55.8 s (59%) |
| Provider time waiting after a 429 | 38.1 s (41%) |
| Successful calls with under 2000 tokens left in the window | 6 of 17 |
| Slowest successful call | 13.6 s, on a key with 73 tokens left |

Two mechanisms:

1. **The SDK sleeps.** `ChatGroq` retries a 429 itself (`max_retries=2`),
   sleeping for `retry-after` first, before the failover client ever sees the
   error and moves to the next key. That is the 41%.
2. **8000 tokens a minute is about two tutor turns per key.** A classifier call
   plus a solver call is roughly 4000 tokens. Five keys give about ten turns a
   minute in total, and a test run exceeds that.

The 429s are not only slow. A code reveal makes one extra call, and when that
call was rate limited on every key the turn came back "did not return usable
code" (scenario 1, turn 4, in the telemetry run).

Applied (`018faa9`): with more than one credential, the SDK's retries are off
and a 429 fails over immediately. With a single key nothing changes.

Proposed, not applied: `LLM_CLASSIFIER_MODEL=openai/gpt-oss-20b`. Groq's limits
are per model, so the classifier would get its own 8000 tokens a minute and the
main model would keep its budget for the solver. The setting exists and is off.

### Streaming

Not enabled, and not possible as asked without a design change. The reply the
learner reads is assembled after the model call from validated JSON: the solver
returns `guided_step`, `pattern_slug` and the rest as one object, the step is
rejected if it contains code, and symbols are escaped. There is no stream of
reply tokens to forward, and forwarding the raw JSON stream would bypass the
check that keeps code out of a hint. `/chat/stream` already streams the
pipeline's stages as they complete. A real fix would be a second, streamed call
that only words the already-decided step; that adds a call per turn on a
provider whose limit is tokens per minute, so it would make latency worse here.

### R4 continued: what was applied, and the classifier model

Applied:

- `018faa9`: with more than one credential the SDK's own retries are off.
- `d210d4d`: when every key for the main model is throttled and the shortest
  `retry-after` is 15 s or less, that wait is taken once and the call retried
  on the main model. The failover cursor returns to the first key after 60 s
  (was 300 s). Before this, one burst of 429s pinned every turn to the
  fallback model for five minutes, which is where the 30 to 60 s turns in
  section 10 came from.

Effect, paced runs on the final code: median turn 3.8 to 6.0 s, p90 9.4 to
11.3 s, slowest 12.0 to 27.8 s, 1.3 to 1.7 model calls per turn, with keys
still being refused throughout. The 15 s target for a tutor turn is met at the
p90 when the load is paced; it is not guaranteed, because it depends on quota.

Measured for the proposal, not turned on: `LLM_CLASSIFIER_MODEL=openai/gpt-oss-20b`.

| | Classifier on the main model | Classifier on `gpt-oss-20b` |
|---|---|---|
| Replay, routing and behaviour checks | 130 / 130 (`c69e1b7`) | 130 / 130 (`3bcd6ce`) |
| Classifier call, median / p90 | 1.84 s / 6.2 s | 1.04 s / 1.7 s |
| Classifier calls rate limited | shares the main model's quota | 0 of 17 |

Recommendation: turn it on, after one more replay and one `live_scenarios`
pass with it enabled. One run of 130 checks is not enough to call the smaller
model's routing equal, and the eight scenarios were not run with it at all. It
removes about a third of the main model's token use and takes the classifier
out of the queue.

### Verification on the final commit (`6a5877f`)

| Check | Result |
|---|---|
| `pytest tests` (Postgres, Qdrant, Docker up) | 1904 passed, 2 skipped (opt-in live LLM) |
| `pyright app tests` (strict) | 0 errors |
| `ruff check .`, `ruff format --check .` | clean |
| `alembic check` | no pending operations |

### Remaining risks after round 3

- Reliability is shown on 1 to 7 runs per scenario across several commits, not
  5 on one. Run the command above before merging.
- A turn that reaches the last-resort provider can hang until the client's
  timeout. The fallback call needs its own time limit.
- Free-tier quota is the binding constraint on latency AND on correctness of
  the turns that need a second large call (a code reveal, test synthesis).
- The suite top-up adds one model call the first time a learner submits code
  for a statement with fewer than four examples.
- Tree and list support covers functions that take or return a root or head.
  In-place mutation with no return value is not handled.
- Skill thresholds are unchanged; two proposals above await a decision.

## 12. Concept explanations (2026-10-05)

Reported: "explain the concept of recursion with examples" was refused ("the
references don't cover recursion"), retrieval returned dynamic-programming
pages, and the reply was labelled "Mental model: Dynamic programming".

### Why recursion matched dynamic programming

The corpus has no page on recursion. It has thirty pattern pages, and the DP
pages are the ones that talk about recursion most, so they rank first. They
rank first with a LOW score, and nothing looked at the score: the only floor
was the one for problem statements, -5.0.

Measured on the live retriever (reranker scores, top hit):

| Question | Top hit | Score |
|---|---|---|
| what is a trie | trie, Overview | 9.04 |
| explain binary search | binary_search, Overview | 7.47 |
| what is a hash map | hashing, When to Recognize It | 5.16 |
| explain the concept of dynamic programming with examples | dynamic_programming, Overview | 4.66 |
| what is recursion | dynamic_programming, Identification Signals | 1.01 |
| **explain the concept of recursion with examples** | dynamic_programming, When to Recognize It | **-1.19** |
| explain big O notation | two_pointers, Complexity | -3.48 |

For the reported query the five hits were -1.19, -1.59 (trees), -1.65 (dfs),
-2.21 and -4.51 (both dynamic_programming). Questions the corpus covers score
4.4 and up; questions it does not cover score 1.0 and down.

### Fixes (`fix(explain)` commit)

| # | Change |
|---|---|
| 1 | `answer_concept` no longer refuses. With no relevant reference, or when the model reports it used none of the ones it was given, the tutor answers from its own knowledge with a prompt that forbids "I cannot answer" and pitches to the learner's level (beginner / intermediate / advanced, from the plan's skill). The reply ends with a one-line note that it is not from the curated material. A study plan is still built only from the curriculum |
| 2 | New floor `MIN_CONCEPT_TOPIC_SCORE = 2.5` for concept questions, in the middle of the measured gap. A hit below it is "no hit": it is not offered as a reference. The statement floor stays at -5.0, because a problem statement scores far lower than a short question against the same pages. A hit that was never reranked is not judged by either floor (its score is on another scale) |
| 3 | The topic of a concept question comes from retrieval only on a hit at or above that floor; otherwise the turn has no topic, so no topic card and no learning event under the wrong pattern. The recursion question is now unlabelled, not "Dynamic programming" |
| 4 | Examples are a separate field of the model's reply, never part of the answer text. Each is run in the sandbox (script mode) and shown with the output it actually produced. One that crashes, times out or is rejected is left out and the reply says so. With no sandbox the code is shown labelled "not executed" |

### Every explanation is detailed and comes with examples (owner, 2026-10-05)

Asked for during the fix: not only recursion -- any topic the learner asks to
have explained gets a detailed explanation with examples. Both concept prompts
(grounded and own-knowledge) now share one rule for depth and one for examples:

- **Depth:** what it is; the intuition; a step-by-step walk through one small
  concrete input; when to use it and when not to; its time and space cost with
  the reason; one common mistake. Pitched to the learner's level.
- **Examples:** one or two short runnable programs on every explanation, asked
  for or not, each run in the sandbox and shown with its real output.
- **Still held back:** advanced variants and neighbouring techniques, unless
  asked. Detail is depth on the topic asked, not breadth (target behaviour
  section 25). This replaces the "two short paragraphs" rule from ERR-007.
- The grounded prompt no longer tells the model to say the references do not
  cover something; it returns `used: []` and the tutor answers from its own
  knowledge.

Live, on the final prompts:

| Question | Grounded | Length | Examples run |
|---|---|---|---|
| explain the concept of recursion with examples | no (note shown) | not measured; the replay's checks passed | at least 1 |
| explain big O notation | no (note shown) | 3,434 chars | 2 |
| explain binary search | yes, `binary_search` | 3,453 chars | 2 |
| what is a linked list | yes, `linked_list` | 3,409 chars | 2 |
| explain how a heap works | yes, `heaps` | 1,203 chars | 0 |

The heap row is a failure worth knowing about, not a prompt problem: the model
call was rate limited, and the existing fail-soft path printed the two
reference excerpts instead. The same question answered in full (3,200 and
3,800 chars, parsed) when called again. A detailed answer is about 1,200 to
1,500 output tokens against 300 before, so explanations now use more of the
8,000-tokens-a-minute budget and meet that limit sooner.

Not done: the corpus still has no recursion page. Adding one would ground the
answer; it is content work, not a code fix. Item 3 means such a question
carries no label at all rather than a "Recursion" label, because the label
vocabulary is the corpus's pattern list.

One thing to know: a concept example is shown on a turn whose plan is below
`full`. That is deliberate (it illustrates a concept, it is not the solution to
the learner's problem), but it is a place where code reaches the learner
outside the hint ladder's gate.

### Verification

- `tests/graph/test_concept_explanations.py` (7), `tests/agents/test_concept.py`
  (3 new): recursion is explained and not refused, is not labelled DP, its
  examples go through the sandbox, the no-sandbox label, binary search stays
  grounded and cited, the floor sits in the measured gap.
- Replay: `adaptive_006` (recursion) and `adaptive_007` (binary search) added.
  The replay total is now 148 checks, was 130. Live on the final prompts:
  18 / 18 on the two new conversations. The other five were not re-run.
- Live reply for the reported query: three paragraphs on recursion with a base
  case and the stack-overflow pitfall, a factorial and a Fibonacci example each
  with its sandbox output (120 and 21), the not-from-the-corpus note, no topic
  label, 2 model calls, 3.2 s.
- `pytest` 1913 passed, 2 skipped; `pyright` strict 0 errors; `ruff` clean.

## 13. Follow-ups belong to the last reply (2026-10-06)

Reported, one conversation: a screenshot problem (LeetCode 678); the next day
"give roadmap to master stack,queue" got a roadmap; "first where should i
start" got Hint 2 of 4 on the problem from the day before; "im asking about the
roadmap" got a new 22-week plan for all of DSA; the plans printed "[14] Stack
family" and citation brackets. The owner's verdict: not one bad pattern, the
agent as a whole does not behave as planned.

### The cause is structural, not one rule

Every earlier fix in this report repaired one misreading. They share a cause:

1. **The conversation had one slot.** The only thing a turn could follow up on
   was the stored problem or code. A plan or an explanation was never a
   subject, so a follow-up to one fell through to whatever problem was stored,
   however old.
2. **Only the hint-ladder agent saw the conversation.** The explanation and
   study-plan prompts received the current message and nothing else, so
   "im asking about the roadmap" was answered as a first message.
3. **The classifier's "refers to previous" meant two things.** "The previous
   thing" was the active problem in the code and the last reply in the model's
   reading.
4. **Plans were built for nobody.** The study-plan prompt had the curriculum
   but not the learner: no skills, no recurring mistakes, and a rule to use
   ONLY the curriculum's families, which is how "Union-Find as a group queue"
   ended up in a queue roadmap.

### What changed

| | Change |
|---|---|
| Thread | The conversation records what the tutor's last reply was (`SessionProgress.last_thread`: a step on the problem, a plan, an explanation). A follow-up with no problem or code of its own belongs to that reply. It goes to the stored problem only when the classifier, shown both, says so, or when the message brings a problem or code |
| Classifier | It is told what the last reply was (`last_reply`) and returns `continues_last_reply`. With no confident reading the default is the last reply, not the older problem |
| History | A follow-up's agent gets the recent exchange (`<conversation_so_far>`, last four messages) and is told to answer the follow-up in that context instead of restarting |
| Plans | Scoped to what was asked. Topics the curriculum lacks are covered from the model's own knowledge instead of being mapped onto an unrelated family. The learner's profile (weak, strong and practised topics, recurring mistakes) is in the prompt and the plan says how it was adapted, or what level it assumed |
| One question | Explanations and plans end on one question ("Your turn"), cut to the first if the model writes several |
| Markers | Reference numbers, "Family [14]", citation brackets and trailing backslashes are removed from the reply |

### Verification

- `tests/graph/test_reply_threads.py` (7): the reported conversation with the
  classifier answering as it did live; an explanation follow-up; the plan's
  profile block; one closing question; marker stripping.
- Live, the reported conversation plus three more turns, one conversation:

| Turn | Before | Now |
|---|---|---|
| 678 problem | hint | hint on the open-count range |
| give roadmap to master stack,queue | 5-week plan with unrelated families | 2-week plan for stacks and queues, states its assumed level |
| first where should i start | Hint 2 of 4 on 678 | "First step: Stack fundamentals ..." from the roadmap |
| im asking about the roadmap | 22-week plan for all of DSA | continues the stack-and-queue roadmap |
| explain sliding window with examples | (worked) | explanation with run examples |
| can you give one more example | not tested before | a second sliding-window example |
| ok back to the parenthesis problem, next hint | not tested before | the next hint on 678 |

- `pytest` 1920 passed, 2 skipped; `pyright` strict 0 errors; `ruff` clean.
- The five-run reliability measurement and the replay were NOT re-run after
  this change. It alters routing for every follow-up, so both should be.

### What this does not make true

- One live pass of one conversation is not a pass rate. The classifier's
  reading still varies between runs.
- The debugger and the code explainer still do not get the conversation.
- The reply is still assembled from sections; it reads as a report more than
  as a tutor talking. Changing that is a redesign of the response layer, not a
  routing fix.
- Skill moves only on sandbox evidence and graded answers, so "adapts to you"
  is slow to become visible (section 11).

## 14. One decision per turn, evidence, adaptation, voice (2026-10-09)

Evidence and the grouped root causes are in `docs/BEHAVIOR_GAP.md`: 48 real
assistant turns from the three human accounts, 39 scored BAD, 25 of them from
the turn's subject and action being worked out in five places.

### What changed

| Cause (BEHAVIOR_GAP) | Change | Commit |
|---|---|---|
| RC-1, RC-2: no single decision; rule lists route | `decide_turn` node and the `TurnDecision` record (subject, evidence, move, scaffold, wants_code, withhold). The `route` node is gone. An unsure reading keeps the conversation; an agent must have its subject; a response is not a code demand; "no code yet" is kept until the code is asked for; an unsure reading still acts on a statement, an error or a named problem | `eab797b`, `6bc4646`, `d8e4021`, `ddfcebe` |
| Profile gets no evidence | Soft evidence: `tutor_reply`, `help_needed`. Never sets `solved`, never reaches the hard band | `356be81` |
| RC-5: the ask never reaches the code writer | The reference prompt honours an asked-for approach and returns the idea and cost of the code it wrote; edge cases join the statement's examples | `36e72b8` |
| Adaptation is a label | `app/tutoring/adaptation.py`: pitch, representation, skip-ahead, recurring mistake; notes only for what the reply did; stored per message (migration `d7e8f9a0b1c2`) | `4595194`, `5bd3183`, `6162952` |
| RC-2: agents without the conversation | `<tutor_state>` and `<conversation_so_far>` in the debugger, reviewer and code explainer | `9c90f29` |
| RC-3: assembled reports | `app/response/voice.py`; the UI joins spoken sections | `ad5c8ae` |
| RC-4: failures as refusals; debugger gaps | A reply with no code is asked for again, then the solver's code is shown unverified; a bare traceback is read; `input()` is not a bug; a reply to a diagnosis gets a verdict | `36e72b8`, `22552b2` |
| Review | Credit only a changed line; Challenge keeps the move a step | `7f93404` |
| Unrelated, found on the way | Activity hours truncated in UTC (a test failed between 00:00 and 00:30 UTC) | `4a2b13c` |

RC-6 (a pasted method body blamed for its indentation) was already fixed by
the round-2 snippet repair; scenario 19 now checks it.

### Live measurement: NOT at the merge bar's sample size

`eval.behavior.live_scenarios --runs 5 --pace 12`, 20 scenarios, commit
`d8e4021`. **The Groq quota ran out during run 3.** Two runs are valid.

| Run | Scenarios passed | Turns the classifier fell back to keywords |
|---|---|---|
| 1 | 19 / 20 | 1 of 70 |
| 2 | 19 / 20 | 1 of 70 |
| 3 | 11 / 20 | 10 of 70 |
| 4 | 6 / 20 | 32 of 70 |
| 5 | 6 / 20 | 30 of 65 |

Runs 3 to 5 measure the provider being unavailable, not the agent, and are
not counted. Over the two valid runs:

| # | Scenario | Passed | Note |
|---|---|---|---|
| 1 | Beginner, Two Sum | 2 / 2 | no full code on turn 1: 2 / 2 |
| 2 | KeyError debug | 2 / 2 | |
| 3 | Challenge mode | 2 / 2 | solution withheld: 2 / 2 |
| 4 | Max Path Sum misconception | 2 / 2 | |
| 5 | BFS vs DFS | 2 / 2 | |
| 6 | 678 as text | 2 / 2 | no study plan on follow-ups: 2 / 2 |
| 7 | Vague phrasings | 2 / 2 | |
| 9 | Owner: 678, roadmap, follow-ups, code, code using stack | 2 / 2 | follow-up about the roadmap: 2 / 2 |
| 10 | Owner: shared code, asks for the code, another method | 2 / 2 | |
| 11 | Owner: explanations and a follow-up | 2 / 2 | |
| 12 | Owner: greetings, a question about the chat | 2 / 2 | no study plan unless asked: 2 / 2 |
| 13 | Stuck three times | 2 / 2 | |
| 14 | Code on the first ask; profile moves | 2 / 2 | |
| 15 | Ref 6: explain, no final code yet | 1 / 2 | run 2: the fix line was stated on turn 1 |
| 16 | Ref 7: independent challenge | 2 / 2 | solution withheld: 2 / 2 |
| 17 | Ref 9: a traceback | 2 / 2 | |
| 18 | Ref 10: code after understanding | 2 / 2 | |
| 19 | Owner: pasted method body | 2 / 2 | |
| 20 | Ref 8: the same mix-up again | 1 / 2 | run 1: a model call failed and the reply was the generic fallback |
| 8 | Cross-session | 2 / 2 | |

The four must-be-every-run checks held on both valid runs. Two runs cannot
show a 4-of-5 rate for anything. The record is
`eval/results/live_scenarios_d8e4021_5runs.json` (git-ignored).

Commits after the measured one: `7f93404`, `4a2b13c`, `ddfcebe` and the one
that adds this section. `ddfcebe` was checked live on scenarios 12 and 14
only, one run each; both passed. The others were not run live.

`eval.behavior.replay` was not run: no quota left.

### The local-model fallback

Tried as asked, with no code change and without editing `.env`: a second
server process with the Groq keys blanked in its environment and the existing
OpenRouter client pointed at Ollama's OpenAI-compatible endpoint
(`OPENROUTER_API_BASE=http://127.0.0.1:11434/v1`, `LLM_MODEL=qwen2.5-coder:7b`).
It works, but the model runs mostly on CPU on this machine (2.3 GB of 5.1 GB
on the GPU): the first tutoring turn took about four minutes and the second
had not finished after eighteen. A run is 70 turns. It was stopped and no
local numbers are reported. `qwen3.5:9b` was not used: its first call timed
out through the same client.

### What reading the live runs found that the tests did not

Every item below had green unit tests when it was found.

| Seen live | Cause | Fix |
|---|---|---|
| "Add 1." (the learner's answer) produced a verified `add_one` function | A solve turn with no subject | An agent must have its subject (`6bc4646`) |
| The same reply twice under a badge "I drew it out" | The question bank's fixed text ignored the adaptation | A repeated miss goes to the solver's step; notes only for what the reply did (`6bc4646`, `6162952`) |
| "this one is a worked example" over the full solution | The note was decided before the reply | `Adaptation.as_delivered` (`6162952`) |
| A follow-up re-explained, a plan rewritten, a diagnosis repeated | "Answer follow-ups briefly" was a side rule | The follow-up has its own prompt (`6bc4646`, `22552b2`, `6162952`) |
| "It fails: EOFError" for a program calling `input()` | The sandbox has no keyboard | `22552b2` |
| A traceback answered "no code was executed" | The debugger read only code | `22552b2` |
| The tutor linked a repeated mistake but the badge did not say so | A non-breaking hyphen in "mix-up" | `d8e4021` |
| A pasted problem statement got "I can't tell what that refers to" | Quota gone, keyword fallback, unsure label | `ddfcebe` |

### What still does not match the target

- Reliability is unmeasured at the required size: two valid runs.
- "Don't give me the final code yet" is honoured by a prompt instruction. In
  one of two runs the model stated the corrected line anyway. The patch and
  the code block are withheld in code; a line quoted inside a sentence is not.
- A model call that fails on an explain turn still yields "I wasn't able to
  put together a full response" (scenario 20, run 1).
- When the provider is unavailable the tutor now takes a first step instead of
  asking what the message refers to, but that step is the generic ladder
  text: the solver's call fails too.
- The curated question bank still answers in fixed text. It is handed to the
  solver only after a repeated miss.
- A wrong complexity claim was seen from the code explainer ("O(2^n)" for a
  tree recursion that is O(n)): `estimate_complexity` counts two recursive
  calls as exponential. Not fixed.
- One failed sandbox run still makes a learner "weak" on a topic, and a later
  reply then says "your record says to take it slowly". The threshold
  question from section 11 is still open.
- "hii agent" in the middle of a conversation was answered with the problem's
  name instead of a greeting (scenario 12, turn 4; no check covers it).
- Other languages: Python only, by the owner's decision.
- Reference conversation 9 was run with the traceback as text; the image path
  was not exercised.
- The live scenario checks are string and route checks. Several wrong replies
  passed them before they were tightened; more probably still can.
