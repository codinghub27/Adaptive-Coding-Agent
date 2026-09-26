"""Corpus loading, chunking, and Qdrant ingestion for the knowledge RAG
pipeline.

`load_corpus` reads the curated DSA pattern markdown files in
`app/knowledge/corpus/` and parses each into a `CorpusDocument`.
`chunk_corpus`/`chunk_document` split each document's body into retrievable
`KnowledgeChunk`s, and `ingest_corpus`/`build_index` embed those chunks and
upsert them into Qdrant. Retrieval (dense + BM25 fusion, rerank) is a later
step of Phase 5.
"""

import hashlib
import re
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

from qdrant_client import AsyncQdrantClient, models

from app.config import Settings
from app.knowledge.bm25 import BM25Index
from app.knowledge.index import ensure_collection
from app.llm.client import Tracer
from app.llm.embeddings import Embedder, load_embedder
from app.schemas.base import APIModel
from app.schemas.event import slug_tag
from app.schemas.knowledge import CorpusDocument, KnowledgeChunk

__all__ = [
    "CORPUS_DIR",
    "EXPECTED_PATTERNS",
    "KNOWLEDGE_NAMESPACE",
    "MAX_CHUNK_CHARS",
    "CorpusError",
    "IngestReport",
    "bm25_text",
    "build_bm25",
    "build_index",
    "chunk_corpus",
    "chunk_document",
    "chunk_from_payload",
    "chunk_to_payload",
    "ingest_corpus",
    "load_corpus",
    "parse_document",
]

CORPUS_DIR: Final[Path] = Path(__file__).parent / "corpus"

#: Maximum length (in characters, including the `title — heading` prefix) of
#: a single chunk's text before it is split into overlapping parts.
MAX_CHUNK_CHARS: Final = 1200

#: Fixed namespace UUID for deterministic chunk id generation via `uuid5`.
#: A literal, not derived from anything: keeping it stable across process
#: runs is what makes chunk ids (and therefore re-ingestion) idempotent.
KNOWLEDGE_NAMESPACE: Final = uuid.UUID("7c6e2a1e-3b7a-4b8a-9b0a-1f2e3d4c5b6a")

#: Repo root, used to render `CorpusDocument.source` as a repo-relative POSIX
#: path for the default corpus directory (three levels up from
#: app/knowledge/corpus).
_REPO_ROOT: Final[Path] = Path(__file__).parent.parent.parent

_FRONT_MATTER_DELIMITER: Final[str] = "---"
_REQUIRED_KEYS: Final[frozenset[str]] = frozenset({"title", "pattern", "topic", "aliases"})
_OPTIONAL_KEYS: Final[frozenset[str]] = frozenset(
    {"pattern_family", "difficulty", "representative_problems", "identification_signals"}
)
_EXPECTED_KEYS: Final[frozenset[str]] = _REQUIRED_KEYS | _OPTIONAL_KEYS
_H1_PATTERN: Final[re.Pattern[str]] = re.compile(r"^# ", flags=re.MULTILINE)

EXPECTED_PATTERNS: Final[frozenset[str]] = frozenset(
    {
        "two_pointers",
        "sliding_window",
        "prefix_sum",
        "hashing",
        "binary_search",
        "dfs",
        "bfs",
        "backtracking",
        "greedy",
        "dynamic_programming",
        "graphs",
        "trees",
        "heaps",
        "fast_slow_pointers",
        "monotonic_stack",
        "stack",
        "binary_search_on_answer",
        "linked_list",
        "union_find",
        "topological_sort",
        "dijkstra",
        "bellman_ford",
        "dp_1d",
        "dp_2d",
        "intervals",
        "bit_manipulation",
        "trie",
        "divide_and_conquer",
        "math_geometry",
        "segment_tree",
    }
)


class CorpusError(ValueError):
    """Raised when a corpus markdown file is malformed or the corpus as a
    whole is inconsistent (duplicate/mismatched pattern)."""


def parse_document(text: str, *, source: str) -> CorpusDocument:
    """Parse one corpus markdown file's text into a `CorpusDocument`.

    Expects a `---`-delimited front matter block with exactly the keys
    `title, pattern, topic, aliases`, followed by a non-empty markdown body
    containing a top-level `# ` heading. Raises `CorpusError`, naming
    `source`, for any structural problem. Does not check that `pattern`
    matches the file stem -- that check belongs to `load_corpus`, which
    knows the file name.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FRONT_MATTER_DELIMITER:
        raise CorpusError(f"{source}: missing front matter (must start with '---')")

    try:
        end_index = next(
            i for i in range(1, len(lines)) if lines[i].strip() == _FRONT_MATTER_DELIMITER
        )
    except StopIteration as exc:
        raise CorpusError(f"{source}: unterminated front matter (missing closing '---')") from exc

    raw: dict[str, str] = {}
    for line in lines[1:end_index]:
        if not line.strip():
            continue
        if ":" not in line:
            raise CorpusError(f"{source}: malformed front-matter line: {line!r}")
        key, _, value = line.partition(":")
        key = key.strip()
        if key not in _EXPECTED_KEYS:
            raise CorpusError(f"{source}: unknown front-matter key {key!r}")
        raw[key] = value.strip()

    missing = _REQUIRED_KEYS - raw.keys()
    if missing:
        raise CorpusError(f"{source}: missing front-matter key(s): {sorted(missing)}")

    body = "\n".join(lines[end_index + 1 :]).strip()
    if not body:
        raise CorpusError(f"{source}: empty body after front matter")

    if not _H1_PATTERN.search(body):
        raise CorpusError(f"{source}: body has no top-level '# ' heading")

    aliases = tuple(alias.strip() for alias in raw["aliases"].split(",") if alias.strip())
    identification_signals = tuple(
        signal.strip()
        for signal in raw.get("identification_signals", "").split(",")
        if signal.strip()
    )
    representative_problems = tuple(
        problem.strip()
        for problem in raw.get("representative_problems", "").split(";")
        if problem.strip()
    )

    return CorpusDocument(
        source=source,
        title=raw["title"],
        pattern=raw["pattern"],
        topic=raw["topic"],
        aliases=aliases,
        body=body,
        pattern_family=raw.get("pattern_family", ""),
        difficulty=raw.get("difficulty", ""),
        representative_problems=representative_problems,
        identification_signals=identification_signals,
    )


def load_corpus(corpus_dir: Path = CORPUS_DIR) -> list[CorpusDocument]:
    """Load and parse every `*.md` file in `corpus_dir`, sorted by name.

    Raises `CorpusError` if any file fails to parse, if a file's `pattern`
    doesn't match its own file stem, or if two files declare the same
    pattern.
    """
    documents: list[CorpusDocument] = []
    seen_patterns: dict[str, str] = {}

    for md_file in sorted(corpus_dir.glob("*.md")):
        try:
            source = md_file.relative_to(_REPO_ROOT).as_posix()
        except ValueError:
            source = md_file.as_posix()

        text = md_file.read_text(encoding="utf-8")
        document = parse_document(text, source=source)

        if document.pattern != md_file.stem:
            raise CorpusError(
                f"{source}: pattern {document.pattern!r} does not match file stem {md_file.stem!r}"
            )
        if document.pattern in seen_patterns:
            raise CorpusError(
                f"{source}: duplicate pattern {document.pattern!r} "
                f"(already defined in {seen_patterns[document.pattern]})"
            )

        seen_patterns[document.pattern] = source
        documents.append(document)

    return documents


def _split_sections(body: str) -> list[tuple[str, str]]:
    """Split a corpus doc body into `(heading, section_text)` pairs.

    Splits on lines starting with `## `, ignoring such lines when they occur
    inside a ``` fenced code block. Drops the top-level `# ` heading line; any
    other preamble text before the first `## ` section is prepended to the
    first section's body.
    """
    lines = body.splitlines()
    in_fence = False
    h1_seen = False
    preamble: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    current_heading: str | None = None
    current_lines: list[str] = []

    for line in lines:
        if line.strip().startswith("```"):
            in_fence = not in_fence
            (current_lines if current_heading is not None else preamble).append(line)
            continue

        if not in_fence and not h1_seen and line.startswith("# ") and not line.startswith("## "):
            h1_seen = True
            continue

        if not in_fence and line.startswith("## "):
            if current_heading is not None:
                sections.append((current_heading, current_lines))
            current_heading = line[3:].strip()
            current_lines = []
            continue

        (current_lines if current_heading is not None else preamble).append(line)

    if current_heading is not None:
        sections.append((current_heading, current_lines))

    preamble_text = "\n".join(preamble).strip()
    if preamble_text and sections:
        heading0, lines0 = sections[0]
        sections[0] = (heading0, [preamble_text, ""] + lines0)

    return [(heading, "\n".join(section_lines)) for heading, section_lines in sections]


def _split_paragraphs(text: str) -> list[str]:
    """Split `text` into paragraphs on blank lines, treating a whole ```
    fenced code block as one indivisible paragraph."""
    lines = text.splitlines()
    paragraphs: list[str] = []
    current: list[str] = []
    in_fence = False

    for line in lines:
        if line.strip().startswith("```"):
            in_fence = not in_fence
            current.append(line)
            if not in_fence:
                paragraphs.append("\n".join(current))
                current = []
            continue

        if in_fence:
            current.append(line)
            continue

        if line.strip() == "":
            if current:
                paragraphs.append("\n".join(current))
                current = []
            continue

        current.append(line)

    if current:
        paragraphs.append("\n".join(current))

    return [paragraph for paragraph in paragraphs if paragraph.strip()]


def _pack_paragraphs(paragraphs: list[str], prefix: str) -> list[str]:
    """Greedily pack `paragraphs` into parts of at most `MAX_CHUNK_CHARS`
    characters (including `prefix`), with a one-paragraph overlap between
    consecutive parts. A single paragraph that alone exceeds the limit
    becomes its own part."""
    parts: list[str] = []
    i = 0
    n = len(paragraphs)

    while i < n:
        current: list[str] = []
        current_len = 0
        j = i
        while j < n:
            paragraph = paragraphs[j]
            joiner_len = 2 if current else 0  # "\n\n" between paragraphs
            candidate_len = len(prefix) + current_len + joiner_len + len(paragraph)
            if current and candidate_len > MAX_CHUNK_CHARS:
                break
            current.append(paragraph)
            current_len += joiner_len + len(paragraph)
            j += 1

        if not current:
            current = [paragraphs[i]]
            j = i + 1

        parts.append("\n\n".join(current))

        if j >= n:
            break
        # One-paragraph overlap: only possible (and only makes progress
        # without looping forever) when this part held more than one
        # paragraph, so the next part still starts strictly after `i`.
        i = j - 1 if len(current) > 1 else j

    return parts


def chunk_document(doc: CorpusDocument) -> list[KnowledgeChunk]:
    """Split one `CorpusDocument`'s body into `KnowledgeChunk`s, one per
    `## ` section (further split into overlapping parts if a section's text
    exceeds `MAX_CHUNK_CHARS`). Pure and deterministic: chunk ids depend only
    on `doc.source`, the section heading, and the part index."""
    chunks: list[KnowledgeChunk] = []

    for heading, section_body in _split_sections(doc.body):
        section_text = section_body.strip()
        prefix = f"{doc.title} — {heading}\n\n"
        full_text = f"{prefix}{section_text}"

        if len(full_text) <= MAX_CHUNK_CHARS:
            parts = [section_text]
        else:
            paragraphs = _split_paragraphs(section_text)
            parts = _pack_paragraphs(paragraphs, prefix)

        for part_index, part_body in enumerate(parts):
            text = f"{prefix}{part_body}"
            chunk_id = str(
                uuid.uuid5(KNOWLEDGE_NAMESPACE, f"{doc.source}#{slug_tag(heading)}#{part_index}")
            )
            metadata = {
                "section": slug_tag(heading),
                "part": str(part_index),
                "aliases": ", ".join(doc.aliases),
                "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
            }
            if doc.pattern_family:
                metadata["pattern_family"] = doc.pattern_family
            if doc.difficulty:
                metadata["difficulty"] = doc.difficulty
            if doc.identification_signals:
                metadata["identification_signals"] = ", ".join(doc.identification_signals)
            if doc.representative_problems:
                metadata["representative_problems"] = " ; ".join(doc.representative_problems)
            chunks.append(
                KnowledgeChunk(
                    id=chunk_id,
                    text=text,
                    source=doc.source,
                    title=doc.title,
                    heading=heading,
                    topic=doc.topic,
                    pattern=doc.pattern,
                    metadata=metadata,
                )
            )

    return chunks


def chunk_corpus(docs: Sequence[CorpusDocument]) -> list[KnowledgeChunk]:
    """Chunk every document in `docs`. Raises `CorpusError` on a duplicate
    chunk id (would indicate two documents/sections colliding after
    normalization), or if any single document yields zero chunks (a doc
    whose body has no `## ` section -- e.g. only a top-level `# ` heading --
    would otherwise silently vanish from the corpus instead of failing loudly)."""
    chunks: list[KnowledgeChunk] = []
    seen_ids: set[str] = set()

    for doc in docs:
        doc_chunks = chunk_document(doc)
        if not doc_chunks:
            raise CorpusError(f"{doc.source}: produced zero chunks (no '## ' sections found)")
        for chunk in doc_chunks:
            if chunk.id in seen_ids:
                raise CorpusError(f"duplicate chunk id {chunk.id!r} (source: {chunk.source})")
            seen_ids.add(chunk.id)
            chunks.append(chunk)

    return chunks


def chunk_to_payload(chunk: KnowledgeChunk) -> dict[str, object]:
    """Serialize a chunk to a JSON-compatible Qdrant point payload."""
    return chunk.model_dump(mode="json")


def chunk_from_payload(payload: Mapping[str, object]) -> KnowledgeChunk:
    """Deserialize a Qdrant point payload back into a `KnowledgeChunk`."""
    return KnowledgeChunk.model_validate(payload)


def bm25_text(chunk: KnowledgeChunk) -> str:
    """The text BM25 indexes for `chunk`: its body plus title/aliases/pattern
    so exact-keyword matches on those also count for the sparse side of
    retrieval.

    `identification_signals` and `pattern_family` are deliberately NOT folded
    in. They are document-level strings copied onto all ten of a doc's chunks,
    and the signals already appear verbatim in that doc's `## Identification
    Signals` body section, so indexing them again is a constant per-doc boost
    that cannot discriminate between sections. Measured over the corpus it left
    BM25 accuracy unchanged (15/15 hit@1, MRR 1.000 either way) while shrinking
    the distinct patterns in BM25's top-4 from 1.27 to 1.13 and the fused top-10
    handed to the reranker from 3.00 to 2.78 -- a narrower candidate pool for no
    gain. The metadata is still carried on every chunk for consumers.
    """
    aliases = chunk.metadata.get("aliases", "")
    return f"{chunk.text}\n{chunk.title}\n{aliases}\n{chunk.pattern.replace('_', ' ')}"


def build_bm25(chunks: Sequence[KnowledgeChunk]) -> BM25Index:
    """Build a `BM25Index` over `chunks`, keyed by chunk id."""
    return BM25Index.build([(chunk.id, bm25_text(chunk)) for chunk in chunks])


class IngestReport(APIModel):
    """Summary of one `ingest_corpus`/`build_index` run."""

    collection: str
    documents: int
    chunks: int
    upserted: int
    deleted_stale: int
    created: bool
    points_after: int


async def ingest_corpus(
    client: AsyncQdrantClient,
    embedder: Embedder,
    *,
    collection: str,
    chunks: Sequence[KnowledgeChunk],
    documents: int,
    recreate: bool = False,
    batch_size: int = 32,
) -> IngestReport:
    """Embed `chunks` and upsert them into `collection`, then delete any
    points in the collection whose id is not in `chunks` (stale cleanup),
    making repeated runs against the same corpus idempotent.

    Security: this must only ever be called with the curated corpus loaded
    via `load_corpus`/`chunk_corpus` -- never wired to user-provided text or
    documents, since it writes directly into the knowledge index every
    learner's retrieval draws from.
    """
    created = await ensure_collection(client, collection, embedder.dim, recreate=recreate)

    upserted = 0
    for start in range(0, len(chunks), batch_size):
        batch = list(chunks[start : start + batch_size])
        vectors = await embedder.embed_passages([chunk.text for chunk in batch])
        for vector in vectors:
            if len(vector) != embedder.dim:
                raise ValueError(
                    f"embedder returned vector of length {len(vector)}, expected {embedder.dim}"
                )

        points = [
            models.PointStruct(id=chunk.id, vector=vector, payload=chunk_to_payload(chunk))
            for chunk, vector in zip(batch, vectors, strict=True)
        ]
        if points:
            await client.upsert(collection_name=collection, points=points, wait=True)
            upserted += len(points)

    current_ids = {chunk.id for chunk in chunks}
    stale_ids: list[models.ExtendedPointId] = []
    offset: object | None = None
    while True:
        records, offset = await client.scroll(
            collection_name=collection,
            limit=256,
            offset=offset,
            with_payload=False,
            with_vectors=False,
        )
        stale_ids.extend(str(record.id) for record in records if str(record.id) not in current_ids)
        if offset is None:
            break

    if stale_ids:
        await client.delete(
            collection_name=collection,
            points_selector=models.PointIdsList(points=stale_ids),
            wait=True,
        )

    count_result = await client.count(collection_name=collection, exact=True)

    return IngestReport(
        collection=collection,
        documents=documents,
        chunks=len(chunks),
        upserted=upserted,
        deleted_stale=len(stale_ids),
        created=created,
        points_after=count_result.count,
    )


async def build_index(settings: Settings, *, recreate: bool = False) -> IngestReport:
    """Load the corpus, chunk it, and ingest it into the configured Qdrant
    knowledge collection. Builds and tears down its own embedder and Qdrant
    client from `settings`."""
    docs = load_corpus()
    chunks = chunk_corpus(docs)

    tracer = Tracer.from_settings(settings)
    embedder = await load_embedder(settings, tracer)

    client = AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=(
            settings.qdrant_api_key.get_secret_value()
            if settings.qdrant_api_key is not None
            else None
        ),
        timeout=settings.qdrant_timeout,
    )
    try:
        return await ingest_corpus(
            client,
            embedder,
            collection=settings.knowledge_collection,
            chunks=chunks,
            documents=len(docs),
            recreate=recreate,
        )
    finally:
        await client.close()
