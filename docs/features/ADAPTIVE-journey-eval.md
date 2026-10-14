# FEATURE — Journey evaluation and agent upgrade (10-session learner journey)

## Status
Phase A (EVALUATE) in progress: A0–A4 done, A5 baseline running, A6 gap report
pending. Phase B (IMPROVE) has not started and waits for the owner's go-ahead
on the packet order.

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

Result: _pending (run after the replays; see A5)._

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

Local (dev ×3, held-out ×3): _running._

## A6 — Gap report

_pending._

## Commands Run
- `docker run --rm --network none --read-only ... python /dataset/verify_examples.py` (dev: 12/12 match; held-out: ground truth generated)
- `python -m eval.behavior.journey.checks` (dev 172/174, held-out 173/174)
- `ruff check app`, `ruff format app`, `pyright app` (0 errors)
- `pytest tests/graph/test_eval_mode.py tests/test_config.py -q` (38 passed)
- `EVAL_MODE=true python -m uvicorn app.main:app --port 8000`
- `python -m eval.behavior.journey.replay_journey --dataset dev --runs 3 --label baseline --no-judge --pace 2`

## Known Issues
- Groq's tokens-per-minute limit stretches single turns to minutes; see A5.
- `failing_case_real` only confirms a quoted case that is in the dataset's own
  suite. A case the agent synthesised itself is reported as unconfirmed and
  left out of the score (the decision record does not carry the case's
  arguments).
