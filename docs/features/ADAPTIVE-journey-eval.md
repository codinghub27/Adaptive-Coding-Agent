# FEATURE — Journey evaluation and agent upgrade (10-session learner journey)

## Status
Phase A done except the three-run spread (A5: one valid cloud run; the local
runs did not fit, see A5). Phase B (2026-10-14): packets I0, I4/I5, E, I1, I2
(part), I3 (part), I7 (part), I10 landed (`d3a6a2e`, `fd0044f` and the final
commit); hard gates clean on the cloud model; L2 95.3% once on the cloud; L1
and the profile targets NOT met. Branch `feat/journey-eval`, not pushed.

Single session, single agent, no subagents. This file is the source of truth
between packets.

## Goal
Make the agent behave like the 10-session journey in
`eval/behavior/journey/adaptive_journey_10.jsonl` (one learner, 30 days, from
"confused intermediate" to "focused problem-solver"), measured on one fixed
instrument, on a dev set and a held-out set.

## Prerequisites (brief, section 2) — 2026-10-14

Read from `ADAPTIVE-upgrade.md` and `ADAPTIVE-tutoring.md`.

| prerequisite | status in the earlier log | commit | verdict |
|---|---|---|---|
| P1 conversation continuity | Done, measured (knows_problem 12/12, same_topic 6/6) | `5c48d92` | done |
| P4 escalation per teaching mode | Done, measured | `2c0757a` | done |
| P5 honest UI steps | Done (10/10 same evidence path, provenance populated, 0 phantom steps) | in `ADAPTIVE-upgrade.md` | done |
| Q2 answer grading | 49/50 on the labelled set, 0/5 injections graded correct | Q1–Q6 commit | done |
| Q4 conceptual evidence | `concept_check` events, CONCEPT_ALPHA rule, never past HARD | same | done |
| Q5 misconceptions | 15/15 on catalog fixtures, 0 free-text ids | same | done |
| Q6 code-submission loop | Ex3 and Ex5 pass | same | done |

No check is tagged "blocked by a packet". Two things the earlier logs do not
cover are tagged on their own:

- **`blocked_by: catalog`** — the closed misconception catalog
  (`app/knowledge/tutoring/misconceptions.json`, 8 entries) has no entry for
  two of the four dev misconceptions (`sliding_window.left_pointer_moves_backward`,
  `binary_search_on_answer.floor_instead_of_ceil`) nor for the two held-out
  ones. Those checks cannot pass until the catalog grows (a Phase B decision).
- **`blocked_by: no_resolved_state`** — the profile stores a count per mistake
  and has no "resolved" state, so "marked resolved" (sessions 6 and 10) cannot
  be recorded yet (brief I2).

**The architecture moved after those logs were written.** Since 2026-10-09
(`CLAUDE.md`, "How a turn actually works") one `decide_turn` node settles the
turn; `discuss` (talk), `assess` (knowledge check) and bug exercises are new
routes. The Q-packet machinery (pending check, grader, bank) is still there,
but a reply to a question the MODEL wrote (not a curated one) is read by the
dialogue agent and lands on `TurnDecision.evidence`, not on `AnswerGrade`. The
`decision.grade` field below reports both, with its source.

## Owner decisions

- **OD-1 (2026-10-14): a session reads its own conversation only; the profile
  carries everything else.** The agent is given the CURRENT conversation, never
  the messages of other sessions. The profile is updated on every turn, so a
  new session knows the learner through it. Consequence for Phase B: spaced
  recall (I2), evidence-based reassurance (I4) and the progress review (I6)
  must be built from PROFILE facts (skills, their last-evidence stamps, recorded
  mistakes and when they were last seen, exposure-only topics, preferences).
  Anything a later session needs must therefore be written to the profile when
  it happens; a fact that lives only in an old transcript is not available.
  The replay already works this way (one account, a new conversation per
  session).

## Deviations from the brief (stated plainly)

1. **Dataset location.** The files were at `eval/behavior/adaptive-dataset/`
   (untracked). Its own README says "put this folder at
   `eval/behavior/journey/`" and the brief uses that path, so the folder was
   renamed. A hyphenated folder also cannot be imported.
2. **LLM provider.** The brief's judge note says "agent calls stay on local
   Ollama". `main` runs on Groq/OpenRouter (`CLAUDE.md`; the Ollama port lives
   on `experiment/local-ollama`), and the owner's instruction for this session
   is: cloud keys are the product path, the local model
   (`eval/local_model_bridge.py`, `qwen-direct`, thinking off) is a fallback
   for evaluation ONLY when the keys are rate-limited, and the cloud path is
   restored afterwards. So the baseline runs on the cloud keys. `.env` is not
   touched by the bridge. `docs/features/LLM-ollama-local.md` does not exist on
   `main`.
3. **`verify_examples.py` is never run on the host.** It `exec`s snippets.
   The harness runs it in a throwaway container (`python:3.11-slim-bookworm`,
   no network, read-only root, all capabilities dropped) and compares the
   result with `sandbox_ground_truth.json`.
4. **`ruff format` touched the dataset's own scripts** (`build_dataset.py`,
   `render_md.py`, `verify_examples.py`): layout only. The verifier's output
   still matches the stored ground truth (12/12 snippets). They still carry
   ~80 lint findings of their own (long lines); CI lints `eval/`, so I10 needs
   either a per-file ignore or a cleanup. Not changed here.
5. **`code-review` plugin** fans out to subagents, which the brief forbids; the
   diff is self-reviewed before each commit (same decision as
   `ADAPTIVE-tutoring.md`).

## Map (file:symbol)

| concern | where |
|---|---|
| eval switch | `app/config.py:Settings.eval_mode`, `_refuse_eval_mode_in_production` |
| request helpers | `app/evalmode.py:eval_mode`, `eval_clock` |
| clock | `app/clock.py:now`, `override`; read by `app/memory/events.py:record_event`, `app/memory/profile.py:to_view`, `app/tutoring/bank.py` (pending `created_at`) |
| decision record | `app/schemas/eval.py:EvalDecision`; built by `app/graph/eval_view.py:build_eval_decision`; returned as `ChatResponse.decision` (`app/graph/api.py`) |
| profile at a date | `app/memory/api.py:read_profile` (`eval_now` query, EVAL_MODE only) |
| vocabulary map | `eval/behavior/journey/vocab_map.json` |
| checks L1 / L2 | `eval/behavior/journey/checks.py:l1_checks`, `l2_checks`, `HARD_GATES`, `self_check` |
| judge L3 | `eval/behavior/journey/judge.py:Judge`; calibration `calibrate.py` |
| replay | `eval/behavior/journey/replay_journey.py:replay`, `trajectory`, `summarize`, `aggregate` |
| held-out set | `eval/behavior/journey/heldout/` (`verify_examples.py`, `build_dataset.py`, `heldout_journey_10.jsonl`, `sandbox_ground_truth.json`) |
| results (git-ignored) | `eval/results/journey/` |

## A0 — Vocabularies (`vocab_map.json`)

- **Topics:** all eight dataset topic ids exist as corpus slugs under the same
  name. No corpus label was renamed.
- **Misconceptions:** 2 of 4 dev ids map to the catalog; 2 have no entry (see
  above). Held-out: 2 of 4 map.
- **Intents:** same names on both sides; compared exactly.
- **Routes:** five equivalences, each with its reason in the file. The harness
  reports `route_strict` next to `route` so an equivalence cannot hide a miss.
  - `dsa` ≡ `debug` when the learner's turn carries code (user-code-first).
  - `explain` ≡ `debug` when the turn carries code (a question about working
    code is answered from the debug node).
  - `dsa` ≡ `practice` (both directions) when the learner asks for a problem:
    the dataset itself labels the same ask `dsa` in session 2 and `practice` in
    session 6.
  - `clarify` ≡ `discuss` / `assess`: the dataset's `clarify` is "a
    conversational turn that asks the learner something"; since 2026-10-13 the
    agent calls that talk or a knowledge check, and its own `clarify` means "a
    first message nobody could read".
- **Assistance levels:** scored exact (`hint` ≠ `concept`); the band
  (no-code / structure / full) is reported beside it.
- **Grades:** `AnswerGrade.grade` when a curated question was graded, else
  `TurnDecision.evidence` mapped (`correct`; `terminology_error`,
  `partially_correct`, `incomplete` → partial; `conceptual_misconception`,
  `implementation_error`, `incorrect` → incorrect; `stuck` → dont_know).
- **Calibration differences** (reported, never patched in the harness): a
  failed run scores `UNSOLVED_SCORE` 0.25 in the agent and 0 in the dataset;
  the agent decays skills (30-day half-life) and the dataset does not; a
  handed-over solution lowers the family estimate slightly in the agent and
  moves nothing in the dataset.

## A1 — Eval-only decision metadata

- `EVAL_MODE` (default off). `Settings` refuses `EVAL_MODE=true` with
  `APP_ENV=prod` at startup.
- `/chat` (auth as before) returns `decision` when it is on: intent (label,
  confidence, source), route, move, subject, scaffold, `wants_code`,
  `withhold`, reasons, teaching mode, plan (difficulty, the assistance level
  the reply was RENDERED at, hint rung, topic, skill, rationale), grade (with
  its source), pending check (kind, topic, id), sandbox (status, passed/total,
  first failing case with the sandbox's own expected/actual, provenance,
  whose code, and the patch's verdict separately), evidence events,
  misconception ids, memory facts used (fixed keys), escalation, skill deltas,
  adaptation notes, `last_thread`, and the clock the turn ran on. No learner
  text: asserted in `tests/graph/test_eval_mode.py`.
- `sandbox` is the verdict on the LEARNER's code (`initial_verdict`), never the
  patch's; the patch's status is `final_status`.
- Clock: `eval_now` form field on `/chat` and query on `GET /profile`, 403
  unless `EVAL_MODE` is on. It is a `ContextVar` for that request only.
  `/chat/stream` returns `decision` too but takes no clock.
- No behaviour change with the switch off: the three clock reads now go
  through `app.clock.now()`, which is `datetime.now(UTC)` without an override.
- `memory_used` lists only facts the state shows were used: recurring mistakes
  the adaptation linked, surfaced misconceptions, and `skills.<topic>` when the
  plan's rationale is `weak_skill` / `strong_skill`.

## A2 — Replay harness (`replay_journey.py`)

- One fresh account for all ten sessions; each session is a new conversation
  with the record's teaching mode; the clock is placed so that day 30 is today.
  The `topic` form field is never sent.
- Before a replay the dataset's snippets are re-run (in a container) and must
  equal the stored ground truth, or nothing is replayed.
- **L1 state** (only for keys the expected `meta` carries): intent, route
  (strict and equivalent), assistance level (exact and band), difficulty,
  grade, misconception id, evidence provenance (+ `solved` for sandbox
  evidence, + "no outcome, help recorded" for exposure), sandbox verdict
  direction, whose code the verdict is on, a quoted failing case that matches a
  ground-truth failing case, escalation reason, memory used.
- **L2 response:** one question when a question is pending; `hint_safety`
  (code only at `full` or on a recorded escalation, against the agent's OWN
  plan); no solution code where the dataset's level is below `full`; every
  "N/M pass" equals the decision's sandbox counts; no pass claim without a
  verdict; no corrected code before the learner's fix (on the turn that reports
  a failing run, ANY statement of code that is not the learner's own counts);
  180 words unless full solution or progress review; no filler praise; memory
  facts used exist in the profile.
- **Hard gates** (`checks.HARD_GATES`): `hint_safety`, `pass_claim_backed`,
  `solved_only_from_learner_sandbox`, `revealed_code_verified`,
  `revealed_code_no_credit`.
- **Divergence** is scored apart: a question was expected and none was asked;
  code was requested from the learner and the reply showed the solution; the
  reply set a DIFFERENT problem than the one the scripted learner goes on to
  solve; or the judge says the next scripted message no longer fits. Turns
  after a different problem was set carry `off_script`.
- **Profile trajectory** after each session: label agreement and absolute error
  per topic against `profile_after`, and each expected mistake.
- **Degraded turns** (a node error, a fallback intent, the safe-fallback reply)
  are counted so a rate-limited turn is not read as a behaviour.
- **Instrument self-check:** `python -m eval.behavior.journey.checks` runs the
  text checks on the dataset's own reference replies. Dev: 172/174; held-out:
  173/174. The misses are reference turns that ask two questions (an
  interviewer's paired follow-up; session 7 turn 1). On those turns the
  reference's own count is the ceiling for `one_question`.
- The judge is a separate pass (`--judge-only`): replay and judge share one
  provider quota, and judging inline rate-limits the agent being measured.

## A3 — Judge calibration (`calibrate.py`)

Twenty hand-labelled turns: six reference replies, two good replies in
different words (must not be marked down for not matching), twelve written
failures (a patch instead of a question, a false pass claim, empty praise and
a lecture, a solution handed to a learner who asked not to get one, a cold
reply, flattery with an invented history, an ignored stored mistake, a refused
explicit ask in Guidance, unearned skill credit, an unrequested hint in
Challenge, a revealed pattern, a correct but overlong review). Gate: within 1
point on ≥80% of (turn, dimension) pairs and per-turn mean within 1 on ≥80% of
turns.

Result (2026-10-14, `eval/results/journey/judge_calibration.json`): judge
`groq/openai/gpt-oss-120b` (the strongest model on the keys; cloud, used for
JUDGING only). **Calibrated: 90.8% of (turn, dimension) pairs within 1 point,
100% of turns within 1 on the mean** (gate 80% / 80%). Mean on the good items
4.71, on the written failures 2.14.

- First attempt: 19 of 20 items came back unparsed. `gpt-oss` is a reasoning
  model and `max_tokens=400` was spent on reasoning, leaving no JSON. Raised
  to 2000 in `judge.Judge.score`; the instrument is otherwise unchanged.
- Known bias: the judge is lenient on a correct but long review (the
  overlong-review item: labelled 2.7, judged 3.5, `concise_one_idea` 1 vs 2).
  The L2 `length_budget` check covers length; L3 is not used to judge length.

## A4 — Held-out set (`heldout/`)

Same arc, behaviour tags, topics and label trajectory; different problems,
paraphrased learner turns, new buggy/fixed code.

| # | dev problem | held-out problem | held-out bug |
|---|---|---|---|
| 2 | Longest Substring Without Repeating Characters | Minimum Size Subarray Sum | shrinks with `if`, not `while` (1/5 → 6/6) |
| 3 | Max Consecutive Ones III | Fruit Into Baskets | none (5/5) |
| 4 | Koko Eating Bananas | Capacity To Ship Packages Within D Days | search starts below the heaviest package (3/4 → 5/5) |
| 5 | Diameter of Binary Tree | Binary Tree Tilt | returns the tilt where the parent needs the sum (1/4 → 4/4) |
| 6 | Binary Tree Maximum Path Sum | Longest Univalue Path | arm not reset when values differ; page examples pass (3/4 → 5/5) |
| 7 | Course Schedule | Parallel Courses | none (5/5) |
| 8 | Coin Change | House Robber | agent reference (5/5) |
| 10 | Sliding Window Maximum | Largest Rectangle in Histogram | none (5/5) |

12 snippets, run in a container; `build_dataset.py` validates every "N/M pass"
line and every quoted failing output against that run. 10 conversations, 96
turns. **Never tune on it.**

## A5 — Baseline

### What happened to the cloud baseline (2026-10-14)
`dev --runs 3` on the cloud keys (Groq `openai/gpt-oss-120b`, 5 keys, OpenRouter
behind them):

| run | turns that got a model reading | wall time | status |
|---|---|---|---|
| 1 | 46 / 48 | 24 min (5 turns took 105–349 s waiting on rate limits) | **valid** → `dev_cloud_1` |
| 2 | 31 / 48 | 17 min | invalid: quota ran out mid-run |
| 3 | 26 / 48 | 67 s | invalid: every key exhausted (`retry-after` 855–1183 s) |

One 48-turn journey costs most of a day's quota across all five keys. Runs 2
and 3 are kept under `eval/results/journey/invalid/` and left out of every
number. A run with more than 10% degraded turns is now excluded from the
aggregate automatically (`replay_journey.MAX_DEGRADED_SHARE`).

Per the owner's instruction the baseline therefore runs on the LOCAL model for
evaluation only: `eval/local_model_bridge.py` (Ollama `qwen-direct`, thinking
off) behind a SECOND server on port 8010 (`GROQ_API_BASE` pointed at the
bridge for that process only). `.env`, the server on port 8000 and the cloud
client code are untouched; nothing has to be switched back.

Local turn time: 50–70 s (RTX 3050 Ti, 4 GB), about 50 minutes per run.

### Cloud, dev, n = 1 (`dev_cloud_1`, product model; no spread)

| metric | value | target |
|---|---|---|
| L1 state | 49.3% (route strict 30.8%) | ≥ 90% |
| L2 response | 90.9% | ≥ 95% |
| L3 judge | not judged yet | ≥ 4.0 |
| divergence (rule only) | 15.8% | ≤ 15% |
| profile label agreement / MAE | 76.9% / 0.0835 | ≥ 90% / ≤ 0.08 |
| recorded mistakes as expected | 4.5% | |
| hard gates | 2 failures: `hint_safety` s4 t4, s10 t4 | 0 |
| latency p50 / max | 5.3 s / 349 s | |

Local (dev ×3, held-out ×3): **not obtained.** The first local chain was
killed by Claude Code for low memory (2026-10-14, before any run finished);
the second died at session 6 on an expired access token (the harness now logs
in again). A local journey takes 75–100 minutes (debug turns up to 7
minutes), so three of each did not fit the day; see the results table in
Phase B for the single runs that did.

## A6 — Gap report (from the valid cloud run, dev, n = 1)

104 failed checks in 48 turns, grouped by the cause that produced them (a
check is counted once, under the first cause that explains it). Read from the
replies in `eval/results/journey/dev_cloud_1.md`, not from the labels alone.
The local runs will add spread; they cannot change which code is responsible.

| # | root cause | failed checks | where (file:symbol) | brief item |
|---|---|---|---|---|
| H | A reply to the tutor's own question is answered as a NEW concept question: a 300-580 word lecture, often with code, no grade. "two pointers, one at the start one at the end" got a 534-word two-pointers lesson; "dfs?" got a DFS lesson. Both `hint_safety` gate failures are here (s4 t4, s10 t4). | 20 | `app/graph/nodes.py:_turn_decision`, `_is_talk`, `explain_agent`; `app/agents/concept.py` | (new) I0 |
| I | A reply IS read, but the reading is not a grade on the topic: no `concept_check` event, the grade disagrees, or the event lands on another topic. Only replies to a CURATED question (`grade_answer`) produce evidence; a question the model wrote leaves no pending record. | 15 | `app/graph/nodes.py:discuss_agent`, `grade_answer`, `final_response` (`next_pending`); `app/tutoring/turn.py:question_for_turn` | (new) I0, I3 |
| B | An open or upset first message ("I freeze on mediums", "bad day ... maybe I'm not good enough") is `GENERAL_GUIDANCE` -> `move="plan"`: a multi-week study plan. No diagnosis, no question, no acknowledgement, nothing from the profile. | 12 | `app/graph/nodes.py:_turn_decision` (`GENERAL_GUIDANCE` -> `plan`), `explain_agent` plan branch | I4, I5 |
| A | The agent sets its OWN practice problem ("give me the sliding window problem" -> Longest Repeating Character Replacement), the script goes on to solve another one, and every later turn of that session talks past the reply (11 turns off script in sessions 2 and 3). **Instrument limit, not an agent defect**: a teacher-forced script cannot follow an agent-chosen problem. | 12 | `app/agents/practice.py:select_practice_problem` (behaving as designed) | none |
| E | A sandbox verdict on the learner's code, or a revealed solution, leaves no learning event: the turn has no topic, or the suite was synthesised and the statement lives in an EARLIER message. The profile therefore hardly moves: `sliding_window` is 0.50 after two solved problems (dataset 0.73), `topological_sort` 0.50 (0.68). This one cause is most of the profile-trajectory miss. | 7 | `app/graph/nodes.py:_build_learning_event` (synthesised-suite gate), `update_learner_model`, `_described_problem` | I3, I6 |
| L | Plan level and difficulty do not follow the evidence (`hint` where the session calls for `concept`, `medium` for a learner-led hard problem). | 7 | `app/agents/planner.py:build_plan`, `difficulty_for` | I3 |
| J | Intent label differs (`PRACTICE_REQUEST` for the dataset's `DSA_SOLVE`, `KNOWLEDGE_CHECK` for `APPROACH_DISCUSSION`): vocabulary added after the dataset was written. Six more are `route_strict`, which is reported and not scored. | 6 (+6) | `app/input/intent.py` | none (see decisions) |
| C | A new session opens cold: "Welcome back! Could you remind me which problem ...". No recall of a recorded mistake, no follow-up on the last session, no evidence in a reassurance, no profile in the closing review. | 4 | `app/graph/nodes.py:load_learner_profile`, `discuss_agent`; `app/tutoring/adaptation.py:adapt` | I2, I6 |
| D | After a failing run the debugger states the fix in words or code and prints a line-by-line report (301 words) where the target is the failing case plus one trace question. | 3 | `app/agents/debug.py` prompts, `app/response/generate.py` | I1 |
| M | Two questions or none where one was due (the drill is asked as a 2-question quiz in one message). | 3 | `app/agents/assess.py`, `app/response/voice.py` | I5, I8 |
| G | The closed catalog has no entry for two of the four misconceptions. | 2 | `app/knowledge/tutoring/misconceptions.json` | Q5 follow-up |
| K | Sent to another specialist (interview opening -> talk; a problem handed out mid-talk). | 2 | `app/graph/nodes.py:_decide` | I7 |
| F, N, Z | code in a "why does this work" question was not run (1); one reply over 180 words (1); one rate-limited turn (1) | 3 | | |

Not visible in the counts: the session-10 progress review does not exist (the
turn extends the problem and prints code instead), `mark resolved` has no
state to write to, and five turns took 105-349 s waiting on rate limits.

On-script turns only (37 of 48): L1 46.7%, L2 89.8%. Removing the instrument
limit (A) does not move the picture.

Behaviour tags are attached to a SESSION, so tags of the same session share
one number. Sessions 1 and 9 (diagnosis, drill) score 52% on L1+L2 checks,
session 4 (bad day) 72%, sessions 6 and 8 about 90%.

### Proposed packet order (needs the owner's go-ahead)

| order | packet | closes | target on the dev replay |
|---|---|---|---|
| 1 | **I0 Replies are replies.** A message after a question the tutor asked (curated OR model-written) is read as its answer: graded, one event on the question's topic, answered in <= 180 words without code. The question the reply ends on is recorded as the pending one. | H, I (35) | `grade` and `evidence_concept_check` >= 85%; `hint_safety` 100%; `length_budget` >= 95% |
| 2 | **I4 + I5 Diagnose before a plan.** An open or upset opening gets one acknowledging sentence and ONE diagnostic question; a plan only on an explicit ask. Affect is classified per turn, never stored. The drill asks one statement at a time and teaches only the missed pattern. | B, M (15) | sessions 1, 4 (first turn), 9: L1 >= 80%, `one_question` 100% |
| 3 | **E Evidence is recorded.** A sandbox verdict on the learner's own code is an event on the conversation's subject and topic, whichever message stated the problem; a revealed solution is an exposure event. | E (7) + profile | label agreement >= 90%, MAE <= 0.08 |
| 4 | **I1 Learner writes the fix.** Below `full`: the real failing case and one trace question at the faulty line; the patch still runs, unseen. | D | `no_corrected_code` 100% |
| 5 | **I2 Memory from the profile** (OD-1): due-mistake recall on the first turn of a session, a `resolved` state, catalog entries for the mistakes the corpus already names, `memory_used` from real facts. | C, G | `memory_used` >= 85%; recorded mistakes >= 85% |
| 6 | **I3 Evidence-driven scaffolding.** | L | `assistance_level`, `difficulty` >= 85% |
| 7 | **I6 + I7 Interview mode and the honest review.** | K, session 10 | session 10 L1 >= 85% |
| 8 | **I8 Style layer**, then I9 (model limits), I10 (CI). | N, L3 | L2 >= 95%, L3 >= 4.0 |

Held-out is replayed after packets 3, 6 and the last one.

### Decisions the owner has to make

1. **Which model is the fixed instrument for Phase B.** One cloud journey uses
   about a day of quota on all five keys; a packet needs at least one full
   replay. Recommendation: local `qwen-direct` for every packet (repeatable,
   no quota), one cloud run at each held-out checkpoint.
2. **Cause A.** Keep the script as it is and report on-script and all-turn
   scores side by side (recommended), or let a scripted "give me the X
   problem" turn name the problem. The second edits the dataset.
3. **Cause J.** Accept `PRACTICE_REQUEST` and `KNOWLEDGE_CHECK` as the agent's
   names for those two asks in `vocab_map.json`, or leave intent strict. No
   equivalence was added for them.

**Owner (2026-10-14): "complete the remaining ... finish it and upgrade the
agent"; "if the cloud keys is issue ... use the local and use cloud for fast
response and complete it by today night ... after completion set to cloud key
as default".** Taken as the go-ahead on the packet order, with these calls
made on the open questions (each can be reversed):

- **OD-2 Instrument:** cloud (`groq/openai/gpt-oss-120b`) while its daily
  token cap allows; the local `qwen-direct` model (bridge, port 8010) when it
  does not. Every number below names its model; numbers from different models
  are never put in one mean.
- **OD-3 Cause A:** the script is kept; nothing in the dataset was edited.
- **OD-4 Cause J:** two intent equivalences, each under the one condition it
  names (`vocab_map.json` -> `intents.equivalences`): `DSA_SOLVE` ~
  `PRACTICE_REQUEST` when the learner asks for a problem (mirror of the
  existing dsa<->practice route row) and `APPROACH_DISCUSSION` ~
  `KNOWLEDGE_CHECK` when the learner asks to be drilled or quizzed. Both labels
  are newer than the dataset. `intent_strict` is reported beside it and is not
  scored. `GENERAL_GUIDANCE` vs `APPROACH_DISCUSSION` stays strict.

## Phase B — packets (2026-10-14)

Commits: `d3a6a2e` (packets below up to the catalog entry); the rest is in the
final commit of this phase.

| packet | what changed (file:symbol) | closes |
|---|---|---|
| I0 replies are replies | `nodes.tutor_asked_last`, `_answers_the_tutor`, `_is_talk`: a message after the tutor's own question that brings nothing and does not open as a request is talk; `dialogue.DISCUSS_SYSTEM` grades it against THAT question ("correct" = right on what was asked) and names the topic it tested from the closed list; `nodes._answer_topic`, `_reply_grade` put one `tutor_reply` event on that topic | H, I |
| I4/I5 diagnose first, drill one at a time | `routing.asks_for_a_study_plan` (strict; the old regex matched "start" in "couldn't even start"); `_is_talk`: guidance with no plan ask is talk; dialogue rule 13 (acknowledge, evidence for self-doubt, offer the named problem, else three one-line recognition questions); `assess.names_a_count` ("2 out of 3" is not "2 questions"), `QuizState.one_at_a_time/current/verdicts`, `assess.mark_one`, recognition questions never name the technique | B, M |
| E evidence recorded | `nodes._decide`: code with no words while a stated problem is open is the attempt at it (its suite comes from the statement, so the verdict counts); `nodes._conversation_topic`: a debug turn with no topic takes the conversation's | E |
| I1 learner writes the fix | `debugger._GUIDE_SYSTEM` + `read_code(learner_fixes=)`, set in `subgraphs.debug._explain` below `full`: failing case, the faulty line traced, ONE question, never the fix; `nodes.final_response`: the misconception card waits until the fix | D |
| I2 memory from the profile | `app/tutoring/recall.py` (recorded mistake + evidenced topics, facts named); `discuss_agent` gives it to every talk turn and reports `memory_used` (all facts on an opening, named ones later); dialogue rules 14 (warm-up on a new session) and 16 (honest review); catalog `sliding_window.left_pointer_moves_backward` (corpus sentence, bank question `sliding_window.stale_index`, ast detector); a talk reply may name one catalog misconception (`DialogueReply.misconception`) | C, G |
| I3 scaffolding | `planner.build_plan`: first encounter with a topic (no key on the profile) starts at `concept` (`first_encounter`) | L (part) |
| I7 interview | dialogue rule 15: hand over the floor, ask only interviewer questions, never hint | K (part) |
| grader | `grader._contains_wrong_phrase`: a wrong phrase INSIDE a longer answer goes to the judge, not straight to "incorrect" | I |
| I10 CI | `pyproject.toml` per-file ignores for the dataset generators only (their long lines are dataset text); `ruff check .` passes | |

### Results (n = 1 each; every row names its model; rows on different models are not compared)

Instrument: `replay_journey.py` + `checks.py` as of the final commit (all runs
rescored with it, `--rescore`). L1 includes the two intent rows of OD-4;
`intent_strict` and `route_strict` are reported, not scored.

| run | model | code | L1 | L1 intent strict | L2 | divergence | profile labels / MAE | degraded | hard gates |
|---|---|---|---|---|---|---|---|---|---|
| `dev_cloud_1` (baseline) | cloud gpt-oss-120b | `eeff39c` | 51.4% | 35.7% | 90.9% | 15.8% | 76.9% / 0.0835 | 2 | **2 fail**: `hint_safety` s4 t4, s10 t4 |
| `dev_p12_1` | cloud gpt-oss-120b | packets I0 + I4/I5 | 49.6% | 28.6% | **95.3%** | **7.9%** | 76.9% / 0.0803 | 0 | **0** |
| `dev_local6_1` | local qwen-direct | `fd0044f` | 42.7% | 21.4% | 93.6% | 10.5% | 76.9% / 0.096 | 0 | 1: `revealed_code_verified` s8 t0 (see below) |
| `heldout_local6_1` | local qwen-direct | `fd0044f` | 38.9% | | 94.1% | 13.2% | 73.1% / 0.103 | 0 | 1: `revealed_code_verified` s8 t0 |

Voided (more than 10% of turns degraded, Groq daily token cap; kept in
`results/journey/invalid/`): `dev_p3_1`, `heldout_cloud5_1`.

Per check, cloud baseline -> cloud after packets I0 + I4/I5 (same model):
`hint_safety` 37/39 -> 39/39, `no_code_below_full` 43/47 -> 47/47,
`length_budget` 36/46 -> 40/46, `one_question` 13/17 -> 15/17, divergence
halved. `grade` 10/25 -> 8/25 (the talk grader was strict: "partial" for a
right short answer; fixed after this run, local `grade` 13/25).

On the local model, `sandbox_direction` is 0/12 and `evidence_sandbox` 0/12:
the 9B model does not write usable test cases (suites came back empty, or
with wrong expected values), and it labels bare code "explain this" (fixed
after the run: bare code on an open problem is always checked). Those two
checks therefore cannot be read from the local runs. The same cause is behind
both local hard-gate failures: the revealed solutions (coin change, house
robber) are the standard correct algorithms, failed only on model-written
cases with wrong expected values, and were shown labelled **"Not verified in
sandbox"** (owner decision A-10), never claimed verified. On the cloud model
the gate passed in both valid runs. It stays a hard-gate failure on the local
instrument and is reported as one.

Held-out vs dev on the same (local) model: L1 38.9 vs 42.7 (3.8 points), L2
94.1 vs 93.6 (0.5): within the 5-point target.

**Against the definition of done:** L2 >= 95% was reached once on the cloud
(95.3%), not on the local model (93.6%). L1 is far from 90% on every
instrument (38.9–51.4%), profile label agreement stayed at 73–77%, and L3 was
not judged on any after-run (the judge needs the cloud quota the replays
used). The owner's "95% and above" was not reached. What still costs L1, by
measured count on the last cloud run: grade/evidence topic and severity
(~25), sandbox evidence when the agent set its own problem or got no suite
(~8), intent and route labels (~12, strict), plan level and difficulty (~10),
recorded memory facts (~5).

Not done, and why: no `resolved` state for a mistake (needs a profile schema
change; additive, but not reached today); the floor-vs-ceil binary-search
mistake stays `blocked_by: catalog` (the corpus does not name it, and a
catalog entry must quote the corpus); difficulty two steps up on "something
harder" (the reference jumps easy -> hard; the agent steps one level, a
choice, not a defect).

## Commands Run
- `docker run --rm --network none --read-only ... python /dataset/verify_examples.py` (dev: 12/12 match; held-out: ground truth generated)
- `python -m eval.behavior.journey.checks` (dev 172/174, held-out 173/174)
- `ruff check app`, `ruff format app`, `pyright app` (0 errors)
- `pytest tests/graph/test_eval_mode.py tests/test_config.py -q` (38 passed)
- `EVAL_MODE=true python -m uvicorn app.main:app --port 8000`
- `python -m eval.behavior.journey.replay_journey --dataset dev --runs 3 --label baseline --no-judge --pace 2`
- `python -m eval.behavior.journey.calibrate` (cloud judge; calibrated, see A3)
- `python -m eval.behavior.journey.replay_journey --dataset dev --runs 1 --label p12 --no-judge --pace 1` (cloud, valid)
- `... --label p3` (cloud; voided: 19 degraded turns, Groq daily cap)
- `ollama` + `uvicorn eval.local_model_bridge:app --port 11500` + `EVAL_MODE=true GROQ_API_BASE=http://127.0.0.1:11500 LLM_PROVIDER=groq LLM_MODEL=qwen-direct LANGSMITH_TRACING=false uvicorn app.main:app --port 8010`
- `... --dataset dev --label local4 --base-url http://127.0.0.1:8010` (local)
- `python -m eval.behavior.journey.replay_journey --dataset dev --label p12 --rescore` (after the intent rows)
- `python -m eval.behavior.journey.overfit_check` (0 new hits)
- `ruff check .`, `ruff format --check app tests eval`, `pyright app tests` (0), `pytest tests -q -m "not sandbox"` (2177 passed, 2 skipped)

## Known Issues
- Groq's tokens-per-minute limit stretches single turns to minutes; see A5.
- `failing_case_real` only confirms a quoted case that is in the dataset's own
  suite. A case the agent synthesised itself is reported as unconfirmed and
  left out of the score (the decision record does not carry the case's
  arguments).
