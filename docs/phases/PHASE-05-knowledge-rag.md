# PHASE-05 — Knowledge Base & Hybrid RAG

## Status
In Progress. Paused 2026-09-24, nothing committed.
- Done + verified: P1 schema/collection/embedders, P2 corpus, P3 ingestion +
  BM25 + build script (live `dsa_knowledge` = 78 points), P4 hybrid retrieval
  (live + Qdrant-down checks pass).
- **P5 graph node: interrupted mid-packet.** `state.py`, `nodes.py`,
  `build.py`, `config.py` and `main.py` are partially edited, and `api.py` and
  the tests are not done yet. Review `git diff app/graph app/main.py
  app/config.py` before continuing.
- Remaining: finish P5 → P6 tests → close-out (code-review, doc, commit).

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
Inspected 2026-09-24 (HEAD `5301208`; Phases 01–04 + JWT auth Done). Via
token-savior symbol lookups, docker MCP, context7 and the installed packages.

- **Knowledge** — `app/knowledge/__init__.py` exists and is empty. No schema,
  corpus, ingestion, retrieval, BM25 or build script. No `scripts/` code for it.
- **Embeddings** — `LLMClient.embed(texts) -> list[list[float]]` is in the
  Protocol (`app/llm/base.py:60`), but `LangChainLLMClient.embed`
  (`app/llm/client.py:166`) still raises `NotImplementedError`. The providers
  (Groq / OpenRouter chat) have **no embeddings endpoint**.
  `BudgetedLLMClient.embed` counts against the per-run budget
  (`DEFAULT_MAX_LLM_CALLS = 3`). `Tracer` (`app/llm/client.py:36`) wraps calls.
- **Installed libs** — `qdrant-client 1.19.1`, `fastembed 0.8.0` (ONNX, no
  torch needed), `sentence-transformers`, `torch`, `langchain-qdrant`. No
  `rank_bm25`. The fastembed cache (`%TEMP%/fastembed_cache`) already holds
  `BAAI/bge-small-en-v1.5` (384-d, quantized ONNX) and `Qdrant/bm25`. No
  cross-encoder is cached yet. Supported rerankers include
  `Xenova/ms-marco-MiniLM-L-6-v2` (0.08 GB).
- **Settings** (`app/config.py::Settings`) — `qdrant_url`, `qdrant_api_key`,
  `qdrant_timeout=10`. No collection, embedding, reranker or top-k settings.
- **Qdrant client** — an `AsyncQdrantClient` is created in the `create_app()`
  lifespan and stored on `app.state.qdrant`. It is only used by `/health`
  (`ping_qdrant`). **It is not in `GraphContext`** (`llm`, `session`,
  `user_id`, `conversation_id`).
- **Graph (Phase 04)** — `AgentState.retrieved_context: list[str]` (reserved,
  nothing writes or reads it). `AgentStateUpdate` mirrors it. Nodes register
  through `build.py::NODE_FUNCTIONS` and must match `nodes.py::FALLBACKS` (a
  check in `build_graph`). Each node is wrapped by `safe_node`. Edges:
  `… plan_teaching -> route -?-> {dsa_agent|debug_agent|explain_agent|clarify}`.
  `run_graph` is called only by `app/graph/api.py`.
- **Intent** — `Intent` StrEnum (11 values, `app/schemas/intent.py`).
  `CODE_DEBUG` routes to `debug`. `StructuredInput.error` holds a detected
  traceback.
- **Infra (docker MCP)** — Postgres `adaptivecodingagent-postgres-1` is healthy
  on 5433. The compose Qdrant `adaptivecodingagent-qdrant-1` (v1.12.4) is
  **stuck in `created`**. Its port 6333 bind failed because a standalone
  container named `qdrant` (`qdrant/qdrant:latest` = v1.19.0, volume
  `qdrant_storage`, not part of this compose project) holds 6333/6334. The
  app's `QDRANT_URL=http://localhost:6333` (with API key) currently reaches
  that standalone container, which has **0 collections**.
- **Tests** — markers `integration` (Postgres + Qdrant), `db`, `live`.
  `asyncio_mode = "auto"`.

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

**Actually created / modified (additions beyond the list are recorded here):**
- Created: `app/knowledge/{base,index,ingest,retrieve,bm25}.py`,
  `app/knowledge/corpus/*.md` (13), `app/schemas/knowledge.py`,
  `app/llm/embeddings.py`, `scripts/{__init__,build_index}.py`,
  `tests/knowledge/{__init__,test_index,test_corpus,test_chunking,test_bm25,test_ingest,test_retrieve,test_phase5_manual,test_fusion_eval}.py`,
  `tests/graph/{test_retrieve_node,test_state_import_weight,test_knowledge_top_k_default}.py`,
  `tests/llm/test_embeddings.py`, `tests/schemas/test_knowledge.py`.
- Modified: `app/graph/{state,nodes,build,api}.py`, `app/config.py`,
  `app/main.py`, `app/llm/client.py` (`Tracer.run`), `tests/llm/test_client.py`, `app/knowledge/__init__.py`, `app/schemas/__init__.py`,
  `docker-compose.yml`, `tests/conftest.py`,
  `tests/graph/{test_build,test_nodes,test_phase4_manual,test_chat_api}.py`.
- Additions beyond the plan:
  - `app/llm/embeddings.py`: the embedding/reranker provider wrapper (CLAUDE.md
    keeps providers in `app/llm/`), needed because the chat providers have no
    embeddings.
  - `app/knowledge/bm25.py`: typed in-house BM25 (no dependency).
  - `app/main.py` + `app/graph/api.py`: build the retriever at startup and pass
    it to `run_graph`.
  - `docker-compose.yml`: Qdrant image `v1.12.4` → `v1.19.0` (owner decision).
  - `tests/conftest.py`: new settings env vars + `knowledge_enabled=False` in
    `make_settings`.
  - Tests split into one file per module, plus the manual and fusion-eval files.

## Architecture Decisions
- Hybrid over pure-dense: DSA queries are keyword-heavy (pattern names), so BM25
  materially helps; RRF keeps fusion simple and robust.
- Every chunk carries source + pattern metadata so responses can cite what they
  taught from and the planner can target weak patterns.
- **Local embeddings + reranker (fastembed, ONNX).** Groq/OpenRouter have no
  embeddings API. `app/llm/embeddings.py` defines the `Embedder` / `Reranker`
  Protocols plus fastembed implementations. Every call goes through
  `asyncio.to_thread` + `Tracer.trace`, and errors become `EmbeddingError`
  (class name only). These calls are local, with no API cost, so they are
  **outside** the `BudgetedLLMClient` budget. `LLMClient.embed` stays a stub
  (see Known Issues).
- **In-house BM25** (`app/knowledge/bm25.py`, Okapi, k1=1.5, b=0.75,
  Lucene-style non-negative IDF). It adds no dependency and is fully typed for
  pyright strict. `rank_bm25` has no types. The index is built in memory from
  the same corpus chunks at retriever startup, so BM25 works without Qdrant.
- **Fusion then rerank:** dense top 20 + BM25 top 20 → RRF (k=60) → cross-encoder
  rerank of the top 10 → top_k (default 4).
- **Fail soft, degrade to BM25-only (owner decision).** Dense search (query
  embedding + Qdrant) runs under `knowledge_timeout_s`. Any failure there gives
  BM25-only results. A reranker failure keeps the fused order
  (`reranked=False`). Any other failure returns `[]`. Only `CancelledError`
  propagates. Logs hold the exception class name only, never the query.
- **Node placement:** `plan_teaching → retrieve_knowledge → route`. The node
  sits after the planner (the plan topic feeds the query, and clarify plans
  skip retrieval) and before the agents. `route` and its conditional edges are
  unchanged, so the Phase 07 contract holds.
- **Intent gating:** retrieval is skipped for empty input, missing or
  low-confidence intent, clarify plans, and **`CODE_DEBUG` with a traceback
  present** (pure runtime-error debugging). ERROR_EXPLANATION and
  TEST_CASE_ANALYSIS still retrieve.
- **Query = plan topic + question + problem prose. It never includes pasted
  code or error text.** That keeps untrusted blobs out of the query and keeps
  it focused on concepts.
- `AgentState.retrieved_context` changed from `list[str]` to
  `list[RetrievalHit]` (owner decision), so provenance (source, pattern,
  retrievers, reranked) reaches Phase 07.
- **Stable, idempotent ingestion:** point id = `uuid5(KNOWLEDGE_NAMESPACE,
  f"{source}#{section}#{part}")`. Each run upserts every chunk, then deletes
  points whose ids are no longer in the corpus. The collection config is
  validated (size/distance), and a mismatch raises `IndexConfigError`. Use
  `--recreate` to rebuild.
- **Infra (owner decision 1a):** the compose Qdrant is pinned to
  `qdrant/qdrant:v1.19.0` to match `qdrant-client 1.19.1`. The owner stopped the
  unrelated standalone `qdrant` container that had been holding :6333.

## Dependencies
- PHASE-01 (Qdrant, embeddings wrapper), PHASE-04 (graph to add the node).

## Implementation Notes
Keep this section short. Record collection name, vector size, chunking params,
reranker model.

- **Collection:** `dsa_knowledge` (`KNOWLEDGE_COLLECTION`), a single unnamed
  vector, **384-d, Cosine**. 13 docs → **78 points** (6 per doc).
- **Embedding model:** `BAAI/bge-small-en-v1.5` (fastembed; passages via
  `passage_embed`, queries via `query_embed`).
- **Reranker:** `Xenova/ms-marco-MiniLM-L-6-v2` (fastembed `TextCrossEncoder`).
- **Chunking:** one chunk per `## ` section (headings inside ``` fences are
  ignored). Text = `"{title} — {heading}\n\n{body}"`, max 1,200 chars. Longer
  sections split on paragraphs (a fenced block is never split) with a
  one-paragraph overlap. The longest current chunk is 1,038 chars.
- **Corpus format:** `app/knowledge/corpus/<pattern>.md`, front matter `title,
  pattern, topic, aliases`, then an H1 and six sections (When to use /
  Recognition signals / Template / Complexity / Common mistakes / Variations).
  BM25 indexes text + title + aliases + pattern words.
- **Retrieval params:** `RRF_K=60`, `DENSE_LIMIT=20`, `BM25_LIMIT=20`,
  `RERANK_CANDIDATES=10`, `MAX_QUERY_CHARS=1000`, `knowledge_top_k=4`,
  `knowledge_timeout_s=3.0`.
- **Rebuild:** `venv/Scripts/python.exe -m scripts.build_index [--recreate]`.
- **Phase 07 contract:** read `state.retrieved_context: list[RetrievalHit]`
  (`hit.chunk.{text,source,pattern,topic,heading}`, `hit.score`,
  `hit.retrievers`, `hit.reranked`). It is empty when retrieval was skipped or
  unavailable.

## Manual Test Cases
### Test 1
Input: Query "how to shrink a variable-size window".
Expected: Top results include the sliding-window pattern doc with correct
metadata; reranker puts it first.
Actual: live Qdrant `dsa_knowledge` + real embedder and reranker
(`tests/knowledge/test_phase5_manual.py`, `integration`):
```
ACTUAL: [('sliding_window', 'Common mistakes', 7.13, ('dense', 'bm25'), True), ('sliding_window', 'When to use', 6.01, ('dense', 'bm25'), True), ('sliding_window', 'Variations', 5.07, ('dense', 'bm25'), True), ('sliding_window', 'Template', 4.17, ('dense', 'bm25'), True)]
```
The first hit is `sliding_window` (topic `arrays`, source
`app/knowledge/corpus/sliding_window.md`), reranked. Both retrievers found it.
**Pass.**

### Test 2
Input: Run the graph on a `DSA_SOLVE` request.
Expected: `retrieved_context` is populated with relevant chunks; a runtime-error
`CODE_DEBUG` request skips retrieval.
Actual: `run_graph` with the real retriever (live Qdrant) and the Phase 4 fake
LLM (0 LLM calls, rule-classified):
```
ACTUAL: route='dsa' intent=DSA_SOLVE n_hits=4 patterns=['sliding_window', 'sliding_window', 'sliding_window', 'sliding_window']
ACTUAL: route='debug' intent=CODE_DEBUG retrieved_context=[]
ACTUAL: route='dsa' errors=[] n_hits=4 retrievers=[('bm25',), ('bm25',), ('bm25',), ('bm25',)]
```
(a) `DSA_SOLVE` ("longest substring without repeating characters") fills
`retrieved_context` with sliding-window chunks. (b) A code + `IndexError`
traceback `CODE_DEBUG` request skips retrieval. (c) The same DSA request with
Qdrant unreachable completes with no `NodeError` and BM25-only hits
(fail-soft). **Pass.**

### Fusion evaluation (Planned Change 5)
12 labeled queries (keyword and paraphrase), real bge-small + BM25, in-memory
Qdrant, pattern-level MRR@5 and hit@3, no reranker
(`tests/knowledge/test_fusion_eval.py`):
```
ACTUAL: mrr_dense=0.854 mrr_bm25=0.833 mrr_fused=0.958 hit3_dense=0.917 hit3_bm25=0.917 hit3_fused=1.000
```
RRF beats either retriever alone on both metrics. **Pass.**

## Commands Run
```text
docker MCP: list_containers / pull_image qdrant/qdrant:v1.19.0 / fetch_container_logs   # infra inspection
docker stop qdrant; docker compose up -d qdrant                                  # run by the owner (standalone container stop was denied to the agent)
venv/Scripts/python.exe -c "<fastembed list_supported_models / qdrant get_collections>"   # model dims + API check
venv/Scripts/python.exe -m scripts.build_index        # x2: created=true then false, 78 points both times
venv/Scripts/python.exe -m pyright                    # strict, after every packet
venv/Scripts/python.exe -m ruff check . && venv/Scripts/python.exe -m ruff format --check .
venv/Scripts/python.exe -m pytest tests/knowledge tests/graph tests/llm tests/schemas -q
venv/Scripts/python.exe -m pytest tests/knowledge/test_phase5_manual.py tests/knowledge/test_fusion_eval.py -s -q
venv/Scripts/python.exe -m pytest -q
```

## Test Results
- `pytest -q`: **893 passed, 2 skipped, 2 failed**. The 2 failures are
  pre-existing auth tests (`MultipleResultsFound`, see Known Issues); they also
  fail at `5301208`. Postgres + Qdrant were up, so the `integration`/`db` tests
  ran. Phase 05 added 278 tests (baseline was 615 passed at Phase 04 plus the
  auth tests).
- The manual Test 1, Test 2 (a/b/c) and the fusion eval pass against the live
  `dsa_knowledge` collection. The ACTUAL lines are above, and they were
  unchanged after the code-review fixes.
- Live index: Qdrant 1.19.0, collection `green`, 78 points, 384-d Cosine. Every
  payload has `source`/`topic`/`pattern`. `build_index` run twice → `created`
  true then false, 78 points both times (idempotent).
- `pyright` (strict, app + tests): 0 errors. `ruff check` / `format --check`:
  clean.
- `code-review` (high): 10 findings. Fixed, each with a regression test:
  1. the retrieval timeout was per stage (about 2×) and timed-out ONNX threads
     sat on the shared executor → one deadline for the whole call + a
     dedicated 2-worker `fastembed` executor;
  2. startup could hang on a model download →
     `knowledge_startup_timeout_s` (60s) → BM25-less startup with
     `retriever=None`;
  3. `CorpusError` was swallowed at startup → re-raised (fail loudly);
  4. embed/rerank calls were not really traced → new `Tracer.run` opens a real
     LangSmith run (inputs are counts + model name only, never text);
  5. `app.graph.state` imported fastembed/onnxruntime → `Retriever` Protocol +
     `DEFAULT_KNOWLEDGE_TOP_K` moved to lightweight `app/knowledge/base.py`,
     and the package `__init__` no longer imports eagerly (subprocess test);
  6. an H1-only corpus doc silently produced 0 chunks → `CorpusError`;
  7. `should_retrieve` duplicated the clarify checks → reuses `select_route`;
  8. the `top_k` default of 4 was in 3 places → one constant;
  9. repeated query tokens over-weighted BM25 → query tokens and query parts
     are deduped.
  Rejected: (10) "`EXPECTED_PATTERNS` unused". `tests/knowledge/test_corpus.py`
  uses it to enforce corpus completeness.

## Security / Reliability Notes
- Retrieval must fail soft: if Qdrant is down, the graph continues without
  context rather than erroring.
- Corpus is curated and trusted; do not ingest arbitrary user content into the
  shared knowledge collection.

## Known Issues
- `LLMClient.embed` / `LangChainLLMClient.embed` still raise
  `NotImplementedError`. Retrieval uses the dedicated `Embedder`
  (`app/llm/embeddings.py`) instead. Remove or delegate the Protocol method in
  a later cleanup.
- The compose Qdrant sets no `QDRANT__SERVICE__API_KEY`, so the API key the app
  sends is ignored locally. It is plain HTTP on localhost. Configure a key
  before any shared deployment.
- `.env.example` does not list the new knowledge settings (the project deny
  rule blocks agent access to `.env.*`). The owner should add:
  `KNOWLEDGE_COLLECTION`, `EMBEDDING_MODEL`, `EMBEDDING_DIM`, `RERANKER_MODEL`,
  `FASTEMBED_CACHE_DIR`, `KNOWLEDGE_TOP_K`, `KNOWLEDGE_TIMEOUT_S`,
  `KNOWLEDGE_ENABLED`.
- No per-document diversity cap: the top-k can all come from one pattern doc
  (e.g. 4 sliding-window sections). That is fine for teaching one pattern.
  Phase 07 may want a max-per-source cap.
- Models download from Hugging Face on first use into fastembed's default cache
  (`%TEMP%/fastembed_cache`, which the OS may clear). Set `FASTEMBED_CACHE_DIR`
  for a persistent cache. With no network and no cache, the retriever starts
  BM25-only (embedder/reranker are `None`).
- The embedder and reranker load at app startup (a few seconds). Tests disable
  this via `knowledge_enabled=False` in `make_settings`.
- Python cannot cancel a running thread: an ONNX call that exceeds the
  deadline keeps running on the dedicated 2-worker `fastembed` executor. Under
  sustained overload, retrieval degrades to BM25 while those threads drain.
  Other `to_thread` users are unaffected.
- `ingest_corpus` does not roll back if a later batch fails after earlier
  batches were upserted. A re-run converges; the corpus is 78 chunks, a single
  batch of 32 × 3.
- Pre-existing, outside Phase 5: 2 auth tests
  (`tests/auth/test_refresh_tokens_db.py::test_create_refresh_token_never_stores_raw_token`,
  `tests/auth/test_routes.py::test_login_persists_hashed_refresh_token_without_raw_values`)
  fail with `MultipleResultsFound` against a dev DB that has real refresh
  tokens. They select every `RefreshToken` row without filtering by user. They
  also fail on the unmodified tree at `5301208`.

## Git Commit
Not created yet.

## Next Phase
- PHASE-06 — Code Execution Sandbox & Verification.
