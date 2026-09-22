# PHASE-05 — Knowledge Base & Hybrid RAG

## Status
Not Started

## Goal
Build the DSA/coding knowledge base and a hybrid retriever (dense + BM25 +
reranker) so agents can ground their teaching in real reference material.

## Scope
- `app/knowledge/ingest.py` — chunk + embed a curated knowledge corpus (DSA
  patterns, algorithms, complexity, Python practices, common-mistake notes) into
  Qdrant; keep a BM25 index alongside.
- `app/knowledge/retrieve.py` — hybrid retrieval: dense (Qdrant) + BM25, fuse
  (RRF), then rerank; return top-k typed `KnowledgeChunk`s with source metadata.
- `app/knowledge/corpus/` — seed content for the patterns listed below.
- `app/graph/` — add a `retrieve_knowledge` node before the agents; populate
  `AgentState.retrieved_context`.
- CLI/script to (re)build the index.

Seed patterns: two pointers, sliding window, prefix sum, hashing, binary search,
DFS, BFS, backtracking, greedy, dynamic programming, graphs, trees, heaps.

## Out of Scope
- Agent logic that consumes the context (Phase 07).
- Sandbox, frontend, evaluation.
- Future phases.

## Current Implementation
Document only what actually exists after inspecting the repository.

## Planned Changes
1. Define `KnowledgeChunk` schema + Qdrant collection config (vector size,
   distance).
2. Implement ingestion (chunking strategy per doc type; store source + topic +
   pattern metadata).
3. Implement dense + BM25 retrieval and RRF fusion; add a reranker pass.
4. Add `retrieve_knowledge` graph node; gate it by intent (skip for pure debug
   of a runtime error).
5. Tests: retrieval returns the right pattern doc for a known query; fusion
   beats either retriever alone on a small fixture set.

## Files Expected
- Create: `app/knowledge/{__init__,ingest,retrieve,index}.py`,
  `app/knowledge/corpus/*.md`, `app/schemas/knowledge.py`,
  `scripts/build_index.py`, `tests/knowledge/test_retrieve.py`
- Modify: `app/graph/{nodes,build}.py`, `app/config.py` (Qdrant collection name)
- Delete: None unless explicitly approved

## Architecture Decisions
- Hybrid over pure-dense: DSA queries are keyword-heavy (pattern names), so BM25
  materially helps; RRF keeps fusion simple and robust.
- Every chunk carries source + pattern metadata so responses can cite what they
  taught from and the planner can target weak patterns.

## Dependencies
- PHASE-01 (Qdrant, embeddings wrapper), PHASE-04 (graph to add the node).

## Implementation Notes
Keep this section short. Record collection name, vector size, chunking params,
reranker model.

## Manual Test Cases
### Test 1
Input: Query "how to shrink a variable-size window".
Expected: Top results include the sliding-window pattern doc with correct
metadata; reranker puts it first.
Actual:

### Test 2
Input: Run the graph on a `DSA_SOLVE` request.
Expected: `retrieved_context` is populated with relevant chunks; a runtime-error
`CODE_DEBUG` request skips retrieval.
Actual:

## Commands Run
```text
# commands
```

## Test Results
-

## Security / Reliability Notes
- Retrieval must fail soft: if Qdrant is down, the graph continues without
  context rather than erroring.
- Corpus is curated and trusted; do not ingest arbitrary user content into the
  shared knowledge collection.

## Known Issues
-

## Git Commit
Not created yet.

## Next Phase
- PHASE-06 — Code Execution Sandbox & Verification.
