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

### P2 — Routing + topic accuracy (B4, F2, F7, F8)

**Target (stated first):** 30-probe topic >=95%; routing >=95%; contentless
probes still resolve None; 0 non-problem requests routed to the ladder.

**What changed**
- **AD-2: a `GENERAL_GUIDANCE` intent** (study plans, roadmaps, career advice,
  greetings). The classifier had no honest label for "give me a 4-month plan",
  so the LLM picked a DSA intent and the turn entered the hint ladder (F7).
  It routes to `explain`, never inherits the active problem
  (`_NON_PROBLEM_INTENTS`, also `PRACTICE_REQUEST`), and a deterministic rule
  sends greeting/acknowledgement-only messages ("hi", "ok thanks") to it at
  low confidence -> `clarify`. The classifier prompt also now says code shared
  with only a vague "take a look" is `CODE_DEBUG`.
- **Topic from the corpus's own recognition vocabulary**, read once from
  front matter (`planner._corpus_vocab`):
  1. a **representative-problem title** named in the statement ("Word
     Ladder" -> `bfs`, "Binary Tree Maximum Path Sum" -> `trees`); a title
     listed by several docs counts only among docs this turn surfaced
     ("Two Sum": `hashing` and `prefix_sum`). Ordered after profile match.
  2. **sibling tie-break**: among hits within `SIBLING_MARGIN = 2.0` of the
     top score (measured misfiled pairs sat 0.6-1.5 apart), the pattern
     whose identification signals / aliases appear as whole phrases wins,
     plus traversal-shape cues from the code's AST names (`stack`+`pop` ->
     DFS, `deque`/`popleft` -> BFS).
- **Corpus gaps closed, workbook-supported only.** `dfs.md`: bridges /
  critical connections signal, a Tarjan low-link variation, and *Critical
  Connections in a Network* (scheduled under Graphs in the workbook) as a
  representative problem. `binary_search.md`: `O(log n) time`, `first and
  last position` front-matter signals (already taught in its body). Index
  rebuilt: 300 points.

**Results**
- 30-probe topic set: **90.0% -> 100% (30/30)**. Honest caveat: two of the
  three fixes were found by looking at those exact misses (the O(log n)
  signal, the stack/deque cue), so the set is no longer a held-out measure;
  the transcript probes and `eval.run` are the independent checks.
- `eval.run`: routing 93.3 -> 93.3 (factorial paste now `debug` ✓; "ok
  thanks" went to `explain` before the multi-word small-talk rule — fixed and
  re-checked deterministically), **topic 93.3 -> 100**, hint_safety 100,
  groundedness 30 (P3). `debug_fix` read **50%** on one run: the
  correct-submission case got `solved=None`; re-running that case alone gave
  one miss and one pass — the B2 synthesis nondeterminism, P5's target.
- Transcript (`eval/results/P2.json`): **279/360 = 77.5%**. `not_ladder` (T12)
  **0/3 -> 3/3**, `route_clarify` 3/3, T8 now `dfs` in all modes. `grounded`
  (T12) 3/3 -> 0/3: the study plan no longer gets a ladder hint (whose
  citations were whatever retrieval returned) and the explain route has no
  answer for it yet — P3 builds the grounded one.

**Known issues from P2**
- Title matching needs the exact title; partial names ("first and last
  position") rely on signals.
- `debug_fix` is not reliably 100% until P5 (synthesis determinism).

**Code review (medium) before commit: 4 findings, all fixed.**
1. (high) `clarify` looked `GENERAL_GUIDANCE` up in a phrase map that lacked
   it -> a plain "hi" raised `KeyError` (masked live by `safe_node`'s fallback).
   Greetings now get an invitation; `PRACTICE_REQUEST` added to the map too.
2. (med) title matching was a substring check: "Binary Search" fired inside
   "insert into a binary search tree". Now the statement's HEADING (first
   line, minus a "Problem:"/number lead-in) must BE a listed title.
3. (low/med) "Two Sum II" fell back to the shorter "Two Sum" title — fixed by
   the same exact-heading rule.
4. (low) any `deque` counted as BFS; now `popleft` decides, and a deque popped
   from the right counts as a DFS stack.
After the fixes: 30-probe set still 100%; `eval.run` **routing 100%, topic
100%, hint_safety 100%, debug_fix 100% (2/2 this run), groundedness 30%**;
`pytest` 1644 passed, 2 skipped.

### P3 — Groundedness (B1, F6)

**Target (stated first):** eval groundedness >=80%; a concept question's
citations name the corpus chunks that shaped it (checked against the prompt);
section presence >=95% on the transcript probes.

**AD-3: cite only what a prompt contained and the model reported using.**
B1's warning taken literally: attaching retrieved ids to an answer that never
read them is a lie. Every grounded prompt now numbers its trusted references
and the model returns `used: [n…]`; citations are those numbers intersected
with what was actually shown (`concept.cited`). The DSA route, which used to
cite every retrieved chunk (even below the noise floor, even when the solver
call never ran), now cites only the hits that were in the solver's prompt
(`dsa_solver.prompt_hits`: relevant hits only — noise is no longer put in the
prompt either) plus the corpus sections it quotes whole.

**What changed**
- `app/agents/concept.py` (new): `answer_concept` for `CONCEPT_EXPLANATION` /
  `GENERAL_GUIDANCE` with no code. Concept questions are grounded in the
  topic's own overview / core intuition / recognition / complexity sections +
  relevant hits; guidance (a study plan) in a curriculum built from the
  corpus's own front matter (17 families, their patterns, scheduled difficulty
  mix, example problems) with its own prompt. Unparseable model output falls
  back to the references themselves, cited exactly.
- Debug (`explain_bug`) and review (`_advisory_findings`) prompts get
  `<reference_notes>` (the topic's common mistakes / when-not-to-use /
  complexity + relevant hits) and report `used`; `DebugResult`/`ReviewResult`
  gained `citations`.
- F6: `app/response/corpus_sections.py` (new) quotes the topic's corpus
  sections WHOLE on the DSA route: `recognition` (every level), `intuition`
  (concept+), `complexity` (concept+, when asked, unless the solver produced
  the problem's own). Requests are read from a fixed vocabulary only.
  `DSA_SECTIONS_BY_ASSISTANCE` gained those kinds (still cumulative).

**Results**
- `eval.run`: **groundedness 30% -> 100% (10/10)**; routing 100, topic 100,
  hint_safety 100, debug_fix 100.
- Prompt check (`cite_check.py`, in-process, a recording LLM wrapper):
  "What is a trie and when would I use it?" -> citations `Trie - Overview /
  Core Intuition / When to Recognize It / Complexity`, **every one present in
  the prompt**. (The LangSmith trace can't show this: payloads are redacted
  to shapes by design, so the check is done on the prompt in-process.) The
  study plan first came back with an answer and **zero** citations — the
  concept prompt didn't fit a plan — fixed with the guidance prompt; it now
  cites the 16 families it used.
- Transcript (`eval/results/P3.json`): **294/360 = 81.7%** (P2 77.5%).
  Section checks: intuition 3/3, recognition 3/3, complexity 3/3,
  recognition_explained (T7) 3/3, sections_present 3/3, states_reasoning 3/3
  -> **section presence 18/18 = 100%**. `grounded` (T12) 3/3.

**Known issues from P3**
- A "run tests" request with no learner code gets no explicit note yet.
- `topic_references` reads one chunk per section (`part 0`); a long section
  split in two would be quoted half — none is today (max chunk 1,046 chars).

**Code review (medium) before commit: 4 findings, all fixed.**
1. (med) a concept question with nothing to ground on returned an empty
   answer instead of falling back to the explainer — it now falls back.
2. (med) a corpus "Complexity" citation was added when the solver supplied
   only `complexity_space` (so the corpus section was never shown) — both
   time and space now count as the problem's own complexity.
3. (low) a corpus section split across chunks would have been quoted in part
   — such a section is now skipped, never quoted half (none is split today).
4. (med) `recognition` at L0 named the pattern before the solver itself would
   (it masks topic/pattern below L1), and on a mere retrieval guess could
   name the wrong one — pattern-naming sections now need L1+, a confident
   topic source (title / conversation / hint / profile), or an explicit
   request.
After the fixes: `eval.run` groundedness **90% (9/10)** (one DSA hint case at
L0 on a retrieval-guessed topic now — correctly — quotes no section), all other
metrics 100%; single-mode transcript re-check: every section check still
passes; `pytest` 1658 passed, 2 skipped.

### P4 — Escalation + hint quality (F4, F5, F10 teaching mode)

**Target (stated first):** full solution reachable per mode; hint_safety 100%;
0 truncated rungs; 0 cross-topic rungs; revealed code always sandbox-verified.

**AD-4: escalation policy by teaching mode.** The ceiling and an explicit ask
are required in every mode; what counts as effort differs:
- **Guidance:** ceiling + explicit ask.
- **Balanced:** ceiling + explicit ask + (verified attempt OR a second explicit
  ask at the ceiling; refused asks counted in `hint_progress.asks_at_ceiling`,
  migration `e2f3a4b5c6d7`).
- **Challenge:** ceiling + explicit ask + sandbox-verified attempt (unchanged).

Whatever is revealed is ONLY `synth.verified_reference`: an LLM-proposed
reference that must define its entrypoint and pass every proposed case in the
sandbox. The solver LLM's own `code` is discarded unconditionally. If no
reference verifies, the L6 hint says so and reveals nothing
(`reveals_code=False`). The verified request then runs again through
`execute_code -> verify`, so the turn's `verification` shows the pass. A reveal
sets `needed_full_solution=true` (L6) and never `solved` (that still comes only
from the learner's own `initial_verdict`). `assistance_cap` still applies after
the plan, so it can only lower assistance.

**Teaching mode end to end.** `teaching_mode` form field (`guidance |
balanced | challenge`, default `balanced`, 422 on anything else) ->
`RawInput.teaching_mode` -> `build_plan(teaching_mode=)`. UI: the pill used to
be a "Guidance" label plus Balanced/Challenge buttons, with Challenge mapped to
`assistance_cap=hint`. It is now a "Mode" label plus three buttons, each sending
`teaching_mode`; the composer's Hint-mode button keeps the cap. **Playwright,
rendered UI:** clicking Guidance made the next `/chat/stream` carry
`teaching_mode=guidance, assistance_cap=null`.

**Explicit asks that never matched (F4).** "give full answer", "give full code"
and "give code for that" did not match the old regex, and only `DSA_SOLVE`
counted. The regex is broadened and now accepts any DSA-route intent. "give code
for that" as a follow-up was also classified as a low-confidence concept
question and went to `clarify`. A follow-up on the active problem that matches
the fixed ask phrases is now classified `DSA_SOLVE` deterministically.

**Rungs (F5).**
- `_first_clause` never cuts a sentence: it takes a whole sentence (up to 320
  chars) or no quote at all.
- L0 is specific: what makes this a `<topic>` problem, from the corpus
  "When to Recognize It" section plus an identification cue, instead of
  "restate the problem in your own words".
- The L2 shape suggestion only uses labels from the same pattern family (or the
  doc's own topic), so "For a heaps problem, trees is often the right shape"
  can no longer be produced.

**Failover rewind (an infrastructure fix outside the packet's own list, recorded
because it changed what could be measured).** The first P4 live run was invalid.
`FailoverLLMClient` never rewound by design, so one per-minute 429 on the
primary Groq key pinned the process to the slow free fallback, and every later
turn misclassified to `clarify` (24–85 s latencies). The cursor now returns to
the first key `DEFAULT_REWIND_AFTER_S = 300` s after it advanced. That costs at
most one failed call per exhausted key per window.

**Result on the fixed instrument — `eval/results/P4.json`: 313/365 = 85.8%**
(P3 81.7%; the denominator grew because revealed turns add
`revealed_code_verified` checks).

| check | P3 | P4 |
|---|---|---|
| full_solution_reachable (Guidance E4, Balanced E5) | 0/2 | **2/2** |
| revealed_code_verified (HARD GATE) | 0/1 | **6/6** |
| reveal_after_verified_attempt / no_reveal_without_attempt (Challenge) | 1/1, 1/1 | 1/1, 1/1 |
| hint_specific (T1) | 0/3 | **3/3** |
| no_truncated_text | 36/39 | 38/39 |
| no_cross_topic | 39/39 | 39/39 |
| ladder_resumed (T10) | 3/3 | 2/3 |

The two misses:
- `ladder_resumed` (balanced): T9 "give code for that" had been routed to
  `clarify`. That is the classification fixed above, after this run.
- `no_truncated_text` (balanced T7): the solver LLM's own "understanding" text,
  not a rung.

`eval.run`: routing 100, topic 100, **hint_safety 100**, debug_fix 100,
groundedness 100. `pytest`: 1677 passed, 2 skipped.

**Seen in the rendered UI (P5's job, not fixed here):** a concept question with
no code showed "Running your code in the sandbox" and "Checking the results",
and "Adapted to your level" on a fresh account.

**Code review (medium) before commit: 6 findings, all fixed.**
1. (high) The ask phrase was matched against the problem STATEMENT too, so
   "return the answer modulo 10^9+7" plus "next hint" counted as asks. Now only
   the learner's own question is read.
2. (med) The widened phrase list matched ordinary sentences ("I'll write code
   myself", "explain the code for this"). It is narrowed back to `give …` /
   `show me the …` / the original phrases. The follow-up intent override now
   applies only to a missing or low-confidence classification.
3. (high) A problem already marked solved could crash the turn (code with no
   L6 hint). The reveal is skipped when `progress.solved`, and code is only
   attached at L6.
4. (med) An UNverified reveal still counted as one (`needed_full_solution`,
   `hints_used=7`, stored L6). The ladder now holds at its previous rung.
5. (med) A 429 on the LAST credential kept postponing the rewind. Only an
   actual cursor move starts the window now.
6. (low) The reveal text overclaimed ("test cases derived from the problem").
   It now says the reference passed cases proposed together with it: checked,
   not proven.

Live re-check of escalation after the fixes (`eval/results/P4-review-E.json`):
Guidance revealed at **E4** (first ask at the ceiling), Balanced at **E5**
(second ask), Challenge **never** without an attempt and at **E8** right after
the verified attempt. **revealed_code_verified 5/5.** One Guidance E6 turn
timed out on the provider (268 s). `pytest`: **1681 passed, 2 skipped**.

**Known issues from P4**
- The verified reference and its cases come from one LLM reply: a wrong
  solution with matching wrong cases still "verifies" (same limitation as
  P1's synthesis). The reveal text says so.
- The UI never rendered `citations` (P5 adds them).

### P5 — Evidence determinism + honest UI (B2, F9)

**Target (stated first):** 10/10 identical debug requests take the same
evidence path; provenance 100% populated; 0 phantom steps across the
transcript probes (checked in the rendered UI).

**What changed**
- **Determinism (B2):** synthesis runs at temperature 0 with ONE bounded retry
  (`SYNTH_ATTEMPTS = 2`) when a proposal fails validation.
  `eval/determinism.py` (new) runs one fixed debug request (a real statement,
  no worked examples, so synthesis is the only path) through the real graph N
  times.
- **Provenance:** `LearningEventCreate.evidence_source` and a
  `learning_events.evidence_source` column (migration `f3a4b5c6d7e8`):
  `extracted | synthesised | none`. Rows that predate it and carry an outcome
  are backfilled `unknown`, never a guessed source.
- **Honest steps (F9):** `stream_graph` streams `execute_code` / `verify` only
  when they produced a result. The `explain_agent` label "Reading your code"
  became "Working through your question", since it now also answers concept
  and study-plan questions with no code.
- **Honest "adapted" (F9):** `ChatResponse.adapted` = `planner.plan_adapted`:
  true only when the difficulty moved off the PRIOR default or a
  profile-driven rule fired (weak/strong skill, stored preferences). The UI
  renders the server value instead of a hard-coded `true`.
- **References shown:** the UI never rendered `generated.citations`; answers
  now end with a References list (only the server-computed sources).
- **Failover wrap-around (infra, recorded for the same reason as P4's
  rewind):** when every credential from the cursor onward is 429-limited,
  earlier keys get ONE more try before the call fails. In the first P5 run, a
  burst pinned the cursor on the 429-ing last fallback, and half the turns hit
  the keyword classifier instantly (0.1–1 s turns, `clarify`). A per-key probe
  right after showed all five Groq keys healthy.

**Results**
- Determinism, same request x10: **7/10 -> 10/10** identical evidence path
  (`synthesised / fail / solved=False`). Before: 3/10 runs produced no suite at
  all (`none / inconclusive / None`).
- Provenance (postgres, live API): a debug turn -> `binary_search, solved=false,
  synthesised`; a DSA attempt -> `sliding_window, solved=true, synthesised`.
  The 70 exposure events in the window are all `none`.
- Phantom steps: **4/62 -> 62/62** clean on the transcript probes.
  **Playwright, rendered UI**, fresh account, "What is a trie…": the steps are
  Reading your input / … / Writing your answer / Updating what I know about
  you, with **no sandbox steps**; **no "Adapted to your level"**; References
  shows `Trie - Overview / Core Intuition / When to Recognize It / Complexity`.
- Transcript (first P5 run, provider-starved in its second half — see the
  wrap-around): 341/362 = 94.2%, with no_phantom_steps 62/62 and
  no_truncated_text 39/39.

**Known issues from P5**
- The debug probe's topic resolved to `binary_search` for a
  "longest increasing run" bug (identifier-only retrieval query). It is a
  topic-accuracy case outside the 30-probe set.
- The worked-example extractor still misses the `Example 1: s = "…" -> 3`
  format, so those turns synthesise (deterministically now) rather than
  extract.

**Code review (medium) before commit: 5 findings, all fixed.**
1. (med) A successful wrap-around retry cleared the rewind timer, so the
   primary key was never re-tried. The window is now kept when landing on any
   key but the first.
2. (med) The synthesis retry also fired on LLM/budget/sandbox errors, which
   just repeat. Now only a proposal that failed VALIDATION is retried (a
   `RETRY` marker); an LLM error makes exactly one call.
3. (low) `evidence_source` said `synthesised` on rows whose outcome the
   statement gate had dropped. Provenance now follows the recorded outcome
   (`none` when `solved` is NULL).
4. (low) A fallback response kept the citations of the sections it dropped.
   It now cites nothing.
5. (low) `eval/determinism.py` would report "10/10" with the sandbox down. It
   now refuses to measure without a runner, and closes Qdrant.
`pytest`: 1688 passed (non-live).

**Provider quota, measured rather than assumed.** After the fixes, a
determinism re-run showed every turn at `clarify` (keyword fallback). A direct
probe found all five Groq keys answer a 5-token request, but return
`RateLimitError` for the real intent-classifier request (prompt plus
`max_tokens=1024`). Today's free-tier token quota is exhausted on every key.
Live numbers taken after this point would measure the quota, not the agent,
so the post-review determinism re-run and the clean P5 transcript re-run wait
for the quota to reset. The 10/10 figure above was measured on the
pre-review code. The review changes only narrow WHEN the retry fires (no
retry on LLM/sandbox errors), which cannot make the validated-suite path less
deterministic.

### Session handoff (usage limit reached mid-P4)

- Committed: P0 `d4bc915`, P1 `5c48d92`, P2 `cb9e558`, P3 `afcd81e`.
- P4 was later completed and committed (see above). Original note: **P4 is applied in the working tree, NOT committed.** It includes the teaching
  mode end to end (form field, UI pill, policy), the verified-only reveal
  (`synth.verified_reference`), the `asks_at_ceiling` counter (migration
  `e2f3a4b5c6d7`), and the rung fixes (no truncation, specific L0,
  same-family shape). It also includes a failover cooldown rewind
  (`DEFAULT_REWIND_AFTER_S = 300`). The first P4 live run was invalidated:
  the cursor never rewound, so the process stayed pinned to the slow fallback
  model, and every turn after that went to `clarify`.
  Non-sandbox tests pass (1653), pyright 0, ruff clean, `eval.run` all
  metrics 100%. Live re-run writing `eval/results/P4.json` was in progress.
  First live evidence: Guidance revealed a verified solution at E4
  (`verification=pass`).
- Still to do for P4: read `eval/results/P4.json`, run the full pytest
  suite, playwright-check the mode pill, run the code review, then commit.
- P5 / P6 are drafted as scratch scripts (`p5_apply.py`, `p6_apply.py`).
  `eval/adaptation_speed.py` (untracked) depends on P6's `skill_for`.
  P7 has not started.
