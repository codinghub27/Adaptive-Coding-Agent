# FEATURE — DSA Knowledge Corpus Expansion

## Status
Done (2026-09-26). Inspected at `bdf01c5`; rebased onto `df6c700`
(two unrelated commits landed mid-feature). Commit `fd8f373`, pushed to `origin/main`.

## Goal
Expand the Phase 05 knowledge corpus from 13 short pattern definitions to the
full spreadsheet-grounded pattern taxonomy (30 docs, 10 sections each), so the
agent can *recognize* a pattern from a problem statement and *teach* why it
works — not just define it.

This EXPANDS Phase 05. The RAG architecture (Qdrant, BM25, RRF, reranker,
`RetrievalHit`, the `retrieve_knowledge` node, fail-soft degradation) is
unchanged.

## Source of truth
Both workbooks are checked in under `docs/source/dsa/` with an extractor:

| File | Sheets used |
|---|---|
| `DSA_Placement_Ready_TopicPatternWise.xlsx` | `Pattern-Wise Map` (20 patterns: Pattern / Recognition Cue / Representative Problem / Link) |
| `DSA_LeetCode_6Month_Plan.xlsx` | `Quick Reference` (21 pattern→cue), `Topic Summary` (18 topics, counts, difficulty mix, focus tips), `Full Schedule` (360 problems) |

`Beyond-DSA Gaps` and `Progress Tracker` are deliberately ignored.

```
venv/Scripts/python.exe docs/source/dsa/extract.py   # -> docs/source/dsa/dsa_source.json
```
`dsa_source.json` is the grounding data corpus authoring reads from, so no
authoring step re-parses Excel.

## Scope
- `app/knowledge/corpus/` — 13 docs enriched to the 10-section structure,
  17 new docs added (30 total).
- `app/knowledge/ingest.py` + `app/schemas/knowledge.py` — four **optional**
  front-matter keys (`pattern_family`, `difficulty`, `representative_problems`,
  `identification_signals`), carried into `KnowledgeChunk.metadata` and BM25.
- `EXPECTED_PATTERNS` 13 → 30; corpus completeness tests updated.
- Index rebuild + corpus-expansion retrieval tests.

## Out of Scope
- The retrieval algorithm, RRF params, reranker choice, `RetrievalHit` shape.
- Beyond-DSA subjects (SQL / DBMS / OS / CN / System Design / Aptitude / Resume).
- Ingesting anything outside the curated corpus.
- Agent-side consumption of the new metadata (Phase 07 territory).

## Current Implementation (inspected 2026-09-26, HEAD `bdf01c5`)
- **Corpus** — 13 markdown docs, ~3.5 KB each. Front matter is exactly
  `title, pattern, topic, aliases`; body is an H1 plus **6** `## ` sections
  (When to use / Recognition signals / Template / Complexity / Common mistakes
  / Variations).
- **`parse_document`** — `_EXPECTED_KEYS` was a *closed* set: any unknown
  front-matter key raised `CorpusError`. This was the one hard blocker on
  richer metadata; docs alone could not carry it.
- **`chunk_document`** — one chunk per `## ` section, `MAX_CHUNK_CHARS = 1200`,
  metadata `{section, part, aliases, content_hash}`, id =
  `uuid5(KNOWLEDGE_NAMESPACE, "{source}#{section}#{part}")`.
- **`KnowledgeChunk.metadata: dict[str, str]`** already existed, so new
  metadata is additive at the schema level; only `CorpusDocument` and
  `parse_document` needed new fields.
- **`bm25_text`** folds in `title`, `aliases`, `pattern`.
- **`EXPECTED_PATTERNS`** (frozenset, 13) is asserted against the live corpus by
  `tests/knowledge/test_corpus.py`, which also pins a `pattern -> topic` table.
- **Infra** — Postgres and Qdrant `v1.19.0` healthy (compose project
  `adaptivecodingagent`). Collection `dsa_knowledge`: **78 points, 384-d
  Cosine, green** (baseline).

## Taxonomy reconciliation (P1)
30 docs. 13 ENRICH + 17 NEW.

| Family | Patterns |
|---|---|
| Arrays & Hashing | `hashing`*, `prefix_sum`* |
| Two-Pointer / Window | `two_pointers`*, `sliding_window`*, `fast_slow_pointers` |
| Stack | `stack`, `monotonic_stack` |
| Searching | `binary_search`*, `binary_search_on_answer` |
| Linked List | `linked_list` |
| Trees | `trees`* |
| Heap | `heaps`* |
| Backtracking | `backtracking`* |
| Graphs | `graphs`*, `bfs`*, `dfs`*, `union_find`, `topological_sort`, `dijkstra`, `bellman_ford` |
| Dynamic Programming | `dynamic_programming`*, `dp_1d`, `dp_2d` |
| Greedy | `greedy`* |
| Intervals | `intervals` |
| Bit Manipulation | `bit_manipulation` |
| Trie | `trie` |
| Divide & Conquer | `divide_and_conquer` |
| Math & Geometry | `math_geometry` |
| Advanced Range | `segment_tree` |

`*` = existed before this feature (ENRICH); the rest are NEW.

**Growth:** 13 -> 30 docs, 47,835 -> 153,497 bytes of corpus, 78 -> 300
indexed chunks. Every doc is 10 sections, so a section is the retrieval unit and
each pattern contributes the same 10 facets (recognition, intuition, signals,
template, pitfalls, boundaries, variations, problems).

### Topic vs. pattern — recorded distinctions
The sheets do not treat these uniformly, so the corpus makes the call explicit:
- **`segment_tree`** appears in `Quick Reference` only. It has no representative
  problem and **no topic in the 360-problem schedule**. Authored with canonical
  problems explicitly marked as *not in the 6-month plan* (owner decision).
- **`bellman_ford`** appears in `Quick Reference` only. Its problems sit under
  the *Advanced Graphs* topic (`Cheapest Flights Within K Stops`,
  `Network Delay Time`).
- **`stack`**, **`linked_list`**, **`trees`**, **`math_geometry`** are schedule
  *topics*, not patterns in either map. Included (owner decision) because they
  carry 42 / 28 / 42 / 12 problems; each doc says so in §1.
- **`prefix_sum`** and **`dynamic_programming`** / **`graphs`** are kept from the
  original corpus. The latter two become umbrella docs that point at the
  narrower new patterns.
- **`binary_search`** (the mechanic) and **`binary_search_on_answer`** (the
  monotonic-predicate reframing) are separate docs: the sheets list them
  separately and they have different recognition cues.

## Required structure for every pattern doc
1. Overview · 2. When to Recognize It · 3. Core Intuition · 4. Identification
Signals · 5. General Template · 6. Complexity · 7. Common Mistakes · 8. When NOT
to Use · 9. Variations · 10. Representative Problems (name + difficulty + link,
from the schedule).

## Code review (high effort, pre-commit)
Seven findings; all resolved. Two MEDIUM findings changed code:

1. **`bm25_text` narrowed the reranker pool for no accuracy gain** -- reverted,
   reproduced the measurement first (see Architecture Decisions).
2. **Skill-map fragmentation / `pattern_family` unconsumed** -- recorded as the
   headline Known Issue and follow-up; agent-side changes are Phase 07 scope.
3. **Stale fusion-eval gold labels** -- three re-pointed; this recovered the
   apparent dense "regression" (see Test Results).
4. **`graphs.md` and `topological_sort.md` taught contradictory error
   contracts** for the same algorithm (`raise ValueError` vs `return []`), and
   both are retrievable for one query. The umbrella doc was de-scoped: its
   template is now `build_adjacency` + a dispatch comment pointing at the six
   narrow graph docs, and its aliases/signals no longer compete with them.
5. **`heaps.md`'s `k_largest(nums, 0)` raised `IndexError`** -- `k <= 0` guard.
6. **`dijkstra.md` / `bellman_ford.md` annotated `list[int]` but returned
   `list[float]`** -- fixed. All 30 templates are now checked under **pyright
   strict** (extract each ```python fence to a temp package with its own
   `typeCheckingMode = "strict"` config; the repo's own `include = ["app",
   "tests"]` silently checks nothing outside those roots, so a naive run
   reports a false clean -- verified with a deliberately reintroduced error).
7. Untracked scratch files at repo root -- excluded from the commit.

## Work packets
| P | Packet | Status |
|---|---|---|
| P1 | Taxonomy reconciliation | Done |
| P2 | Optional metadata schema (additive) | Done, verified |
| P3a | Author NEW docs — graphs + searching + stack/pointer families | Done, verified |
| P3b | Author NEW docs — DP, intervals, bits, trie, D&C, segment tree, math | Done, verified |
| P3c | Enrich the 13 existing docs 6 -> 10 sections + repair 4 format-bound tests | Done, verified |
| P4 | Rebuild the index; confirm idempotence and point growth | Done, verified |
| P5 | Corpus-expansion retrieval tests | Done, verified |

## Architecture Decisions
- **Optional, not required, front matter.** `_EXPECTED_KEYS` split into
  `_REQUIRED_KEYS` (the original four) and `_OPTIONAL_KEYS` (the new four), so
  every pre-existing doc keeps parsing and the migration can be incremental.
- **`representative_problems` splits on `;`, not `,`** — LeetCode titles contain
  commas (`Pow(x, n)`, `Sum of Two Integers`). Each entry stays the raw
  `Name | Difficulty | URL` string; the corpus is display data, not a model.
- **`identification_signals` is folded into `bm25_text`.** Recognition cues are
  short keyword phrases ("next greater element", "prerequisites"), exactly the
  query shape BM25 is good at — this is what makes cue queries land.
- **Chunk ids and `content_hash` are unchanged** for existing docs: ids stay
  `uuid5(source#section#part)` and the hash still covers chunk text only, so
  metadata edits never churn the whole index.
- **Spreadsheets checked in + extractor script.** Provenance is in the repo, and
  authoring reads `dsa_source.json` rather than re-parsing Excel.
- **Section headings are unnumbered.** The 10 required sections are fixed and
  ordered, but written `## Overview`, not `## 1. Overview`: `slug_tag` turns the
  numbered form into `1._overview`, which leaks an ordinal into
  `KnowledgeChunk.metadata["section"]` and into the chunk text prefix the
  embedder sees. Order is carried by the file, not by the heading text.
- **One chunk per section.** Authors keep each section body under ~1,000 chars
  so a 10-section doc yields exactly 10 chunks. Longer sections still work (the
  chunker splits on paragraphs) but fragment a single idea across hits.
- **`EXPECTED_PATTERNS` grew to 30 in P2, before the docs existed**, so the two
  corpus-completeness tests were marked `xfail(strict=False)` with a
  `TODO(P3)` until authoring lands. The tests were not weakened or deleted.

## Commands Run
```text
venv/Scripts/python.exe -m pip install openpyxl        # needed to read the workbooks
venv/Scripts/python.exe docs/source/dsa/extract.py     # 20 patterns, 21 cues, 18 topics, 360 problems
curl -s localhost:6333/collections/dsa_knowledge       # baseline: 78 points, 384-d Cosine, green

venv/Scripts/python.exe -m scripts.build_index         # x2 -> 300 points, deleted_stale 39 then 0 (idempotent)
curl -s -X POST .../points/scroll                      # 30 patterns x 10 chunks, metadata on all 300
venv/Scripts/python.exe -m pyright                     # strict, after every packet -> 0 errors
venv/Scripts/python.exe -m ruff check . && ruff format .   # ruff also formats python fences inside the corpus .md
venv/Scripts/python.exe -m pytest tests/knowledge tests/schemas -q
venv/Scripts/python.exe -m pytest tests/knowledge/test_corpus_expansion.py -s -q   # 26 passed
venv/Scripts/python.exe -m pytest tests/knowledge/test_fusion_eval.py -s -q
venv/Scripts/python.exe -m pytest -q                   # 1416 passed, 2 skipped
# live-collection spot check via create_retriever(settings, client, Tracer.from_settings(settings))
```

### Planner verification script
Re-runnable check over all 30 docs (parse, chunk, headings, metadata, template
`compile()`, link grounding). Kept out of the repo deliberately -- it duplicates
what `tests/knowledge/test_corpus.py` and `test_corpus_expansion.py` now assert
permanently.

## Test Results

### P2 — metadata schema (verified by the planner, 2026-09-26)
```
pyright (strict, app + tests)            0 errors, 0 warnings
ruff check .                             All checks passed
pytest tests/knowledge tests/schemas -q  137 passed, 2 xfailed
load_corpus()                            13 docs -> 78 chunks (unchanged)
EXPECTED_PATTERNS                        30
existing docs, new fields                all default to empty
chunk metadata when fields absent        ['aliases','content_hash','part','section'] (unchanged)
scripts.build_index                      78 points (unchanged)
```
Independently confirmed that `git diff` touches no `uuid5` / `content_hash` /
`KNOWLEDGE_NAMESPACE` line, so existing chunk ids cannot have moved.
The 2 xfails are the corpus-completeness tests awaiting P3.

### P3a / P3b / P3c — authoring (verified by the planner)
Planner-side verification script, run over all 30 docs independently of the
subagents' own reports. For each doc it re-parses the file, re-chunks it, and
checks: `pattern` == file stem, exactly 10 chunks, heading set and order match
the required 10, `pattern_family` / `identification_signals` /
`representative_problems` non-empty, every ```python fence `compile()`s, and
every `leetcode.com` link in the body appears verbatim in `dsa_source.json`.

```
30/30 docs: chunks=10, headings OK, templates compile, links grounded
largest single chunk: 1,046 chars (dfs) -- under MAX_CHUNK_CHARS = 1200
```
The only links not found in the JSON are the two in `segment_tree.md` under the
explicit `**Not in the 6-month plan:**` heading, which is intended.

Pinned chunk ids were **rebaselined** in P3c and independently recomputed by the
planner from `uuid5(KNOWLEDGE_NAMESPACE, f"{source}#{slug_tag(heading)}#{part}")`
-- all four match. The rebaseline was forced by the heading renames
(`When to use` -> `Overview`), which is the point of the packet, not a
regression.

### P4 — index rebuild (verified by the planner against live Qdrant)
```
build 1: 30 docs, 300 chunks, 300 upserted, deleted_stale=39, points_after=300
build 2: 30 docs, 300 chunks, 300 upserted, deleted_stale=0,  points_after=300   # idempotent
GET /collections/dsa_knowledge: points=300, status=green, 384-d Cosine
scroll(limit=400): 30 distinct patterns, every pattern has exactly 10 chunks
  metadata.pattern_family          300/300 points
  metadata.identification_signals  300/300 points
  metadata.representative_problems 300/300 points
  metadata.difficulty              270/300 points  (the 3 docs that omit it by design)
```
`deleted_stale=39` is exactly right: of the original 78 chunks, the three
headings whose slug survived the rename (`Complexity`, `Common Mistakes`,
`Variations`) account for 13x3 = 39 retained ids, so 39 were superseded.

### Full project suite
```
after P3c + P4          ->  1416 passed, 2 skipped, 0 failed  (301s)
after code-review fixes ->  1442 passed, 2 skipped, 0 failed  (244s)
```
Postgres and Qdrant were up, so the `integration`/`db` tests ran. No regression
anywhere in the project. For reference the Phase 05 baseline was 893 passed /
2 failed (two pre-existing auth tests, which now also pass).

### P5 — corpus-expansion retrieval tests
`tests/knowledge/test_corpus_expansion.py` (new). In-memory Qdrant + the real
`bge-small` embedder + real BM25, ingested once per module, mirroring
`test_fusion_eval.py`. Re-run by the planner:
```
pytest tests/knowledge/test_corpus_expansion.py -q   ->  26 passed
pyright                                              ->  0 errors
ruff check / ruff format --check                     ->  clean, 250 files
```

All 11 new-pattern queries rank the intended pattern **first** (top-3 shown):
```
ACTUAL: 'shortest path with negative edges'        -> bellman_ford / dijkstra / bellman_ford
ACTUAL: 'range queries with updates'               -> segment_tree x3
ACTUAL: 'dependency ordering with prerequisites'   -> topological_sort x3
ACTUAL: 'next greater element'                     -> monotonic_stack x3
ACTUAL: 'minimize a value over a search space'     -> binary_search_on_answer, binary_search_on_answer, binary_search
ACTUAL: 'detect a cycle in a linked list'          -> fast_slow_pointers x3
ACTUAL: 'merge overlapping ranges'                 -> intervals x3
ACTUAL: 'prefix search autocomplete dictionary'    -> trie x3
ACTUAL: 'count the set bits using XOR tricks'      -> bit_manipulation x3
ACTUAL: 'connected components in an undirected graph' -> union_find x3
ACTUAL: 'shortest path with weighted edges no negatives' -> dijkstra x3
```
Also covered: winning hits carry non-empty `pattern_family` /
`identification_signals` and the expected `topic`; the reranker ranks the
intended pattern first with `reranked=True`; the original 13 still resolve
(`sliding_window`, `two_pointers`, `hashing`, `backtracking`, `heaps` each at
rank 1); and with Qdrant pointed at a dead port retrieval still returns
BM25-only hits without raising.

**Deliberately not asserted:** rank-1 for graph/DP queries. With the umbrella
`graphs` / `dynamic_programming` docs now competing against the narrower new
docs, "the one right answer" is genuinely ambiguous, so the test asserts the
top-3 for `topological sort of a directed acyclic graph` is a subset of
`{graphs, topological_sort, dfs}` instead. Actual: `topological_sort, dfs,
topological_sort`.

### Live-collection check (planner, against the running container)
Not just in-memory -- the real deployment path, `create_retriever(settings,
client, Tracer.from_settings(settings))` against `dsa_knowledge`:
```
'shortest path with negative edges'      bellman_ford            7.589  (dense, bm25) reranked
'range queries with updates'             segment_tree            8.168  (dense, bm25) reranked
'next greater element'                   monotonic_stack         6.663  (dense, bm25) reranked
'dependency ordering with prerequisites' topological_sort        6.167  (dense, bm25) reranked
'how to shrink a variable-size window'   sliding_window          6.757  (dense, bm25) reranked
'minimize a value over a search space'   binary_search_on_answer 7.658  (dense, bm25) reranked
```
Both retrievers contribute on every query and the reranker is active.

### Fusion evaluation, re-run on the 30-doc corpus
First run, before the code-review fixes:
```
ACTUAL: mrr_dense=0.708 mrr_bm25=0.903 mrr_fused=0.917 hit3_dense=0.917 hit3_bm25=1.000 hit3_fused=1.000
```
That looked like a dense regression (0.854 -> 0.708). It was mostly
**measurement error**: three of the twelve gold labels were written for the
13-doc corpus and now scored the *better* answer as a miss -- `union find
disjoint set` was labelled `graphs` but correctly returns `union_find`,
`cheapest path when edges have costs` returns `dijkstra`, and `binary search on
answer bisect` returns `binary_search_on_answer`. Labels re-pointed, and
`bm25_text` reverted (see Architecture Decisions). Final:
```
ACTUAL: mrr_dense=0.833 mrr_bm25=0.875 mrr_fused=0.958 hit3_dense=0.917 hit3_bm25=0.917 hit3_fused=1.000
```
**`mrr_fused` is back to 0.958 -- identical to the 13-doc Phase 5 baseline --
and `hit3_fused` is still 1.000, now over 30 docs instead of 13.** RRF still
beats either retriever alone, so the Phase 5 invariant holds.

## Known Issues
- **The learner skill map fragments across the 17 new patterns, and
  `pattern_family` is not yet consumed.** `app/agents/planner.py:188` keys the
  profile lookup on `chunk.pattern or chunk.topic`, so a graph question that
  retrieves `union_find` looks up the slug `union_find`, missing a learner whose
  history sits under `graphs`. Checked precisely rather than assumed:
  - `_retrieval_topics` (`app/graph/nodes.py:789`) emits **both**
    `chunk.pattern` and `chunk.topic`, so exposure events *do* still record the
    umbrella key. This is a real mitigation the effect is smaller than a
    pattern-only reading suggests.
  - But the `topic` layer is **inconsistent across the umbrella/child split**:
    `union_find`/`topological_sort`/`dijkstra`/`bellman_ford` are `topic:
    graphs`, yet the pre-existing `bfs`/`dfs` are `topic: graph_traversal`; and
    `dp_1d`/`dp_2d` are `topic: dynamic_programming` while the umbrella
    `dynamic_programming` doc is `topic: optimization`. So the topic fallback
    unifies some of the split but not all of it.
  - `pattern_family` **is** consistent (`graphs` for all seven graph docs,
    `dynamic_programming` for all three DP docs) and nothing reads it.

  **Recommended follow-up**, in Phase 07 rather than here (this feature is
  corpus + ingestion only): aggregate the profile on `pattern_family`, which is
  what it was added for. A cheaper interim fix is to make the `topic` values
  coherent (`bfs`/`dfs` -> `graphs`, `dynamic_programming` -> its own topic),
  but that re-keys existing profile rows, so it is the owner's call.
- **`sliding_window.md` was edited during P3c to keep the fusion eval green.**
  Restructuring its prose moved it to rank 2 for the eval query "monotonic deque
  maximum". The fix was to promote "monotonic deque window maximum" from the
  `Variations` section into `Identification Signals` and the front-matter key.
  The content is genuinely true of the pattern, so this is a real improvement
  rather than a fudge -- but it *was* prompted by a failing test, and it is
  recorded here for that reason.
- **`bellman_ford` cites 3 problems and `binary_search_on_answer` 5**, against a
  6-9 target. The spreadsheets simply do not schedule more problems that
  genuinely use those patterns; padding them with near-misses (e.g. filing a
  Floyd-Warshall problem under Bellman-Ford) would teach the wrong recognition
  cue, so they were left short deliberately.
- **`prefix_sum` has no dedicated problems in the plan.** The `Arrays & Hashing`
  topic contains no `Subarray Sum Equals K`, so its representative problems are
  the closest fits from that topic rather than canonical prefix-sum problems.
- **`segment_tree` has no problem set in the plan at all** (it appears only in
  the `Quick Reference` sheet). It cites the in-plan `Range Sum Query - Immutable`
  as the static contrast plus two canonical problems under an explicit
  `**Not in the 6-month plan:**` heading.
- `ruff format` reformats Python fences inside the corpus markdown; two new docs
  needed a pass. Worth knowing before hand-editing a template.
- **`bellman_ford` vs `dijkstra` is a thin margin** on the live collection
  (7.589 vs 7.397 for "shortest path with negative edges"). Both are correct
  neighbours and the intended doc wins, but the two are close enough in
  embedding space that a reworded query could flip them. Worth a labelled
  regression query if the graph docs are edited again.
- Carried over from Phase 05 and unchanged: no per-document diversity cap, so a
  top-k can still come entirely from one doc. With 30 docs and 10 sections each
  this matters more than it did at 13x6 -- Phase 07 may want a max-per-source
  cap.

## Git Commit
`fd8f373` — feat: expand DSA knowledge corpus to full spreadsheet-based pattern taxonomy

## Next
- Phase 07 should aggregate the learner profile on `pattern_family` (see Known
  Issues); the field exists and is populated on all 300 chunks.
