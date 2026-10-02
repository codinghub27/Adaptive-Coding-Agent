# FEATURE — Adaptive agent upgrade (current behaviour -> planned agent)

## Status
In progress. Single session, single agent, gated packets P0..P7. This log is
the source of truth between packets; each packet records its numeric target
BEFORE work starts and its measured result after.

## Frozen baseline (start of this work, HEAD `472895c`)

| Check | Value |
|---|---|
| `pytest tests -q` | 1574 passed, 2 skipped |
| pyright strict (`app tests`) | 0 errors |
| ruff check / format | clean |
| eval routing | 93.3% |
| eval topic | 93.3% |
| eval hint_safety | 100% |
| eval debug_fix | 100% |
| eval groundedness | 30% |
| 30-probe topic set | 90% |

Calibration in force: `ALPHA 0.3`, `WEAK_SKILL 0.42`, `HARD_SKILL 0.68`,
`MIN_RETRIEVAL_TOPIC_SCORE -5.0` (the code value; `ADAPTIVE-loop.md` still says
-6.0 in prose).

## The fixed instrument

All before/after numbers come from these three, unchanged between packets:

1. `python -m eval.run` — 15 cases, in-process graph, real LLM/Qdrant/sandbox.
2. The 30-probe topic set.
3. `python -m eval.transcript_probes` — T1..T13 over the live HTTP API on a
   fresh account, per teaching mode where escalation applies (added in P0).

## Where each blocker lives (file:symbol)

| Id | Symptom | Location |
|---|---|---|
| B1 | retrieval not in debug/explain/review prompts; empty citations | `app/agents/debugger.py::_debug_context_block`, `explain_bug`, `patch_code`; `app/agents/explainer.py::generate_line_explanations`, `generate_complexity_rationale`; `app/agents/reviewer.py`; `app/graph/subgraphs/{debug,explain}.py`; citations only built in `app/graph/subgraphs/dsa.py::_citation_labels`; rendering in `app/response/generate.py::_render_debug/_render_explain/_render_review` |
| B2 | nondeterministic test synthesis | `app/execution/synth.py::synthesize_test_suite/_synthesize` (one LLM call, no retry, provider default temperature); `app/graph/nodes.py::_resolve_test_suite` |
| B3 | `plan.topic` None on bare follow-ups | `app/agents/planner.py::analyze_problem` (no conversation source); `app/graph/nodes.py::plan_teaching`, `_event_topic`, `_latest_conversation_topic` (ladder key only, never the plan) |
| B4 | sibling confusion; factorial paste -> explain | `app/agents/planner.py::analyze_problem` (`context[0]` only), `MIN_RETRIEVAL_TOPIC_SCORE`; `app/input/intent.py::rule_intent/_keyword_intent/_default_intent`; `app/graph/routing.py::INTENT_ROUTES` |
| B5 | `pattern_family` unread | written by `app/knowledge/ingest.py`; never read in `app/memory/profile.py::skill_keys/apply_event` or `app/agents/planner.py::analyze_problem` |
| B6 | `/chat/stream` has no LangSmith parent | `app/graph/build.py::stream_graph` (accepts `tracer`, ignores it); `app/llm/client.py::Tracer.run` |
| B7 | `/chat` never creates a conversation | `app/graph/api.py::chat`, `_chat_stream_events`; `app/graph/nodes.py::_persist_turns` (skips when `conversation_id is None`) |
| F1 | follow-ups lose the problem | no stored `StructuredInput`: `app/memory/hint_progress.py` holds `(topic, level, solved)` only; `app/graph/nodes.py::load_learner_profile` loads recent message text but nothing reuses it as the active problem; `app/graph/subgraphs/dsa.py::_understand` |
| F2 | topic drift on follow-up | `app/agents/planner.py::analyze_problem` (retrieval on the bare follow-up text); `app/agents/hint_engine.py::_shape_hint` (splices `context_labels` from a different topic) |
| F3 | "Hint k of N" inconsistent; re-paste resets | `app/graph/nodes.py::_hint_topic_key`, `_problem_fingerprint`, `resolve_hint_progress`; ceiling from `planner.MAX_HINT_LEVEL_FOR_ASSISTANCE[plan.assistance_level]` recomputed per turn |
| F4 | full solution unreachable | `app/agents/planner.py::build_plan` escalation rule, `_explicit_solution_request`, `MAX_INITIAL_ASSISTANCE`, `clamp_assistance`; `app/graph/nodes.py::_record_verified_attempt` |
| F5 | generic / truncated rungs | `app/agents/hint_engine.py::_rung_text`, `_first_clause` (the `...` cut), `_grounded_clause`, `GENERIC_SHAPE_HINT` |
| F6 | in-message requirements ignored | `app/response/generate.py::_render_dsa` (fixed section set); `app/graph/subgraphs/dsa.py::_key_insight` |
| F7 | study-plan request enters the ladder | `app/input/intent.py::_default_intent/fallback_intent`; `app/graph/routing.py::select_route`; `app/agents/planner.py::DSA_ROUTE_INTENTS` |
| F8 | topic None on critical connections | `app/agents/planner.py::analyze_problem` + corpus `app/knowledge/corpus/{dfs,graphs}.md` (no bridges/Tarjan signal) |
| F9 | phantom progress steps; "Adapted to your level" always | `frontend/js/streaming.js`, `frontend/js/ui.js` (step list, adapted badge); server `app/graph/stages.py::STAGE_LABELS` + `stream_graph` emits `execute_code`/`verify` for every non-clarify route even when nothing ran |
| F10 | profile panel inconsistent; teaching mode has no backend effect | `app/memory/profile.py::suggested_focus/to_view`; `app/graph/nodes.py::_retrieval_topics`/exposure keys; `frontend/js/ui.js` profile panel + mode pill (`assistance_cap` only) |
| F11 | no traces visible in LangSmith | see P0 diagnosis below |

Line-level detail is filled in at the start of each packet, not here.

## Numeric targets (stated before work starts)

| Packet | Target |
|---|---|
| P0 | 5 live turns (3 `/chat/stream`, 2 `/chat`) -> exactly one `teaching_graph` root per turn within 30s, >=25 nested runs, redacted payloads, token+cost metadata on the root |
| P1 | follow-up probes resolve correct problem + topic 100%; 0 ladder resets on re-paste; N constant per ladder |
| P2 | 30-probe topic >=95%; routing >=95%; contentless probes -> None; 0 non-problem requests in the ladder |
| P3 | eval groundedness >=80%; section presence >=95% on transcript probes |
| P4 | full solution reachable per mode; hint_safety 100%; 0 truncated rungs; 0 cross-topic rungs; revealed code always sandbox-verified |
| P5 | 10/10 identical debug requests take the same evidence path; provenance 100%; 0 phantom steps |
| P6 | measured turns-to-leave-PRIOR speedup recorded; 0 untouched profile keys |
| P7 | scorecard >=95% of checks, all hard gates 100% |

## Architecture Decisions

(appended per packet)

## Packet log

### P0 — Instrument + LangSmith visibility (B6, F11)

**Target (stated first):** 5 live turns (3 `/chat/stream`, 2 `/chat`) -> exactly
one `teaching_graph` root per turn within 30s, >=25 nested runs, redacted
payloads, token + cost on the root.

**Diagnosis (F11).** Listing the project's root runs before any change showed
only flat, parentless `llm.chat` / `rerank` / `embedding.embed_query` roots and
no `teaching_graph` at all for every turn since the UI moved to
`/chat/stream`. Config was fine (key set, project `adaptive-coding-agent_v1`,
US endpoint, runs arriving). The cause was B6 alone: `stream_graph` accepted
`tracer` and ignored it, because `Tracer.run` wraps a coroutine and a
LangSmith run tree lives in a contextvar that an async generator cannot hold
across yields (confirmed via context7: the SDK itself runs generator bodies in
a captured context). Secondary gaps fixed alongside: no `flush` at shutdown
(the last runs before exit were lost), three separate LangSmith clients (LLM,
retriever, graph), and no `LANGSMITH_ENDPOINT` setting (an EU/self-hosted
workspace silently received nothing).

**What changed**
- `app/graph/build.py::stream_graph` runs the graph in its own task inside
  `tracer.run(...)` and forwards stage events through a queue as they arrive
  (no buffering); a client disconnect cancels the task.
- `app/llm/client.py::Tracer` — `run(metadata=...)`, `flush()`, `api_url` from
  the new `Settings.langsmith_endpoint`; token/cost keys allow-listed.
- `app/llm/pricing.py` (new) + `BudgetedLLMClient.usage_summary()` — per-turn
  `input_tokens / output_tokens / total_tokens / cost_usd / priced_calls`, on
  the root run's outputs AND metadata, plus `transport` (`chat` | `stream`).
- `app/main.py` — one shared `Tracer`, flushed (10s) on shutdown.
- `eval/transcript_probes.py` (new) — the fixed instrument (T1..T13 per mode,
  E escalation sequence, C continuity), `--langsmith` root-run check.

**Acceptance — PASS.** 3 stream + 2 chat turns on a fresh account
(`$TEMP/p0_accept.py`, kept out of the repo):

| Check | Result |
|---|---|
| one `teaching_graph` root per turn | 5/5 |
| parentless roots in the window | 0 |
| nested runs (routed turns) | 25, 26, 26, 25 — the `clarify` turn ("hi") has 12; clarify never runs `execute_code`/`verify`/an agent subgraph, so the >=25 bar applies to routed turns |
| token + cost metadata | present on all 5 (e.g. `transport=stream total_tokens=2126 cost_usd=0.000486`) |
| payload redaction | root inputs/outputs are allow-listed scalars; `llm.chat` inputs read `{'messages': '<list:1>'}` |

Run ids: `01a0fd9f-9b82-79c1-8c02-81351a80663d`, `01a0fd9f-a73c-7832-a2b5-e6476c89cb6c`,
`01a0fd9f-b067-73a1-846a-1518335185c2` (stream); `01a0fd9f-bb96-7c20-af54-7a03b6478084`,
`01a0fd9f-beb6-7511-ba56-bf9989500d4d` (chat).

The full baseline below also ran with `--langsmith`: **64/64 stream turns had
exactly one root with usage metadata, 0 stray roots.**

**Verification:** pyright 0 errors · ruff clean · `pytest tests -q` **1579
passed, 2 skipped** (+5: 2 stream-tracing, 3 pricing; `test_tracing.py`'s
expected output keys updated with the reason inline) · `alembic check` clean ·
`eval.run` and the 30-probe set unchanged (below).

#### BASELINE on the fixed instrument (before P1)

`eval.run`: routing 93.3% · topic 93.3% · hint_safety 100% · debug_fix 100% ·
groundedness 30%. 30-probe topic set: 90.0% (misses `dsa_binary_search ->
two_pointers`, `dsa_dp_2d -> trie`, `explain_dfs_traversal -> bfs`).

`eval.transcript_probes` (3 modes, 64 turns, `eval/results/P0-baseline.json`):
**245/360 checks = 68.1%**

| check | pass | | check | pass |
|---|---|---|---|---|
| completed | 39/39 | | no_phantom_steps | **4/62** |
| conversation_created (C1) | **0/1** | | no_truncated_text | 27/39 |
| hint_advanced | 3/4 | | no_cross_topic | 39/39 |
| hint_specific (T1) | **0/3** | | not_ladder (T12) | **0/3** |
| knows_problem | 9/12 | | recognition_explained (T7) | **0/3** |
| same_N | 3/6 | | section intuition/recognition/complexity (T1) | **0/3 each** |
| same_topic (T9/T10) | 3/6 | | topic_trees | 6/16 |
| no_heaps_drift (T3) | **0/3** | | full_solution_reachable (E, guidance+balanced) | **0/2** |
| revealed_code_verified (E8 challenge) | **0/1** | | route_clarify / topic_bfs / topic_graph_family | 3/3 each |
| ladder_resumed (T10) | 3/3 | | escalation_per_mode (T3/T5) | 6/6 |
| LangSmith root / usage | 1/1, 64/64 | | | |

Notes on the instrument, recorded once so later numbers are read correctly:
- `escalation_per_mode` (T3/T5) only checks "not a dead end" and is lenient; the
  `E` sequence is the real reachability measure.
- `revealed_code_verified` is a HARD GATE and already fails at baseline: in
  Challenge, E8 revealed code with `verification=None` (the reveal followed a
  verified attempt, but the revealed solution itself was never run).
- `topic_graph_family` passes with `union_find` for Critical Connections — in
  the graph family, but bridges are a DFS (Tarjan low-link) pattern. P2 owns it.

**Code review (medium) before commit:** 1 finding, fixed. A client disconnect
closed `_chat_stream_events` at a `yield`, which closed the session while the
new background graph task was still running on it (the inner generator was
only closed later by GC). Fixed with `contextlib.aclosing(stream_graph(...))`
in `app/graph/api.py` and `stream_graph` typed as `AsyncGenerator`; regression
test `test_closing_stream_graph_early_cancels_the_graph_task`. Final suite:
**1580 passed, 2 skipped**.

**Still not working after P0** (everything the baseline table shows failing):
follow-up continuity, ladder N, topic drift, escalation, phantom steps,
non-problem routing, grounded sections — P1..P6.

### P1 — Conversation continuity (B7, B3, F1, F3)

**Target (stated first):** follow-up probes resolve the correct problem + topic
100%; 0 ladder resets on a same-problem re-paste; N constant per ladder.

**Design (Architecture Decision AD-1: the conversation's ACTIVE PROBLEM).**
- `conversations` gains `active_problem` (JSONB `StructuredInput` dump —
  untrusted, stored like `messages.content`), `active_problem_key` (hash of the
  statement) and `active_topic` (closed-vocabulary slug). Migration
  `d1e2f3a4b5c6`. `hint_progress` gains `ceiling` (fixed at insert, never
  updated: `COALESCE` in the upsert).
- `load_learner_profile` loads it; `retrieve_knowledge` decides the turn's
  relation (`resolve_problem_relation`): **new** statement, **same** statement
  (equal after normalization, or one contains the other — a re-paste with a
  trailing "give full code" that normalization kept inside the statement),
  **followup** (no statement, no code, names no corpus subject), or **none**.
  A follow-up's `structured_input` becomes the stored statement + this turn's
  question (stored code is never re-run), and retrieval is re-run on it, so
  hint grounding/citations are about the problem, not about "give full answer".
- `analyze_problem(inherited_topic=...)`: new `topic_source="conversation"`,
  after an explicit hint and before profile/retrieval (F2).
- The hint ladder is keyed by the PROBLEM (`problem_key`), not the topic: two
  different trees problems in one conversation used to share one `trees`
  ladder. `ladder_ceiling` keeps the stored N unless the plan escalates to
  `full` or a client cap lowers it (F3: "Hint 1 of 4" -> "Hint 2 of 3").
- `/chat` and `/chat/stream` create the conversation when none is sent, and
  return 404 for a missing / foreign id (resolved before the stream starts).

**A measurement that changed the design.** The first cut decided "follow-up
vs own question" with the retrieval floor. Measured: "give full answer"
retrieves `heaps` at **-4.79** (above the -5.0 floor) and "hi" retrieves
`binary_search` at **+3.85** — so the score cannot separate them, and T3 kept
drifting to heaps. The decision is now lexical against the corpus's own
vocabulary (pattern/title/topic/aliases, `names_corpus_subject`): "what is a
trie?" is its own question, "give full answer" is a follow-up.

**Result on the fixed instrument — `eval/results/P1.json`: 278/360 = 77.2%
(baseline 68.1%).** P1's own checks:

| check | baseline | P1 |
|---|---|---|
| knows_problem (T2,T3,T5,T9) | 9/12 | **12/12** |
| same_topic (T9,T10) | 3/6 | **6/6** |
| topic_trees (T1..T5) | 6/16 | **16/16** |
| no_heaps_drift (T3) | 0/3 | **3/3** |
| hint_advanced (T2,C2) | 3/4 | **4/4** |
| same_N (T2,T10) | 3/6 | **6/6** |
| ladder_resumed (T10) | 3/3 | 3/3 (now on the problem's own ladder: 1->2) |
| conversation_created (C1) | 0/1 | **1/1** |

Follow-ups now record events against the inherited topic (B3) — postgres, one
probe account: `trees` 6 events, `union_find` 5, `bfs` 1, all `solved=NULL`
(exposure only, as designed). `eval.run` unchanged (routing 93.3, topic 93.3,
hint_safety 100, debug_fix 100, groundedness 30).

**Verification:** pyright 0 · ruff clean · `alembic check` clean · `pytest`
1604 passed, 2 skipped. Tests updated with the reason inline: retrieve-node
expectations (+`problem_relation`/`problem_key`), the stream "done == /chat"
comparison (ids differ now that each call creates a conversation), the
in-stream commit-failure test, and `test_chat_with_conversation_owned_by_another_user_does_not_leak`
(200-with-errors -> 404, still no leak). The table-less session stubs got a
one-conversation mixin (`tests/graph/_conversation_stub.py`).

**Known issues from P1**
- Code pasted without a statement never inherits the active problem (by
  design: a wrong statement must not judge unrelated code). A learner who
  pastes only their attempt on the active problem gets no statement-gated
  evidence for it.
- "hi" and the study-plan request are follow-ups under the lexical rule (no
  corpus term). Harmless for "hi" (clarify, no event), but T12 inherits the
  graph problem's ladder — P2 adds a non-problem intent so such turns never
  inherit.
- The stored ceiling is per ladder; an escalation to `full` still raises N for
  that turn (P4 decides how the reveal is presented).

**Code review (medium) before commit: 6 findings, all fixed.**
1. (high) the new `_p…` problem key could reach hint text as a "topic" via
   `_anchored_ladder_topic` (only `_q` was filtered) — now filtered.
2. (high) an error-only turn (pasted traceback) was inheriting the active
   problem and dropping its traceback — `.error` now blocks inheritance.
3. (med) a client cap on a ladder's FIRST turn became its permanent N — a
   capped turn no longer stores the ceiling.
4. (med) planner and hint engine computed "ceiling reached" differently —
   one helper, `base_ladder_ceiling`.
5. (med) a retriever failure (safe_node fallback) lost relation/key — the
   relation is now pure, decided before retrieval, and kept by the fallback.
6. (low) substring "same" merged a longer variant into the old ladder —
   `_is_repaste` allows at most 60 chars of extra text (room for an ask).
Regression tests added for 2, 5, 6. Single-mode re-check on the live API after
the fixes: all P1 checks still pass (knows_problem 4/4, same_topic 2/2,
topic_trees 6/6, same_N 2/2, ladder_resumed 1/1, conversation_created 1/1).
Final suite: **1607 passed, 2 skipped**.
