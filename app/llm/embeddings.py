"""Local embedding and reranking model wrappers.

Wraps fastembed's local ONNX models behind the `Embedder`/`Reranker`
protocols so retrieval code never depends on the `fastembed` package
directly. Unlike `LLMClient.chat`/`.vision`, these calls run a local model
with no network egress and no per-call cost, so they are deliberately kept
outside `BudgetedLLMClient`'s call budget -- retrieval can embed/rerank as
often as it needs to within a single graph run.

fastembed's model construction and inference are synchronous/CPU-bound, so
every blocking call here is dispatched off the event loop. Model
*construction* (`load_embedder`/`load_reranker`) still uses `asyncio.to_thread`
-- it only runs once, at startup. Model *inference* (`embed_passages`/
`embed_query`/`rerank`) instead runs on `_EXECUTOR`, a small, dedicated
`ThreadPoolExecutor` -- never `asyncio.to_thread`'s shared default executor --
so that a slow or timed-out ONNX call can only ever back up this pool's two
workers, never every other `asyncio.to_thread` caller in the process. Python
cannot cancel a running thread: a caller that times out (e.g. via
`asyncio.wait_for`) abandons the inference call rather than killing it: it
keeps running in the background, occupying one of the two workers until it
finishes, even though its result is discarded.

Each inference call is also wrapped in a real, standalone LangSmith run via
`Tracer.run` (unlike `Tracer.trace`, which only opens ambient tracing context
around a LangChain-produced run -- fastembed emits no such run of its own).
"""

import asyncio
import concurrent.futures
from collections.abc import Callable, Mapping, Sequence
from typing import Final, Protocol, TypeVar, cast, runtime_checkable

from fastembed import TextEmbedding
from fastembed.rerank.cross_encoder import (  # pyright: ignore[reportMissingTypeStubs]
    TextCrossEncoder,
)

from app.config import Settings
from app.llm.base import LLMError
from app.llm.client import Tracer

__all__ = [
    "Embedder",
    "EmbeddingConfigError",
    "EmbeddingError",
    "FastEmbedEmbedder",
    "FastEmbedReranker",
    "Reranker",
    "create_embedder",
    "create_reranker",
    "load_embedder",
    "load_reranker",
]

_T = TypeVar("_T")

#: Dedicated thread pool for fastembed model inference -- bounded and
#: separate from `asyncio.to_thread`'s shared default executor. `max_workers`
#: is deliberately small: this only ever runs (at most) one embed and one
#: rerank call concurrently per retrieval, and a larger pool would just let
#: more abandoned, timed-out inference calls pile up in the background.
_EXECUTOR: Final = concurrent.futures.ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="fastembed"
)


class EmbeddingConfigError(ValueError):
    """Raised when an embedding/reranker model is misconfigured.

    E.g. an unsupported model name, or one whose published dimensionality
    doesn't match the configured `embedding_dim`.
    """


class EmbeddingError(LLMError):
    """Wraps a local embedding/reranker model failure without leaking details.

    The message must only ever contain the failing exception's class name,
    per project convention -- never the exception's own message.
    """


@runtime_checkable
class Embedder(Protocol):
    """Provider-agnostic interface for a local dense-embedding model."""

    @property
    def dim(self) -> int: ...

    async def embed_passages(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


@runtime_checkable
class Reranker(Protocol):
    """Provider-agnostic interface for a local cross-encoder reranker."""

    async def rerank(self, query: str, documents: Sequence[str]) -> list[float]: ...


def _supported_embedding_dim(model_name: str) -> int | None:
    """Look up `model_name`'s published dimensionality, or None if unsupported."""
    supported = cast(
        "list[dict[str, object]]",
        TextEmbedding.list_supported_models(),  # pyright: ignore[reportUnknownMemberType]
    )
    for entry in supported:
        if entry.get("model") == model_name:
            dim = entry.get("dim")
            return dim if isinstance(dim, int) else None
    return None


async def _run_traced(
    tracer: Tracer,
    op: str,
    run_type: str,
    inputs: Mapping[str, object],
    fn: Callable[[], _T],
    outputs: Callable[[_T], Mapping[str, object]],
) -> _T:
    """Run blocking `fn` on the dedicated fastembed executor, inside a real
    traced LangSmith run, error-wrapped.

    `inputs` must only ever hold counts/model names -- never the raw
    text/documents being embedded or reranked, which is untrusted user data.
    """

    async def _call() -> _T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_EXECUTOR, fn)

    try:
        return await tracer.run(op, run_type, inputs, _call, outputs=outputs)
    except Exception as exc:
        raise EmbeddingError(f"{op} call failed: {type(exc).__name__}") from None


class FastEmbedEmbedder:
    """`Embedder` implementation backed by a local fastembed `TextEmbedding` model.

    Validates at construction that `model_name` is a fastembed-supported
    model and that its published dimensionality matches `expected_dim`,
    rather than failing lazily on the first embed call.
    """

    def __init__(
        self,
        model_name: str,
        *,
        cache_dir: str | None,
        tracer: Tracer,
        expected_dim: int,
    ) -> None:
        actual_dim = _supported_embedding_dim(model_name)
        if actual_dim is None:
            raise EmbeddingConfigError(f"unsupported embedding model: {model_name!r}")
        if actual_dim != expected_dim:
            raise EmbeddingConfigError(
                f"embedding model {model_name!r} has dim {actual_dim}, expected {expected_dim}"
            )

        self._model = TextEmbedding(model_name=model_name, cache_dir=cache_dir)
        self._model_name = model_name
        self._dim = expected_dim
        self._tracer = tracer

    @property
    def dim(self) -> int:
        return self._dim

    def _embed_passages_sync(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.passage_embed(texts)  # pyright: ignore[reportUnknownMemberType]
        return [vector.tolist() for vector in vectors]  # pyright: ignore[reportUnknownVariableType]

    def _embed_query_sync(self, text: str) -> list[float]:
        vectors = list(self._model.query_embed(text))  # pyright: ignore[reportUnknownMemberType]
        return vectors[0].tolist()  # pyright: ignore[reportUnknownVariableType]

    async def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        texts_list = list(texts)
        return await _run_traced(
            self._tracer,
            "embedding.embed_passages",
            "embedding",
            {"model": self._model_name, "n_texts": len(texts_list)},
            lambda: self._embed_passages_sync(texts_list),
            lambda result: {"n_vectors": len(result), "dim": self._dim},
        )

    async def embed_query(self, text: str) -> list[float]:
        return await _run_traced(
            self._tracer,
            "embedding.embed_query",
            "embedding",
            {"model": self._model_name, "n_texts": 1},
            lambda: self._embed_query_sync(text),
            lambda _result: {"n_vectors": 1, "dim": self._dim},
        )


class FastEmbedReranker:
    """`Reranker` implementation backed by a local fastembed cross-encoder model."""

    def __init__(
        self,
        model_name: str,
        *,
        cache_dir: str | None,
        tracer: Tracer,
    ) -> None:
        self._model = TextCrossEncoder(model_name=model_name, cache_dir=cache_dir)
        self._model_name = model_name
        self._tracer = tracer

    def _rerank_sync(self, query: str, documents: list[str]) -> list[float]:
        scores = self._model.rerank(query, documents)  # pyright: ignore[reportUnknownMemberType]
        return [float(score) for score in scores]  # pyright: ignore[reportUnknownVariableType]

    async def rerank(self, query: str, documents: Sequence[str]) -> list[float]:
        if not documents:
            return []
        documents_list = list(documents)
        return await _run_traced(
            self._tracer,
            "rerank",
            "retriever",
            {"model": self._model_name, "n_texts": len(documents_list)},
            lambda: self._rerank_sync(query, documents_list),
            lambda result: {"n_scores": len(result)},
        )


def create_embedder(settings: Settings, tracer: Tracer) -> FastEmbedEmbedder:
    """Build the configured `FastEmbedEmbedder`.

    Blocking: constructs (and, on first use, downloads) the underlying ONNX
    model, so it must not be called directly from an async context -- use
    `load_embedder` there instead.
    """
    return FastEmbedEmbedder(
        settings.embedding_model,
        cache_dir=settings.fastembed_cache_dir,
        tracer=tracer,
        expected_dim=settings.embedding_dim,
    )


def create_reranker(settings: Settings, tracer: Tracer) -> FastEmbedReranker:
    """Build the configured `FastEmbedReranker`.

    Blocking: constructs (and, on first use, downloads) the underlying ONNX
    model, so it must not be called directly from an async context -- use
    `load_reranker` there instead.
    """
    return FastEmbedReranker(
        settings.reranker_model,
        cache_dir=settings.fastembed_cache_dir,
        tracer=tracer,
    )


async def load_embedder(settings: Settings, tracer: Tracer) -> FastEmbedEmbedder:
    """Construct the configured `FastEmbedEmbedder` off the event loop."""
    return await asyncio.to_thread(create_embedder, settings, tracer)


async def load_reranker(settings: Settings, tracer: Tracer) -> FastEmbedReranker:
    """Construct the configured `FastEmbedReranker` off the event loop."""
    return await asyncio.to_thread(create_reranker, settings, tracer)
