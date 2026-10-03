# FEATURE — Adaptive tutoring behaviours (G1..G5)

## Status
In progress. Single session, single agent, gated packets Q0..Q7. This file is
the source of truth between packets: each packet states its numeric target
BEFORE work starts and records the measured result after.

## Goal
Close the gap between the agent and the five reference conversations in
`eval/behavior/adaptive_examples.jsonl`:

| | feature |
|---|---|
| G1 | answer grading — a reply to the agent's own guiding question is graded (correct / partial / incorrect / dont_know) and the grade decides the next move |
| G2 | conceptual evidence — graded answers become learner-model evidence, weighted below sandbox evidence |
| G3 | misconception tracking — named misconceptions from a CLOSED catalog, taught, remembered |
| G4 | code-submission loop — "send me your implementation" creates a pending expectation; the next code is reviewed against the active problem and run in the sandbox |
| G5 | in-session progression — success raises the next problem's difficulty, struggle adds scaffolding |

## Prerequisite check (Section 1) — 2026-10-03

| prerequisite | doc status | commit | verdict |
|---|---|---|---|
| P1 conversation continuity | Done, measured (knows_problem 12/12, same_topic 6/6) | `5c48d92` | **done, with one gap (below)** |
| P4 escalation per teaching mode | Done, measured | `2c0757a` | done |

**Gap found in P1 (live UI, owner's session):** "lets start with easy problem"
got a MEDIUM `bit_manipulation` problem, and the next turn "hint?" produced a
`trees` hint. Root causes (file:symbol):
- `app/graph/nodes.py:practice_agent` hands out a corpus problem but never makes
  it the conversation's active problem; `active_problem_update` only stores a
  statement the LEARNER sent. The follow-up therefore inherited the previous
  (trees) problem.
- `practice_agent` takes difficulty only from `plan.difficulty`
  (`app/agents/planner.py:difficulty_for` on the skill), ignoring an explicit
  "easy".
- `app/agents/practice.py:_cue` returns the PATTERN's first recognition signal
  but `render_practice_problem` presents it as being about THIS problem.

Examples 3 and 5 depend on an agent-chosen problem staying active, so the fix
is part of Q1/Q6 (practice problems become the active problem; explicit
difficulty words are honoured), not deferred to the end.

## Map (file:symbol)

| concern | where |
|---|---|
| graph wiring | `app/graph/build.py:build_graph` (understand_input → classify_intent → load_learner_profile → retrieve_knowledge → plan_teaching → route → agent → execute_code → verify → final_response → update_learner_model) |
| routing | `app/graph/routing.py:INTENT_ROUTES`, `ROUTE_NODES`, `select_route`, `route_after`; `RouteKey` in `app/graph/state.py` |
| state | `app/graph/state.py:AgentState` / `AgentStateUpdate` |
| active problem | `app/graph/nodes.py:resolve_problem_relation`, `_problem_update`, `active_problem_update`, `_persist_active_problem`; `app/memory/conversation.py:get_active_problem`, `set_active_problem`; columns on `app/db/models/conversation.py:Conversation` |
| hint ladder state | `app/db/models/hint_progress.py:HintProgress`, `app/agents/hint_engine.py:base_ladder_ceiling`, `app/graph/nodes.py:resolve_hint_progress`, `_hint_topic_key`, `_record_verified_attempt` |
| planner | `app/graph/nodes.py:plan_teaching` → `app/agents/planner.py:analyze_problem`, `build_plan`, `difficulty_for` (WEAK 0.42 / HARD 0.68), `clamp_assistance` |
| practice | `app/graph/nodes.py:practice_agent`, `app/agents/practice.py:select_practice_problem`, `_nearest`, `_cue`, `render_practice_problem` |
| debug / sandbox | `app/graph/nodes.py:debug_agent`, `_resolve_test_suite` (`extract_test_suite` → `synthesize_test_suite`), `app/graph/subgraphs/debug.py` (`initial_verdict` = learner code, `final_verdict` = patched code) |
| learner model | `app/memory/profile.py:apply_event` (`ALPHA` 0.3, `PRIOR` 0.5), `common_errors_list`; `app/memory/events.py` (record + rebuild from the event log); `common_errors` is a `{tag: count}` map on `app/db/models/profile.py` |
| events | `app/schemas/event.py:LearningEventCreate` (`evidence_source`, `errors`, tri-state `solved`); `app/graph/nodes.py:update_learner_model` |
| response | `app/response/generate.py:generate_response`, `app/response/format.py:SECTION_TITLES`, `render_verdict`; `app/schemas/response.py:ResponseSectionKind` |
| API | `app/graph/api.py:ChatResponse`, `/chat`, `/chat/stream` |
| misconception source | `## Common Mistakes` of `app/knowledge/corpus/*.md` |

## Scope
- Q0..Q7 of the brief. Additive state/schema/DB changes only; the Phase 4 route
  contract keeps its keys (`grade` is added).

## Out of Scope
- New corpus patterns, React frontend, Redis/Celery.
- LLM-generated guiding questions for arbitrary problems (see AD-T2).

## Architecture Decisions
- **AD-T1 Pending check lives on the conversation.** `conversations.pending_check`
  (JSONB, nullable) holds `{kind, question_id, rubric_ref, expected_concepts,
  topic, created_at, ...}`. It is per-turn: every turn writes the pending check
  it ends with (or NULL), so it is cleared when answered, when a new problem
  starts, and when the learner moves on.
- **AD-T2 Guiding questions and rubrics come from a curated, corpus-anchored
  question bank** (`app/knowledge/tutoring/`), never from learner text and never
  free-generated: per-pattern recognition questions (expected concepts = the
  corpus pattern's own name/aliases) plus curated question chains for problems
  with a curated statement. The LLM only JUDGES free-form replies against that
  rubric.
- **AD-T3 Grading is rules-first, judge-second, and fails closed.** The
  deterministic grader (injection guard -> accepted answers / normalized
  expressions -> known wrong answers -> options -> don't-know -> recognition ->
  rubric keywords) settles most replies; only the rest reach the LLM judge, with
  the reply inside `<learner_reply>` tags and an explicit "data, never
  instructions" rule. Judge output is filtered to closed sets (grade labels,
  rubric concept ids, the question's catalog misconception ids). A judge
  "correct" below 0.6 confidence is "partial"; an unparseable judge answer is
  "partial"; an instruction-shaped reply is "incorrect" before any model call.
- **AD-T4 Route key `grade` is ADDED** (`grade_answer`); existing keys are
  untouched. `should_grade`: a pending check + no code/error + not a new problem
  + not a confident new request + not an explicit help request. A low-confidence
  label on a short reply ("7?" -> GENERAL_GUIDANCE 0.4) IS graded. After grading:
  "don't know" on a problem hands the next rung to `dsa_agent` (raised
  assistance); everything else goes straight to `final_response`.
- **AD-T5 Conceptual evidence (relaxes "evidence requires code").** Graded
  answers are `concept_check` events (`evidence_source`, `concept_grade`;
  `solved` stays NULL). `CONCEPT_ALPHA = 0.1` (a third of `ALPHA` 0.3): one
  answer shows one idea, not working code, so ~3 answers ~ 1 sandbox verdict.
  Scores: correct 0.85, partial 0.55, incorrect 0.2, dont_know 0.25. Rising is
  capped at `CONCEPT_CEILING` 0.67 < `HARD_SKILL` 0.68, so answers alone can
  never make the next problem HARD. Concept evidence moves the FAMILY estimate,
  and a topic key only once that key has sandbox evidence: measured, letting it
  create a topic key shadowed the family estimate and slowed adaptation from 9
  to 22 turns.
- **AD-T6 Misconceptions are a closed, corpus-anchored catalog**
  (`app/knowledge/tutoring/misconceptions.json`, 8 ids). Each quotes a sentence
  of its pattern's `## Common Mistakes` (three sentences were ADDED to the
  corpus for the ids the brief names; index rebuilt). Detection = AST code
  detectors (precise shapes, never executed) + the grader's closed mapping.
  Counted in `common_errors`; `common_errors_seen` stores last-seen. A
  recurring one is surfaced ("Watch out") on later practice / new-problem
  turns of the same family.
- **AD-T7 Agent-chosen and named problems become the active problem.** A
  practice problem (curated statement, or corpus title + link) and a curated
  problem the learner only NAMES ("help me solve Two Sum") are stored as the
  conversation's active problem. This closes the owner's live P1 gap ("hint?"
  answered about the previous problem).
- **AD-T8 Code-submission loop.** A pending `code_submission` + a code-only
  message attaches the active statement (P1 deliberately never did this for
  unsolicited code; here the agent asked for it), routes to `debug`, and the
  suite is extracted from the curated statement's worked examples. "Execution"
  lines: "(your code)" = `initial_verdict`; "(suggested fix)" = the outer
  verification of patched code; "not executed" when nothing ran.
- **AD-T9 Progression.** Practice difficulty: explicit ask > in-session grades
  since the last problem (success -> +1 level; struggle -> stay) > skill.
  "harder" after struggle is refused (stays). A "don't know" sets a per-problem
  assistance floor (one step up, capped per mode: guidance `partial`, balanced
  `pseudocode`, challenge `concept`; never `full`, which stays P4's) that later
  turns on the problem keep.

## Numeric targets (stated before work)
| packet | target |
|---|---|
| Q0 | instrument exists; baseline recorded (expected low) |
| Q1 | 100% of replies after an agent question route to `grade_answer`; 100% of new-problem messages clear pending; `alembic check` clean |
| Q2 | grader accuracy ≥90% on the labelled set; 0 injection attempts graded `correct`; no learner text in traces |
| Q3 | correct → advance, incorrect → reframe at same rung, dont_know → assistance +1 on 100% of replay turns testing it |
| Q4 | `concept_check` events with provenance; CONCEPT_ALPHA rule; never past HARD without sandbox evidence; adaptation speed before/after |
| Q5 | detection ≥85% on catalog fixtures; 0 free-text ids; recurring misconception surfaced later (Ex4, Ex5) |
| Q6 | Ex3: Tarjan submission reviewed vs the active problem, parent-edge flagged, sandbox-verified; Ex5: difficulty up after success, assistance up after struggle |
| Q7 | replay + labelled set + eval.run + transcript probes gated in CI |

## Packet log

### Q0 — Instrument first (no feature code)

**Target (stated first):** the instrument exists and runs on the live API; a
baseline is recorded. Expected to be low.

**What was built**
- `eval/behavior/adaptive_examples.jsonl`: the three `[code]` placeholders are
  real fixtures — correct Two Sum (Ex1, an assistant turn, reference only),
  Tarjan bridges skipping the parent NODE (Ex3 #8), and a BFS for Shortest Path
  in Binary Matrix that marks visited on DEQUEUE (Ex5 #10). The KeyError Two Sum
  (Ex2) and the wrong `maxPathSum` (Ex4) were already real code. All learner
  fixtures are top-level functions so `extract_test_suite` can call them.
- `eval/behavior/replay.py`: replays every LEARNER turn of the five examples on
  a fresh account through `/chat/stream` (no `topic` field; mode per learner
  preference: Ex1 guidance, Ex3 challenge, others balanced). 3 hard-gate checks
  on every turn (`no_error`, `hint_safety`, `execution_lines_match`) + the
  frozen per-turn table `CHECKS` derived from each example's `agent_behavior`
  (no code on turn 1, exactly one check question when awaiting an answer, grade
  label, reaction advance/narrow/reframe, assistance +1, misconception id,
  practice difficulty, difficulty rose, recurring misconception surfaced,
  submission reviewed in the sandbox) + `/profile` `common_errors` checks.
  "Execution" lines: a "(your code)" line must render the learner's own
  verdict (`tutoring.execution.learner`), every other line the turn's
  authoritative `verification`; "not executed" when nothing ran.
- `eval/behavior/answer_set.jsonl`: **50** labelled replies (11 from the
  examples, 34 variations incl. 6 replies to questions on other patterns, 5
  injection attempts; gold: correct / partial / incorrect / dont_know, plus the
  gold misconception id where one applies).
- `eval/behavior/misconception_fixtures.jsonl`: 21 fixtures — 14 code (10
  positives over 6 ids, 4 correct-code negatives) and 7 answers (5 positives,
  2 negatives).
- `eval/behavior/offline.py`: grader accuracy on the answer set and detection
  rate on the fixtures, with the REAL configured LLM.

**Baseline (HEAD `45e0152`, live API, `eval/results/tutoring_Q0_baseline.json`)**

| instrument | baseline |
|---|---|
| replay checks (all) | **73/129 = 56.6%** — inflated by the 63 per-turn hard gates, which all pass |
| replay feature checks (excl. gates) | **10/66 = 15.2%** (the 10 are route/no-code checks) |
| examples passing end to end | **0/5** |
| grader accuracy | **0/50** (no grader) |
| misconception detection | **0/15** (no detector), 0 free-text ids |

Observed in the baseline: "7?", "I don't know how.", "DFS?", "BFS because…" and
the Ex5 code submission all route to `clarify`; "Maybe a low-link value?" and
"low[v] > disc[u]" route to `explain`; the Ex3 Tarjan submission is debugged
with no statement (synthesised suite, `fail` 3/5); Ex5 #0 "confused about BFS
vs DFS" routes to `clarify`; the Ex3 practice request returns a problem but no
question and does not make it the active problem.

### Q1–Q6 — implemented together (one commit), measured per packet on the same instrument

**Deviation from the brief, stated plainly:** the pending state, grader,
reactions, conceptual evidence, misconceptions and the code-submission /
progression loop share the same state fields, node and response layer, so they
were built in one pass and committed together. Each packet's acceptance
criterion was still measured separately, on the unchanged Q0 instrument (plus
two new probes for Q1/Q2 that did not exist at Q0 and therefore have no
baseline). `/compact` and the `code-review` plugin were not run inside this
session (the plugin fans out to sub-agents, which the brief forbids); the diff
was self-reviewed instead.

**New files:** `app/schemas/tutoring.py`, `app/tutoring/{bank,grader,misconceptions,progression,turn}.py`,
`app/knowledge/tutoring/{checks,misconceptions}.json`, migration
`b5c6d7e8f9a0_tutoring_loop_state.py`, `eval/behavior/{pending_probe,trace_probe}.py`,
`tests/tutoring/*`, `tests/memory/test_activity.py`.

| packet | target | measured | file |
|---|---|---|---|
| Q1 | replies after an agent question -> `grade_answer` 100% | **11/11** (one more turn after a question was a new-problem request; correctly routed to `practice`, not graded) | `tutoring_Q1.json` + `pending_probe` |
| Q1 | new requests clear the pending check 100% | **3/3** (new problem, practice request, hint request) | `pending_probe` |
| Q1 | `alembic check` | clean | |
| Q2 | grader accuracy >= 90% | **49/50 = 98%** (both runs; the one miss differs: an LLM judgement once, a rate-limited judge falling back to `partial` once) | `offline` |
| Q2 | 0 injections graded correct | **0/5** | `offline` |
| Q2 | no learner text in traces | **not measured** -- the trace probe ran while every LLM credential was rate-limited, so its setup turns went to `clarify` and no grading happened (0 leaks in 108 runs scanned, but `grade_answer` never ran). Must be re-run. | `trace_probe` |
| Q3 | correct->advance, incorrect->reframe, dont_know->assistance +1 on 100% of replay turns testing it | **9/9** (5 `advanced`, 1 `moved_on`, 1 `reframed`, 2 `assistance_up`) | `tutoring_Q1.json` |
| Q4 | `concept_check` events with provenance, CONCEPT_ALPHA rule, never past HARD | unit tests + replay `concept_event` check pass | `tests/tutoring` |
| Q4 | adaptation speed before/after (same `eval.adaptation_speed`) | 1 sandbox verdict / 3 turns, all good: **9 -> 6 turns**; all bad: 3 -> 3; answers alone, 30 turns: never past `medium` | `eval.adaptation_speed` |
| Q5 | detection >= 85% on catalog fixtures | **15/15 = 100%**, 0/6 false positives, 0 free-text ids | `offline` |
| Q5 | recurring misconception surfaced later (Ex4, Ex5) | Ex5 #12 `surfaced_misconception` passes; Ex4/Ex5 profile `common_errors` pass after the topic fix | replay |
| Q6 | Ex3: Tarjan reviewed vs the active problem, parent-edge flagged, sandbox-verified | **pass** (#8 route debug, `submission_reviewed`, verdict `pass` 2/2 extracted cases, `graphs.parent_node_vs_parent_edge`) | `tutoring_Q1.json` |
| Q6 | Ex5: difficulty up after success, assistance up after struggle | **pass** (#12 medium -> hard: Shortest Path in Binary Matrix -> Word Ladder; #14 hint -> concept) | `tutoring_Q1.json` |

**Replay, before -> after (same instrument)**

| | Q0 baseline | best full run (`tutoring_Q1.json`) | last full run (`tutoring_final.json`) |
|---|---|---|---|
| all checks | 73/129 (56.6%) | **127/129 (98.4%)** | 98/129 (76.0%) |
| feature checks | 10/66 | 64/66 | 35/66 |
| examples end to end | 0/5 | 3/5 (the 2 misses were the profile checks, fixed after) | 3/5 (Ex1, Ex2, Ex4: 100%) |
| hard-gate failures | 0 | 0 | 0 |

The last full run is NOT a regression in the code: from its third conversation
on, the LLM provider rate-limited every call (verified directly:
`LLMRateLimitError ... TooManyRequests` on the intent prompt), the keyword
fallback is low-confidence by design, and those turns went to `clarify`. The
same code had passed Ex3 31/31 and Ex5 48/48 earlier. Re-run with the quota
available: `python -m eval.behavior.replay --pace 20`.

**Not run in this session (quota):** `eval.run` and `eval.transcript_probes`
-- so "no regression on routing / topic / groundedness / debug_fix" is NOT yet
demonstrated. Both are wired into the CI `live-eval` job.

**Fixes found by the live replay (each re-verified):**
- "7?" was classified GENERAL_GUIDANCE at 0.4 and bypassed grading; only a
  CONFIDENT new request now bypasses (AD-T4).
- "Two Sum" named in prose never became the active problem, so "I don't know
  how" had nothing to hand off to; named curated problems now do (AD-T7).
- "Give me a hard graph problem" had no planner topic; the request's own corpus
  vocabulary now names it (`named_pattern`, slug beats alias on ties,
  "binary search tree" -> trees).
- Curated examples were `Example: a -> b`; the suite extractor reads
  `Input:/Output:` lines, so they were rewritten (Tarjan submission: inconclusive -> pass 2/2).
- A misconception on a debug turn with no plan topic was never counted; the
  event now takes the catalog pattern as its topic.

### Q7 — Scorecard + CI
`.github/workflows/ci.yml` `live-eval` now also gates: replay `--gate 0.90`
(+ hard gates), `pending_probe`, `offline --gate` (grader >= 90%, 0 injections
correct, detection >= 85%, 0 free-text ids) and `trace_probe`. The `static` job
already runs `tests/tutoring` and formats/lints `eval/`.

### Owner-reported UI issue — learning streak stuck at "1 day"
Cause: the sidebar derived activity days from each conversation's LAST message
only, so a learner working in one conversation every day had a streak of 1.
New `GET /profile/activity` returns every UTC hour with a learner message (last
60 days); the client buckets them into local days. Verified in the browser
(`/profile/activity` requested; tooltip shows active days of the last 7) and by
`tests/memory/test_activity.py` (three days in one conversation -> three days).

### Owner-reported — screenshot not processed, spinner not animating
- **Image:** reproduced on the live API with the owner's LeetCode screenshot.
  The vision model DID read it (problem extracted); the turn then went to
  "could you confirm?" because the chat-model intent call was rate-limited and
  the keyword fallback scored 0.2. Fix: a deterministic rule -- a problem
  statement plus a plain "how do I solve this" ask is `DSA_SOLVE` (0.75); more
  specific asks still go to the classifier. The vision prompt now keeps the
  worked examples (Input/Output lines) inside `problem`, and when extraction
  itself fails the reply says the image could not be read instead of the
  generic clarify. Live: same screenshot + "How to solve this problem" -> `dsa`,
  intent `rule` 0.75, sections understanding / hint / "Your turn".
  Known limitation: Longest Palindromic Substring is in no corpus file, so its
  topic is inferred by retrieval (it picked `sliding_window` / `two_pointers`).
- **Spinner:** `base.css` stops every animation under `prefers-reduced-motion`
  (Windows "Show animations" off), freezing the loading spinners. They are now
  exempt (1.2 s rotation, infinite). Verified in the browser with reduced
  motion emulated: working-panel spinner and send-button spinner both
  `spin 1.2s infinite`.

### Owner's behaviour specification (2026-10-03) — alignment pass
The owner supplied a behaviour spec (core-behaviour table, hard rules, an
anti-example and five worked conversations). Gaps closed:

| spec rule | change |
|---|---|
| "Keep answers short; teach the missing piece, not the topic"; anti-example (1000-word BFS answer) | A curated chain's first question replaces the long answer with its one-line opener (`lead`, "Let's start"); any turn that asks or reacts drops survey sections (recognition, intuition, understanding, constraints, key insight, complexity unless code is shown, common mistakes, next steps). New chain `bfs_basics` catches "I don't understand BFS". |
| "One question at a time" | unchanged: exactly one `check_question` (replay-checked) |
| "End with the reusable lesson" | `lesson` on the closing question of each chain and on each catalog misconception; shown as "Takeaway" when a chain closes, or -- when it closes with a code request -- with the reviewed / verified code |
| Example 1: "I don't know how" -> build it together -> verified solution -> Execution ✓ | Guidance mode only: "don't know" on a code request (after the concept was graded correct) raises assistance to `full`, which reaches the existing P4 path that reveals ONLY a sandbox-verified reference. Balanced / Challenge stay +1 step. |
| "Every Execution: ✓ line must come from a real execution result" | Execution lines now carry ✓ / ✗ and are still rendered only from the verdict; replay wording updated (check logic unchanged) |
| Example 5: "Based on this session, you're comfortable with…" | practice turns that step difficulty up say so from the session's graded counts and the closed topic slugs; a struggle says it stays and adds guidance |

**Instrument changes (recorded so numbers stay honest):** Ex1 #6 `assistance_up`
now requires "rose" (was "exactly +1") and two checks were added there
(`verified_solution`, `execution_line` required), because the owner's spec
says that turn reveals the verified solution. Execution wording gained ✓/✗.

### Local models (qwen3.5:9b + qwen2.5-coder:7b) — verification, 2026-10-03
Branch `experiment/local-ollama` only. Same instruments, live API, no rate
limits. Latency, live checks and the full cloud comparison are in
`LLM-ollama-local.md`.

| | Q0 baseline | cloud best (`tutoring_Q1.json`) | local, first run | local, final (`tutoring_local.json`) |
|---|---|---|---|---|
| all checks | 73/129 (56.6%) | 127/129 (98.4%) | 121/130 (93.1%) | **130/130 (100%)** |
| examples end to end | 0/5 | 3/5 | 4/5 | **5/5** |
| hard-gate failures | 0 | 0 | 0 | **0** |
| replies -> `grade_answer` | -- | 11/11 | -- | **11/11** |
| new requests clear pending | -- | 3/3 | -- | **3/3** |
| grader accuracy | 0/50 | 49/50 | -- | **49/50 (98%)** |
| injections graded correct | -- | 0/5 | -- | **0/5** |
| misconception detection | 0/15 | 15/15 | -- | **15/15**, 0/6 false positives, 0 free-text ids |
| no learner text in traces | -- | not measured | probe failed (injection reply not graded) | **0 leaks / 103 runs, 3/3 grade traces** (closes the Q2 "must be re-run" item) |
| transcript probes | -- | 341/362 (94.2%, P5) | -- | **285/294 (96.9%)**, gate passed |
| eval.run | -- | 100 / 100 / 100 / 100 / 100 | -- | 100 / 100 / 100 / 100, groundedness **90** |

(The check total is 130, not 129: the owner's spec pass added two checks on
Ex1 #6 and changed one.)

**Changes made for the local models** (re-measured on the full replay):
- `should_grade`: a bare reply (<= 3 words, no request wording) to a pending
  question is graded at ANY classifier confidence. AD-T4 relied on the cloud
  classifier being unsure about "7?" (0.4); qwen3.5:9b says GENERAL_GUIDANCE
  at 0.9. Short requests ("another problem", "Give me a problem") still bypass.
- `should_grade`: an instruction-shaped reply goes to the grader (AD-T3 fails
  it closed) instead of to `explain` on a confident GENERAL_GUIDANCE label.
- `verified_reference` (Example 1's reveal): worked examples from the
  statement outrank the model's own `expected` values; one repair turn with
  sandbox feedback otherwise.
- Debugger prompts: name the exact line, trace the failing case, say what it
  should refer to, speak to the learner.

**Behaviour spec acceptance:** replay >= 90% -> 100%; 5/5 conversations; hard
gates 100%. Not met outside the replayed conversations (see "Known issues" in
`LLM-ollama-local.md`): concept answers end without a question; a problem
missing from the corpus gets retrieval's topic, so its recognition question
can accept the wrong pattern; transcript T1 conflicts with the "short answer"
rule.

### Final session (2026-10-04) — owner decisions applied
- **Explicit section asks override the short-answer rule** (owner decision on
  transcript T1). "Explain the intuition / how to recognize / the complexity"
  keeps exactly those sections (`app.graph.nodes.requested_sections`, fixed
  phrases); a curated chain's one-line opener is not used on such a turn.
  The assistance level still decides whether any code is shown.
- **No pattern is taught from a guess.** The "which technique?" recognition
  question is asked only when the topic is trusted (title, conversation,
  explicit hint, profile match), never on a bare retrieval guess
  (`question_for_turn(topic_trusted=...)`). Longest Palindromic Substring was
  added to the two-pointers notes (expand around centre) and the index rebuilt;
  the owner's screenshot now gets topic `two_pointers`.

Final measurements (experiment branch, local models, same instruments):
replay **130/130, 5/5 conversations**; transcript probe T (balanced)
**86/86** incl. T1 `section_intuition`, `section_recognition`,
`section_complexity`; pytest 1775 passed / 28 skipped; pyright 0; ruff clean.

**Cloud models (main, after porting the fixes, commit `a56da35`):** replay
**128/130, 4/5** in one paced run -- the only misses were Example 1 #6
(`verified_solution`, `execution_line`): that turn finished in 3.6 s with no
reference solution, consistent with a rate-limited reference call. Re-running
Example 1 alone: **26/26**, the verified Two Sum solution revealed with
"Execution ✓ passed 3/3". So every reference conversation passes on cloud and
on local models; the cloud number is sensitive to provider rate limits.
