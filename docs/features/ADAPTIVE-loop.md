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
