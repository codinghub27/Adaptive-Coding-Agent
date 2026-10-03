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
- (more recorded per packet below)

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
