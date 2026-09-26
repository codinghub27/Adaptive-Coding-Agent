# FEATURE — Closing the adaptive loop

## Status
Done — verified end to end against Postgres + Qdrant and a live LLM provider.

## Why

The agent renders "Adapted to your level" on every turn, but for a new learner
there is no level: the profile is empty and stays empty forever. This document
records the audit, the root cause, and the ordered work to fix it.

---

## Audit (2026-09-26)

### What is already built and working

**Phase 05 RAG is real and good.** `app/knowledge/` has ingestion, a 13-file
curated corpus, dense retrieval (Qdrant, `bge-small-en-v1.5`), BM25, RRF
fusion and a cross-encoder reranker, all fail-soft. Probed live against the
running index:

| Question | Top retrieved labels |
|---|---|
| "array [2,7,11,15] target 9, find indices" | `two_pointers`, `hashing` |
| "give problem to solve using 2 pointers" | `two_pointers` (all four hits) |
| "how do I find the shortest path in a graph" | `bfs`, `graphs` |
| "my recursion for permutations is too slow" | `backtracking`, `dfs` |
| "longest palindrome problem" | `sliding_window`, `dynamic_programming` |

The corpus is an accurate topic vocabulary. **It is simply never consulted for
topic inference.** RAG is not missing — it is disconnected from the adaptive
loop.

### The root cause: a bootstrap deadlock

```
analyze_problem() infers a topic ONLY by matching the learner's existing
skill_levels keys against the prose  (app/agents/planner.py::_best_topic_match)
        |
        v  a new account's skill_levels is {}
plan.topic = None, on every turn, forever
        |
        v  update_learner_model only builds an event when topic is not None
no learning event is ever written
        |
        v
skill_levels stays {} -----------------------+
        ^                                    |
        +------------------------------------+
```

A second, independent lock sits on the same loop: `_persist_event` only writes
when `agent_output.solved is not None`, and `DSAResult.to_outcome` deliberately
leaves `solved=None` ("this result carries no direct evidence"). So even a
topic-bearing hint turn records nothing.

### Gap list

| # | Gap | Where |
|---|---|---|
| G1 | Topic inference reads only the profile's own skill keys | `app/agents/planner.py::analyze_problem` |
| G2 | Retrieval runs *after* planning, so its labels cannot inform the plan | `app/graph/build.py` edge order |
| G3 | An event is only built when a topic is already known | `app/graph/nodes.py::update_learner_model` |
| G4 | An event is only persisted when `solved is not None` | `app/graph/nodes.py::_persist_event` |
| G5 | Difficulty is always `medium` because skill falls back to `PRIOR` | consequence of G1–G4 |
| G6 | Retrieved context reaches the learner only as a data-structure noun in one hint rung | `app/agents/hint_engine.py` |

### A trap to avoid

`outcome_score` maps any not-solved event to `UNSOLVED_SCORE`. Simply removing
the G4 gate would therefore record every question a learner asks as a failure
and drive their skill *down* for asking. Asking for a hint is evidence of
**exposure**, not of failure. The fix must separate the two.

---

## Plan

**Packet A+B — retrieve first, then infer the topic from what came back.**
- Move `retrieve_knowledge` before `plan_teaching` in the graph.
- Drop `plan.topic` from the retrieval query (it was circular) and gate
  retrieval on intent + structured input rather than on the not-yet-computed
  plan.
- `analyze_problem` gains a retrieved-context source. Resolution order:
  explicit `topic_hint` > profile skill match > top retrieved chunk's
  `pattern`/`topic` > `None`. Only trusted corpus slugs are ever used.
- `ProblemAnalysis.topic_source` gains `"retrieval"`.

**Packet C — exposure events, so the profile can bootstrap.**
- Record an event whenever a topic is known, even with no observed outcome.
- A no-outcome event contributes a *neutral* score, creating the skill key
  without penalising the learner for asking.

**Packet D — verify the loop end to end.**
A new account asks about Two Sum and the skill map gains `hashing` /
`two_pointers`; a later turn on the same topic is planned from that skill
rather than from `PRIOR`.

## Test Results

`pytest tests -q` -> **1403 passed, 2 skipped** (1373 before this work).
pyright strict: 0 errors. ruff check + format: clean. `alembic check`: no
pending operations.

### The loop, driven over the real HTTP API on a brand-new account

```
START            skills={}

TURN  "Given an array [2,7,11,15] and target 9, find the indices..."
  route=dsa  topic='two_pointers'  events_persisted=1
  PROFILE    skills={"two_pointers": 0.5}

TURN  "Give me the next hint."
  route=dsa  topic=None  hint_level=1  events_persisted=0
  PROFILE    skills={"two_pointers": 0.5}          <- unchanged, correctly

TURN  "How do I find the shortest path in an unweighted graph?"
  route=explain  topic='bfs'  events_persisted=1
  PROFILE    skills={"bfs": 0.5, "two_pointers": 0.5}
```

The profile bootstraps from an empty map, the ladder continues across a
contentless follow-up instead of restarting, and no junk key is written.

### Two defects this end-to-end test caught, which the unit suite did not

1. **A contentless follow-up inferred a topic from noise.** "Give me the next
   hint." returned `trees`, restarted the ladder at rung 0 and wrote a bogus
   skill. Fixed with `MIN_RETRIEVAL_TOPIC_SCORE` (see below).
2. **The solver LLM's free-text topic became a skill key.** `DSAResult.topic`
   is model output ("A short topic tag for this problem"); for the same
   follow-up the model answered `"unknown"`, which landed in the profile as a
   skill. Event topics now come from `plan.topic` only, and the LLM-authored
   `pattern` is kept only when this turn's above-floor retrieval vouches for
   it.

### The relevance floor, and why it is not `score > 0`

The reranker emits raw cross-encoder logits that are routinely negative for
correct matches, so an absolute "positive means relevant" cutoff would discard
most good answers. Measured against this corpus and reranker:

| | top score |
|---|---|
| "array [2,7,11,15] target 9" | -2.15 |
| "shortest path in a graph" | +6.63 |
| "recursion for permutations is too slow" | -3.25 |
| "Give me the next hint." | -8.39 |
| "I've worked through the hints..." | -10.09 |
| "ok thanks" | -10.89 |

`MIN_RETRIEVAL_TOPIC_SCORE = -6.0` sits in the empty band between the two
clusters. It separates "a real question" from "no question at all", not
"relevant" from "irrelevant". It is corpus- and model-specific: re-measure it
if `reranker_model` or the corpus changes. A test pins the measured values so
the constant cannot be moved silently.

## P1 — an outcome signal (2026-09-26)

Known Issue #1 below said skills start and stay at `PRIOR` because nothing
observes whether a learner succeeded. P1 builds that observation.

### The approved design

The LLM proposes a reference solution **and** test cases. The sandbox runs the
REFERENCE against those cases first, and the suite is discarded unless the
reference passes every one. Only a suite that survives that check is ever used
to judge a learner. Both directions count: a pass raises the skill, a failure
lowers it.

### What shipped

**P1a (`85c1e77`)** — `app/execution/synth.py::synthesize_test_suite`.
`testgen.py`'s three `ast` helpers became public for reuse; it stays LLM-free.
The entrypoint is always chosen from the learner's own code via
`select_entrypoint`, never the LLM's free-text name: an invented name makes the
sandbox report `entrypoint_missing`, which `verify` turns into a real `fail` —
a false failure recorded against the learner.

**P1b** — wired into `debug_agent` and the review branch of `explain_agent`
via `_resolve_test_suite` (extraction first, synthesis only as fallback), plus
two semantic fixes:

1. **`DebugResult.to_outcome` now derives `solved` from `initial_verdict`, not
   `final_verdict`.** `final_verdict` is the verdict *after the debugger
   patched the code*, so "the agent fixed it" was being recorded as evidence
   about the learner. `initial_verdict` is the verdict on the code they
   actually submitted.
2. **A synthesised suite may only set `solved` when the turn carries a real
   problem statement** (`AgentState.suite_source` + the gate in
   `_build_learning_event`). With no statement, the learner's buggy code is the
   only spec the synthesiser has, so it can write cases matching the bug AND a
   reference reproducing it — self-consistent, sandbox-passing, and it would
   certify broken code as `solved=True`. The suite is still used to produce a
   better answer; it just never judges the learner. An `extracted` suite is not
   gated: its cases come from the statement's own worked examples.

The gate is not theoretical. Driving a bare `factorial` paste live, the
synthesiser reproduced the off-by-one in **both** its cases and its reference,
the sandbox returned `pass` on 5/5, and the gate forced `solved=None` anyway.

### Measured, over the real HTTP API on a brand-new account

```
START                                        skills={}

TURN A  statement + CORRECT code
  route=explain  topic='hashing'         verification=pass(6 cases, synthesised)
  event.solved=True                          skills={'hashing': 0.6}        ABOVE prior

TURN B  statement + BUGGY code
  route=debug    topic='sliding_window'  verification=pass(6 cases, synthesised)
  event.solved=False                         skills={..., 'sliding_window': 0.42}  BELOW prior

TURN C  code only, NO problem statement
  route=debug    topic=None              verification=pass(5 cases, synthesised)
  events_persisted=0                         skills unchanged, bit-identical
```

Turn B is worth reading twice: the turn's `verification` says `pass` (the
debugger's patch works) while the event says `solved=False` (the learner's own
code did not). That is fix #1 doing its job.

`pytest tests -q` -> **1486 passed, 2 skipped**. pyright strict: 0 errors on
every file this work touched. ruff + `alembic check`: clean.

### Known issues from P1

1. **A reference and its cases can be wrong together in the same way.** The
   sandbox check catches an *inconsistent* suite, not a confidently wrong one.
   With a problem statement present this is much less likely, which is exactly
   what the gate leans on — but it is not eliminated.
2. **Turn C's skill map is bit-identical only because no topic was inferred.**
   When a topic *is* inferred from a contentless turn, the documented exposure
   path still creates that key at `PRIOR`. That is by design (Packet C), not a
   P1 regression — but it means a wrong topic still costs a junk key. Observed
   live: a `factorial` off-by-one was filed under `binary_search_on_answer`.
   That is F2, and it is P2's job.
3. **Synthesis costs one LLM call plus one sandbox run** on every debug/review
   turn with no worked examples, and there is no kill switch. `GraphContext`
   carries no settings, and threading one through means editing
   `app/graph/build.py`, which currently holds unrelated uncommitted work.
4. **`_resolve_test_suite` runs even when `state.execution_request.tests` is
   already set**, in which case `run_debug` discards the synthesised suite and
   the LLM call is wasted — and `suite_source` would mislabel the turn.
   Unreachable today (nothing populates `execution_request` before the agent
   node), so it is recorded rather than fixed.
5. **The DSA route still reports `solved=None` always.** Option (c) — a learner
   submission verified against a validated suite — is P1c, not yet built.

## Known Issues

1. **Skill levels start and stay at `PRIOR` (0.5) until a turn is solved.**
   Exposure creates the key; only an observed outcome moves it. Nothing yet
   observes whether the learner actually solved a DSA problem, so in practice
   the map fills with topics at 0.5 and difficulty stays `medium`. Closing that
   needs an outcome signal (the sandbox already verifies debug fixes; DSA has
   no equivalent).
2. **A bare follow-up is recorded as nothing at all.** Correct today -- there is
   no trusted topic -- but it means turn counts under-represent engagement.
3. **The floor is calibrated on six measurements.** Sound for the gap it has to
   bridge, but it deserves re-measuring against a larger sample.
4. **`topic_source="retrieval"` trusts rank alone above the floor.** Only the
   top hit is consulted; a second-place hit is never considered even when the
   scores are nearly tied.
