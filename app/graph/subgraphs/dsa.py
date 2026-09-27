"""The DSA solver subgraph: hint ladder + level-gated problem analysis.

`DSAState` is this subgraph's own **private** scratch state -- never
`AgentState`, which is frozen with `extra="forbid"` and must not grow
scratch fields for one capability. `run_dsa` is the clean boundary the outer
graph (Phase 07 P7's `dsa_agent` node) calls: it maps `AgentState` onto a
fresh `DSAState`, runs the compiled subgraph once, and maps the finished
`DSAState` back onto a `DSAResult` (plus, when this turn produced an L6 full
solution, the `ExecutionRequest` to hand to `execute_code`/`verify`).

Pipeline: `understand -> constraints -> pattern -> brute_force -> why_slow
-> key_insight -> hint`. Exactly one LLM call happens, in `understand`
(`app.agents.dsa_solver.analyze_dsa_problem`), to stay within the graph
run's LLM call budget (`BudgetedLLMClient`); every other content node is a
small, single-purpose extraction of its own already-level-gated field(s)
from that one parsed `DSAAnalysis` -- never a second call. `hint` is the
only node that touches `app.agents.hint_engine.next_hint` (pure, LLM-free);
it is safe to call it twice in one run (once, in `understand`, purely to
determine this turn's gating level; again here for the attached result)
because it is a deterministic, side-effect-free function of unchanged
inputs.

After that compiled pipeline finishes, `run_dsa` runs one more (non-LLM)
pass of its own, outside the `StateGraph`: when the learner submitted code
this turn AND a validated `TestSuite` was handed in (see
`app.graph.nodes._resolve_test_suite`), their code is run once against it
via `runtime.context.runner` -- the only sandbox ground truth
`DSAResult.initial_verdict` may ever carry. That same request is also
returned as `DSARunResult.execution_request` so the outer graph's
`execute_code -> verify` edges re-run it and produce `state.verification`;
running the learner's code twice this way is a deliberate, accepted
double-run, mirroring `app.graph.subgraphs.debug.run_debug`.

`state["problem"]` (sourced from `AgentState.structured_input`) is untrusted
learner content, exactly as documented in `app.graph.nodes`'s module
docstring; it is only ever handed to the LLM as clearly-delimited data to
reason about (see `app.agents.dsa_solver`'s module docstring), never echoed
into `DSAResult`'s free-text fields.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from typing import Final, TypedDict

from langgraph.graph import END, START, StateGraph  # pyright: ignore[reportMissingTypeStubs]
from langgraph.graph.state import (  # pyright: ignore[reportMissingTypeStubs]
    CompiledStateGraph,
)
from langgraph.runtime import Runtime

from app.agents.debugger import extract_learner_code
from app.agents.dsa_solver import DSAAnalysis, analyze_dsa_problem, build_execution_request
from app.agents.hint_engine import HintProgress, next_hint
from app.execution.verification import verify
from app.graph.state import AgentState, GraphContext
from app.knowledge.ingest import chunk_corpus, load_corpus
from app.schemas.agent_results import DSAResult, HintLevel, HintResult
from app.schemas.execution import (
    ExecutionRequest,
    ExecutionResult,
    HarnessError,
    TestSuite,
    Verdict,
)
from app.schemas.input import StructuredInput
from app.schemas.knowledge import KnowledgeChunk, RetrievalHit
from app.schemas.plan import TeachingPlan

__all__ = [
    "DSARunResult",
    "DSAState",
    "build_dsa_graph",
    "get_dsa_graph",
    "run_dsa",
]


class DSAState(TypedDict, total=False):
    """Private scratch state threaded through the DSA solver subgraph only.

    `problem`/`plan`/`context`/`progress`/`ladder_topic` are this run's inputs
    (set once by `run_dsa` and never rewritten by any node); every other field
    is a stage output, populated incrementally as the pipeline runs.
    `analysis` holds the one parsed, level-masked `DSAAnalysis` from
    `understand`'s single LLM call -- every later content node reads its own
    field(s) from it rather than calling the LLM again.

    `ladder_topic` (Packet P5b) is the hint ladder's anchored topic slug for
    this conversation -- resolved by `app.graph.nodes._hint_topic_key`, the
    same key that ladder's `HintProgress` is stored under -- used only as
    `next_hint`'s fallback when `plan.topic` is `None` (a bare follow-up turn
    that resolved no topic of its own; see `hint_engine.next_hint`'s
    docstring) and to look up that pattern's own corpus chunks directly for
    rung grounding (see `_grounding_context`), independent of whatever this
    turn's own retrieval happened to surface.
    """

    problem: StructuredInput | None
    plan: TeachingPlan | None
    context: list[RetrievalHit]
    progress: HintProgress
    ladder_topic: str | None

    hint_level: HintLevel | None
    analysis: DSAAnalysis | None

    understanding: str | None
    constraints: list[str]
    topic: str | None
    pattern: str | None
    common_mistakes: list[str]
    brute_force: str | None
    why_slow: str | None
    key_insight: str | None
    pseudocode: str | None
    complexity_time: str | None
    complexity_space: str | None
    code: str | None
    hint: HintResult | None


_CompiledDSAGraph = CompiledStateGraph[DSAState, GraphContext, DSAState, DSAState]

_DEFAULT_PROGRESS: Final = HintProgress()

#: Score assigned to a corpus chunk fetched directly for the anchored ladder
#: topic (see `_grounding_context`), comfortably above
#: `app.agents.hint_engine.MIN_GROUNDING_SCORE`. These chunks are not scored
#: by any retriever or reranker -- they are looked up by an exact pattern-slug
#: match, which is strictly more certain than any retrieval score, so a fixed
#: constant (rather than a borrowed retrieval score) is the honest choice.
_ANCHOR_HIT_SCORE: Final = 0.0


@cache
def corpus_chunks_by_pattern() -> Mapping[str, tuple[KnowledgeChunk, ...]]:
    """Every curated corpus chunk, grouped by its `pattern` slug.

    `pattern` (e.g. `sliding_window`), not `topic` (e.g. `arrays`), is the
    vocabulary `TeachingPlan.topic` and the hint ladder's anchored topic share
    -- see `app.agents.planner.analyze_problem`: `topic = chunk.pattern or
    chunk.topic`. Loaded once per process from the bundled, curated corpus
    files (`app.knowledge.ingest.load_corpus`/`chunk_corpus` -- pure, no
    network, no learner input) and cached, mirroring `get_dsa_graph` below.
    """
    grouped: dict[str, list[KnowledgeChunk]] = {}
    for chunk in chunk_corpus(load_corpus()):
        grouped.setdefault(chunk.pattern, []).append(chunk)
    return {pattern: tuple(chunks) for pattern, chunks in grouped.items()}


def _grounding_context(
    context: Sequence[RetrievalHit], ladder_topic: str | None
) -> list[RetrievalHit]:
    """This turn's retrieved hits, extended with the anchored topic's own
    corpus chunks -- used only to ground hint-rung text (Packet P5b), never
    as `analyze_dsa_problem`'s LLM context, which stays exactly this turn's
    real retrieval, unmodified by this function.

    Packet P5's grounding asks for one specific corpus section per rung tier
    and takes nothing if that section isn't among the (top-k, this-turn-only)
    retrieved hits -- a coin flip at best over a ~300-chunk corpus with top-k
    4, and worse still on a bare follow-up turn whose own retrieval query is
    thin, topic-agnostic prose ("give me the next hint"). Once the ladder is
    anchored to a topic at all (see `app.graph.nodes._hint_topic_key`), that
    topic's own corpus document unambiguously has every section a rung might
    want, so this appends it as a fallback tier rather than relying on
    retrieval luck.

    `context`'s real, scored hits are always listed first, so a hit this turn
    actually retrieved for the wanted section still wins --
    `hint_engine._chunk_for_section`/`_identification_signal` both return the
    first match in iteration order. The appended chunks only fill in a
    section no retrieved hit this turn happened to carry. `_UNGROUNDABLE_SECTIONS`
    still applies downstream in `hint_engine._trusted_hits` exactly as it does
    for retrieved hits, so `general_template`/`representative_problems` stay
    ineligible here too.
    """
    extended = list(context)
    if ladder_topic:
        for chunk in corpus_chunks_by_pattern().get(ladder_topic, ()):
            extended.append(
                RetrievalHit(chunk=chunk, score=_ANCHOR_HIT_SCORE, retrievers=(), reranked=False)
            )
    return extended


# --------------------------------------------------------------------------
# Nodes
# --------------------------------------------------------------------------


async def _understand(state: DSAState, runtime: Runtime[GraphContext]) -> DSAState:
    """Determine this turn's gating hint level, then run the one LLM call.

    Both a missing plan and `progress.solved` leave `hint_level`/`analysis`
    at `None`: no plan means no ceiling to gate against, and a solved
    problem means this turn has nothing further to teach.
    """
    plan = state.get("plan")
    if plan is None:
        return {"hint_level": None, "analysis": None}

    progress = state.get("progress", _DEFAULT_PROGRESS)
    context = state.get("context", [])
    ladder_topic = state.get("ladder_topic")
    preview = next_hint(
        None,
        plan,
        progress,
        context=_grounding_context(context, ladder_topic),
        ladder_topic=ladder_topic,
    )
    if preview is None:
        return {"hint_level": None, "analysis": None}

    level = preview.level
    problem = state.get("problem")
    if problem is None or problem.is_empty:
        return {"hint_level": level, "analysis": None, "understanding": None}

    analysis = await analyze_dsa_problem(problem, plan, level, context, runtime.context.llm)
    return {"hint_level": level, "analysis": analysis, "understanding": analysis.understanding}


async def _constraints(state: DSAState, runtime: Runtime[GraphContext]) -> DSAState:
    """Surface the (already level-gated) constraints and common-mistakes fields."""
    del runtime
    analysis = state.get("analysis")
    if analysis is None:
        return {}
    return {"constraints": analysis.constraints, "common_mistakes": analysis.common_mistakes}


async def _pattern(state: DSAState, runtime: Runtime[GraphContext]) -> DSAState:
    """Surface the (already level-gated) topic/pattern tags, drawn from `retrieved_context`."""
    del runtime
    analysis = state.get("analysis")
    if analysis is None:
        return {}
    return {"topic": analysis.topic, "pattern": analysis.pattern}


async def _brute_force(state: DSAState, runtime: Runtime[GraphContext]) -> DSAState:
    """Surface the (already level-gated) brute-force description."""
    del runtime
    analysis = state.get("analysis")
    if analysis is None:
        return {}
    return {"brute_force": analysis.brute_force}


async def _why_slow(state: DSAState, runtime: Runtime[GraphContext]) -> DSAState:
    """Surface the (already level-gated) explanation of why the brute force is slow."""
    del runtime
    analysis = state.get("analysis")
    if analysis is None:
        return {}
    return {"why_slow": analysis.why_slow}


async def _key_insight(state: DSAState, runtime: Runtime[GraphContext]) -> DSAState:
    """Surface the (already level-gated) solution-shape fields: insight through code.

    Grouped in one node because they form a single "reveal the efficient
    solution" tier (`key_insight` at L3, `pseudocode`/complexity at L4,
    `code` only at L6) -- `analysis` already enforces every one of those
    gates, so this node's only job is to copy the fields across.
    """
    del runtime
    analysis = state.get("analysis")
    if analysis is None:
        return {}
    return {
        "key_insight": analysis.key_insight,
        "pseudocode": analysis.pseudocode,
        "complexity_time": analysis.complexity_time,
        "complexity_space": analysis.complexity_space,
        "code": analysis.code,
    }


async def _hint(state: DSAState, runtime: Runtime[GraphContext]) -> DSAState:
    """Compute and attach this turn's authoritative `HintResult`.

    Recomputes `next_hint` (rather than reusing `understand`'s preview
    level) so this node stands on its own as the pipeline's one authoritative
    hint-ladder step; `next_hint` is pure, so this is guaranteed to reproduce
    the same level `understand` gated `analysis` against.
    """
    del runtime
    plan = state.get("plan")
    if plan is None:
        return {"hint": None}
    progress = state.get("progress", _DEFAULT_PROGRESS)
    context = state.get("context", [])
    ladder_topic = state.get("ladder_topic")
    return {
        "hint": next_hint(
            None,
            plan,
            progress,
            context=_grounding_context(context, ladder_topic),
            ladder_topic=ladder_topic,
        )
    }


# --------------------------------------------------------------------------
# Graph assembly
# --------------------------------------------------------------------------


def build_dsa_graph() -> _CompiledDSAGraph:
    """Build and compile a fresh DSA solver subgraph."""
    builder = StateGraph(DSAState, context_schema=GraphContext)
    builder.add_node("understand", _understand)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("constraints", _constraints)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("pattern", _pattern)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("brute_force", _brute_force)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("why_slow", _why_slow)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("key_insight", _key_insight)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("hint", _hint)  # pyright: ignore[reportUnknownMemberType]

    builder.add_edge(START, "understand")
    builder.add_edge("understand", "constraints")
    builder.add_edge("constraints", "pattern")
    builder.add_edge("pattern", "brute_force")
    builder.add_edge("brute_force", "why_slow")
    builder.add_edge("why_slow", "key_insight")
    builder.add_edge("key_insight", "hint")
    builder.add_edge("hint", END)

    return builder.compile()  # pyright: ignore[reportUnknownMemberType]


@cache
def get_dsa_graph() -> _CompiledDSAGraph:
    """The compiled DSA solver subgraph, built once and cached for reuse.

    Stateless (no checkpointer, no module-level mutable state), so sharing
    this one instance across concurrent runs is safe -- mirrors
    `app.graph.build.get_graph`.
    """
    return build_dsa_graph()


# --------------------------------------------------------------------------
# AgentState <-> DSAState boundary
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DSARunResult:
    """The outcome of one `run_dsa` call.

    `execution_request` is set from one of two sources, in priority order:
    the learner's own submitted code (paired with the `TestSuite` that
    already produced `result.initial_verdict` inside this call -- see the
    module docstring), or, failing that, `result.code` (an L6 full solution
    the agent itself revealed). Either way, `dsa_agent` puts it on
    `AgentState.execution_request` so the outer `execute_code -> verify`
    edges (Phase 06) re-run it and produce `state.verification`.
    """

    result: DSAResult
    execution_request: ExecutionRequest | None


_LEARNER_RUN_FAILED_MESSAGE: Final = "running your code failed unexpectedly"


def _sandbox_error_result(request: ExecutionRequest) -> ExecutionResult:
    return ExecutionResult(
        status="sandbox_error",
        language=request.language,
        error=HarnessError(type="DSARunFailed", message=_LEARNER_RUN_FAILED_MESSAGE),
    )


async def _run_learner_code(
    request: ExecutionRequest, runtime: Runtime[GraphContext]
) -> ExecutionResult:
    """Run `request` via `runtime.context.runner`, degrading any raised
    exception to a `sandbox_error` result rather than propagating it (which
    could otherwise leak raw exception text from an untrusted run) -- mirrors
    `app.graph.subgraphs.debug._run_in_sandbox`."""
    runner = runtime.context.runner
    if runner is None:
        return _sandbox_error_result(request)
    try:
        return await runner.run(request)
    except Exception:  # noqa: BLE001 - never leak the raw exception from an untrusted run
        return _sandbox_error_result(request)


def _citation_labels(context: Sequence[RetrievalHit]) -> list[str]:
    """Human-readable citation labels for the chunks this turn drew on.

    These are shown to the learner, so they must read as sources rather than
    storage keys: citing `1a9847c1-88f9-5c46-b9b3-4a79ccb3b135` tells a learner
    nothing. `KnowledgeChunk.title`/`heading` carry the meaning, so a citation
    becomes e.g. "Sliding Window - Shrinking the window". Duplicates collapse,
    since several retrieved chunks often share one heading. The groundedness
    evaluation planned for a later phase reads `state.retrieved_context`
    directly, so no machine linkage is lost by not emitting ids here.
    """
    labels: list[str] = []
    for hit in context:
        chunk = hit.chunk
        parts = [part for part in (chunk.title.strip(), chunk.heading.strip()) if part]
        label = " - ".join(parts) or chunk.source.strip() or chunk.id
        if label not in labels:
            labels.append(label)
    return labels


async def run_dsa(
    state: AgentState,
    runtime: Runtime[GraphContext],
    *,
    progress: HintProgress = _DEFAULT_PROGRESS,
    tests: TestSuite | None = None,
    ladder_topic: str | None = None,
) -> DSARunResult:
    """Run the DSA solver subgraph for this turn and map the result back.

    `progress` defaults to a fresh ladder (`last_level=None`): `AgentState`
    does not yet carry a persisted hint-progress field across turns (no
    packet in this phase adds one), so today every call starts the ladder
    over. Passing an explicit `progress` (e.g. from a caller that tracks it
    elsewhere) lets a turn resume mid-ladder without changing this
    function's primary two-argument shape that `dsa_agent` calls.

    `tests` is this turn's validated `TestSuite`, if `dsa_agent` resolved
    one (only attempted when the learner actually submitted code -- see its
    docstring). When both the learner's code and `tests` are present, that
    code is run once against it here to produce `result.initial_verdict`;
    otherwise `initial_verdict` stays `None`, so `DSAResult.to_outcome`
    reports `solved=None` rather than fabricating evidence.

    `ladder_topic` (Packet P5b) is the conversation's anchored hint-ladder
    topic slug, resolved by `dsa_agent` via `app.graph.nodes._hint_topic_key`
    -- see `DSAState`'s docstring for what it's used for downstream. Defaults
    to `None` (a caller that doesn't pass one just gets the pre-Packet-P5b
    behaviour: `plan.topic` alone, no corpus-direct grounding fallback).
    """
    initial: DSAState = {
        "problem": state.structured_input,
        "plan": state.plan,
        "context": list(state.retrieved_context),
        "progress": progress,
        "ladder_topic": ladder_topic,
    }
    final_state = await get_dsa_graph().ainvoke(  # pyright: ignore[reportUnknownMemberType]
        initial, context=runtime.context
    )

    learner_code = extract_learner_code(state.structured_input)
    learner_request: ExecutionRequest | None = None
    initial_verdict: Verdict | None = None
    if learner_code is not None and tests is not None:
        learner_request = ExecutionRequest(code=learner_code, tests=tests)
        learner_result = await _run_learner_code(learner_request, runtime)
        initial_verdict = verify(learner_result, learner_request)

    citations = _citation_labels(state.retrieved_context)
    result = DSAResult(
        topic=final_state.get("topic"),
        pattern=final_state.get("pattern"),
        hint=final_state.get("hint"),
        understanding=final_state.get("understanding"),
        constraints=final_state.get("constraints") or [],
        brute_force=final_state.get("brute_force"),
        why_slow=final_state.get("why_slow"),
        key_insight=final_state.get("key_insight"),
        pseudocode=final_state.get("pseudocode"),
        code=final_state.get("code"),
        complexity_time=final_state.get("complexity_time"),
        complexity_space=final_state.get("complexity_space"),
        common_mistakes=final_state.get("common_mistakes") or [],
        citations=citations,
        initial_verdict=initial_verdict,
    )
    solution_request = build_execution_request(result.code) if result.code else None
    execution_request = learner_request or solution_request
    return DSARunResult(result=result, execution_request=execution_request)
