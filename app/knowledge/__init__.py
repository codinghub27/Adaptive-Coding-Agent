"""Knowledge RAG: corpus indexing and retrieval (Qdrant + BM25 + reranker).

Deliberately does not import `app.knowledge.retrieve`/`.ingest`/`.index` at
package-import time -- those pull in `qdrant_client`/`fastembed`, and modules
that only need the lightweight `Retriever` contract (e.g. `app.graph.state`)
import it from `app.knowledge.base` instead. Import the submodules directly
(`app.knowledge.retrieve`, `app.knowledge.ingest`, `app.knowledge.index`) for
their heavier functionality.
"""
