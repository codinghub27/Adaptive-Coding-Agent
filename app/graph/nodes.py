"""Graph nodes for the teaching graph.

Every node shares one signature: `async def node(state: AgentState, runtime:
Runtime[GraphContext]) -> AgentStateUpdate`. Nodes read dependencies (the LLM
client, DB session, ids) only from `runtime.context` -- never module-level
globals -- so they stay swappable and testable in isolation.

The specialized-agent nodes (`dsa_agent`, `debug_agent`, `explain_agent`) call
the Phase 07 subgraphs/pipelines -- `app.graph.subgraphs.dsa.run_dsa`,
`app.graph.subgraphs.debug.run_debug`, `app.graph.subgraphs.explain.run_explain`,
and `app.agents.reviewer.review_code` (dispatched from `explain_agent` for the
`CODE_REVIEW`/`OPTIMIZATION` intents) -- without renaming these node names
(see `app.graph.routing` for the STABLE routing contract these names are part
of). Every other node here is real Phase 04 pipeline logic.

Nodes must never interpolate raw user-supplied text (from `state.input` /
`state.structured_input`) into their own output -- that data is untrusted
content, not something to echo back. `NodeError.message` values are always
fixed, safe strings for the same reason: never the raw exception text or any
user-supplied content, either of which might carry secrets or untrusted data.
"""

import ast
import hashlib
import logging
import re
from collections.abc import Callable, Sequence
from types import MappingProxyType
from typing import Final, Protocol, cast
from uuid import UUID

from langgraph.runtime import Runtime

from app.agents.debugger import extract_learner_code
from app.agents.hint_engine import HintProgress
from app.agents.planner import (
    INTENT_DEFAULTS,
    MIN_RETRIEVAL_TOPIC_SCORE,
    analyze_problem,
    build_plan,
    clamp_assistance,
)
from app.agents.reviewer import review_code
from app.execution.synth import synthesize_test_suite
from app.execution.testgen import extract_test_suite
from app.execution.verification import verify as verify_result
from app.graph.routing import select_route
from app.graph.state import (
    AgentOutcome,
    AgentState,
    AgentStateUpdate,
    GraphContext,
    NodeError,
    SuiteSource,
)
from app.graph.subgraphs.debug import run_debug
from app.graph.subgraphs.dsa import run_dsa
from app.graph.subgraphs.explain import run_explain
from app.input.intent import classify_intent as _classify_intent_llm
from app.input.normalize import merge_inputs, normalize_text
from app.input.vision import ImageValidationError, extract_from_image
from app.llm.base import LLMError
from app.memory.conversation import add_turn, get_recent_context
from app.memory.events import record_event, requested_help_for
from app.memory.hint_progress import get_hint_progress, get_latest_hint_progress, save_hint_progress
from app.memory.profile import PRIOR, get_profile
from app.response.format import SAFE_FALLBACK_RESPONSE
from app.response.generate import generate_response
from app.schemas.event import LearningEventCreate, slug_tag
from app.schemas.execution import ExecutionResult, HarnessError, TestSuite, Verdict
from app.schemas.input import CodeBlock, StructuredInput
from app.schemas.intent import Intent
from app.schemas.knowledge import RetrievalHit
from app.schemas.plan import TeachingPlan
from app.schemas.profile import LearnerProfileView

__all__ = [
    "FALLBACKS",
    "Node",
    "SAFE_FALLBACK_RESPONSE",
    "build_retrieval_query",
    "classify_intent",
    "clarify",
    "debug_agent",
    "dsa_agent",
    "execute_code",
    "explain_agent",
    "final_response",
    "load_learner_profile",
    "plan_teaching",
    "resolve_hint_progress",
    "retrieve_knowledge",
    "route",
    "safe_node",
    "should_retrieve",
    "understand_input",
    "update_learner_model",
    "verify_execution",
]

logger = logging.getLogger(__name__)


class Node(Protocol):
    """Callable protocol for a graph node, matching langgraph's `_NodeWithRuntime`.

    `runtime` is keyword-only here (as langgraph itself requires); a plain
    `async def node(state, runtime)` function is still assignable, since a
    positional-or-keyword parameter satisfies a keyword-only call.
    """

    async def __call__(
        self, state: AgentState, *, runtime: Runtime[GraphContext]
    ) -> AgentStateUpdate: ...


# --- understand_input --------------------------------------------------------

_IMAGE_EXTRACTION_FAILED_MESSAGE: Final = "could not read the image; continuing with text only"


async def understand_input(state: AgentState, runtime: Runtime[GraphContext]) -> AgentStateUpdate:
    """Normalize `state.input`'s text and/or image into a `StructuredInput`.

    Always drops `state.input.image` from the returned update (keeping
    `image_mime`) once extraction has been attempted, so the raw image bytes
    aren't carried forward into every later node's state/trace.
    """
    ctx = runtime.context
    raw = state.input
    scrubbed_input = raw.model_copy(update={"image": None})

    text = raw.text
    text_result = (
        normalize_text(text, language_hint=raw.language) if text and text.strip() else None
    )

    if raw.image is None:
        return {"structured_input": text_result}

    try:
        image_result = await extract_from_image(ctx.llm, raw.image, declared_mime=raw.image_mime)
    except (ImageValidationError, LLMError) as exc:
        error = NodeError(
            node="understand_input",
            error_type=type(exc).__name__,
            message=_IMAGE_EXTRACTION_FAILED_MESSAGE,
        )
        return {"input": scrubbed_input, "structured_input": text_result, "errors": [error]}

    if text_result is not None:
        return {
            "input": scrubbed_input,
            "structured_input": merge_inputs(image_result, text_result),
        }
    return {"input": scrubbed_input, "structured_input": image_result}


def _understand_input_fallback(state: AgentState) -> AgentStateUpdate:
    scrubbed_input = state.input.model_copy(update={"image": None})
    return {"input": scrubbed_input, "structured_input": None}


# --- classify_intent ---------------------------------------------------------


async def classify_intent(state: AgentState, runtime: Runtime[GraphContext]) -> AgentStateUpdate:
    """Classify `state.structured_input`'s intent, or `None` for empty input."""
    inp = state.structured_input
    if inp is None or inp.is_empty:
        return {"intent": None}
    result = await _classify_intent_llm(inp, runtime.context.llm)
    return {"intent": result}


def _classify_intent_fallback(state: AgentState) -> AgentStateUpdate:
    del state
    return {"intent": None}


# --- load_learner_profile ----------------------------------------------------

_PROFILE_LOAD_FAILED_MESSAGE: Final = "could not load your learner profile"
_RECENT_CONTEXT_FAILED_MESSAGE: Final = "could not load recent conversation history"


async def load_learner_profile(
    state: AgentState, runtime: Runtime[GraphContext]
) -> AgentStateUpdate:
    """Load the learner's profile and recent conversation context, if available.

    Each read runs in its own savepoint (`session.begin_nested()`), so a DB
    error in one read can never leave the shared session's outer transaction
    aborted for later reads/writes in this turn. Errors are caught here
    (rather than left to `safe_node`) so a failed read degrades to an empty
    profile / empty context instead of losing the other read's result too.
    """
    del state
    ctx = runtime.context
    errors: list[NodeError] = []

    if ctx.session is not None and ctx.user_id is not None:
        try:
            async with ctx.session.begin_nested():
                profile = await get_profile(ctx.session, ctx.user_id)
        except Exception as exc:
            profile = LearnerProfileView.empty()
            errors.append(
                NodeError(
                    node="load_learner_profile",
                    error_type=type(exc).__name__,
                    message=_PROFILE_LOAD_FAILED_MESSAGE,
                )
            )
    else:
        profile = LearnerProfileView.empty()

    if ctx.session is None or ctx.user_id is None or ctx.conversation_id is None:
        update: AgentStateUpdate = {"profile": profile, "recent_context": []}
        if errors:
            update["errors"] = errors
        return update

    try:
        async with ctx.session.begin_nested():
            recent_context = await get_recent_context(ctx.session, ctx.user_id, ctx.conversation_id)
    except Exception as exc:
        recent_context = []
        errors.append(
            NodeError(
                node="load_learner_profile",
                error_type=type(exc).__name__,
                message=_RECENT_CONTEXT_FAILED_MESSAGE,
            )
        )

    update = {"profile": profile, "recent_context": recent_context}
    if errors:
        update["errors"] = errors
    return update


def _load_learner_profile_fallback(state: AgentState) -> AgentStateUpdate:
    del state
    return {"profile": LearnerProfileView.empty(), "recent_context": []}


# --- plan_teaching ------------------------------------------------------------


async def plan_teaching(state: AgentState, runtime: Runtime[GraphContext]) -> AgentStateUpdate:
    """Build this turn's `TeachingPlan` from the classified intent and profile."""
    del runtime
    profile = state.profile if state.profile is not None else LearnerProfileView.empty()
    analysis = analyze_problem(
        state.structured_input,
        profile,
        state.input.topic_hint,
        context=state.retrieved_context,
    )
    plan = build_plan(state.intent, profile, analysis)
    plan = clamp_assistance(plan, state.input.assistance_cap)
    return {"plan": plan}


def _plan_teaching_fallback(state: AgentState) -> AgentStateUpdate:
    # No `clamp_assistance` call needed here: this fallback plan's
    # `assistance_level` is already "hint", the floor of `ASSISTANCE_ORDER`,
    # so a cap could never lower it further.
    intent = state.intent
    strategy = (
        "clarify" if intent is None or intent.low_confidence else INTENT_DEFAULTS[intent.intent][1]
    )
    plan = TeachingPlan(
        difficulty="easy",
        assistance_level="hint",
        solution_strategy=strategy,
        topic=None,
        skill_level=PRIOR,
        rationale=["planner_fallback"],
    )
    return {"plan": plan}


# --- retrieve_knowledge --------------------------------------------------------


def should_retrieve(state: AgentState) -> bool:
    """Decide whether this turn should query the knowledge corpus.

    Retrieval now runs *before* `plan_teaching` (see `app.graph.build`), so
    `state.plan` does not exist yet here and this gate cannot consult it.
    It instead mirrors `select_route`'s first two guards directly -- no/empty
    structured input, no/low-confidence intent -- since those are exactly the
    "nothing usable to search with" conditions retrieval also needs to skip.
    `select_route`'s third guard ("the plan already decided to clarify") is
    intentionally dropped rather than reproduced: it cannot fire before the
    plan is computed, so omitting it changes no behaviour, only removes a
    check that could never have applied at this point in the graph. On top of
    that, this also skips a pure runtime-error debugging turn (the debugger
    works from the traceback itself, not pattern docs). Otherwise True: every
    DSA/explain intent, plus the other debug-route intents
    (`ERROR_EXPLANATION`, `TEST_CASE_ANALYSIS`), retrieve.
    """
    intent = state.intent
    structured = state.structured_input
    if structured is None or structured.is_empty:
        return False
    if intent is None or intent.low_confidence:
        return False
    return not (intent.intent == Intent.CODE_DEBUG and structured.error)


#: A bare identifier ending in `Error`/`Exception` (e.g. `IndexError`), never
#: the exception message or any other traceback text.
_EXCEPTION_TYPE_RE: Final = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception))\b")

#: Cap on how many identifiers `_extract_identifiers` contributes to a query,
#: so one large code block still can't swamp the short question/problem text
#: it's added alongside -- this is a bounded handful of tokens, not the code.
_MAX_QUERY_IDENTIFIERS: Final = 12

#: Python builtins/keywords common enough in *any* snippet that they carry no
#: topic signal (and would otherwise show up in nearly every query).
_IDENTIFIER_STOPWORDS: Final = frozenset(
    {
        "self",
        "cls",
        "range",
        "len",
        "print",
        "int",
        "str",
        "list",
        "dict",
        "set",
        "tuple",
        "True",
        "False",
        "None",
        "return",
    }
)


def _extract_exception_type(error: str | None) -> str | None:
    """The exception *type name* mentioned in `error`, or `None`.

    Deliberately narrow, per the audit that led to this: only a bare
    identifier shaped like `SomeError`/`SomeException` is pulled out -- never
    the exception message or any other traceback text, which is untrusted
    free text and must not be spliced into a retrieval query verbatim. The
    *last* match wins, since a full traceback lists the exception that was
    actually raised on its final line, and code/messages earlier in the text
    frequently *mention* other exception types (a caught-and-reraised clause,
    a docstring).
    """
    if not error:
        return None
    matches = _EXCEPTION_TYPE_RE.findall(error)
    return matches[-1] if matches else None


def _extract_identifiers(code: Sequence[CodeBlock]) -> list[str]:
    """Function/variable identifier names parsed out of `code` via `ast`.

    Structurally constrained to bare identifier tokens -- function/class
    names and variable references -- never full source lines, comments, or
    string/docstring contents, so this is safe to fold into a retrieval query
    even though `code` itself is untrusted learner input: there is no free
    text here for a corpus doc or an LLM prompt to misinterpret as
    instructions, only isolated names like `two_sum` or `dfs`. A block that
    fails to parse (non-Python, or invalid syntax -- common for buggy learner
    code) is silently skipped rather than raising: a parse failure must never
    cost the learner their turn. Order-preserving, deduped, common
    builtins/keywords filtered out, capped at `_MAX_QUERY_IDENTIFIERS`.
    """
    names: list[str] = []
    seen: set[str] = set()
    for block in code:
        try:
            tree = ast.parse(block.content)
        except (SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            name: str | None = None
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                name = node.name
            elif isinstance(node, ast.Name):
                name = node.id
            if (
                name is None
                or name in seen
                or name in _IDENTIFIER_STOPWORDS
                or name.startswith("__")
            ):
                continue
            seen.add(name)
            names.append(name)
            if len(names) >= _MAX_QUERY_IDENTIFIERS:
                return names
    return names


def build_retrieval_query(state: AgentState) -> str:
    """Build the retrieval query text from trusted-shape signal fields, plus
    two narrow, structurally-constrained signals pulled *out of* `code`/`error`.

    The raw `code`/`error` text is still never included: that was the change
    already tried and reverted, because splicing whole tracebacks and source
    blocks in swamps a short knowledge-corpus query with noise. What *is*
    included is two narrow, structurally-constrained signals: an extracted
    exception *type name* (never the message; see `_extract_exception_type`)
    and a bounded list of identifier names parsed out of `code` via `ast`
    (never the source itself; see `_extract_identifiers`) -- a handful of
    tokens, not the code.

    When there's no `problem` statement -- the common shape for a debug/
    review turn (`question` + `code` [+ `error`], no full problem text) --
    and identifiers were found, the query is the identifiers *alone* (plus
    the exception type), dropping `question`. This was a measured choice,
    not a guess: reranking `question` alongside the identifiers consistently
    scored *lower* against the correct corpus doc than the identifiers alone,
    across every debug/review probe measured (see
    `tests/graph/test_topic_accuracy.py`). The generic wrapper text nearly
    every debug/review turn shares ("why does this crash", "can you review
    this") is a *worse* signal than a handful of the learner's own
    function/variable names, which are corpus-vocabulary-shaped tokens
    (`two_sum`, `dfs`, `reverse_list`) a cross-encoder matches far more
    precisely than it does generic phrasing. This fixed both known misfiles
    that motivated it: an `IndexError` loop bug filed under `hashing`, and
    (partially -- see `MIN_RETRIEVAL_TOPIC_SCORE`) a `factorial` off-by-one
    filed under `binary_search_on_answer`.

    When a `problem` statement *is* present -- the common shape for a DSA
    turn -- it is always kept (official problem text is the strongest signal
    available and code, if any, is only a supplementary attempt), combined
    with `question`, the exception type, and identifiers.

    `state.plan` is never consulted: this node now runs *before*
    `plan_teaching` (see `app.graph.build`), so a plan doesn't exist yet at
    this point in the graph -- and joining it in was always circular besides
    (the plan's topic itself needs to be inferred from what retrieval
    returns). Duplicate parts (e.g. the question repeated in the problem
    statement) are deduped, order-preserving, so BM25 doesn't over-weight the
    repeated text. May return "" if nothing at all is present.
    """
    parts: list[str] = []
    structured = state.structured_input
    if structured is not None:
        identifiers = _extract_identifiers(structured.code)
        if structured.problem:
            if structured.question:
                parts.append(structured.question)
            parts.append(structured.problem)
        elif not identifiers and structured.question:
            parts.append(structured.question)
        if identifiers:
            parts.append(" ".join(identifiers))
        exception_type = _extract_exception_type(structured.error)
        if exception_type is not None:
            parts.append(exception_type)
    return "\n".join(dict.fromkeys(parts)).strip()


async def retrieve_knowledge(state: AgentState, runtime: Runtime[GraphContext]) -> AgentStateUpdate:
    """Query the knowledge corpus for this turn's topic/question/problem.

    Never touches `runtime.context.llm`/the LLM budget: retrieval runs
    entirely on local embedding/BM25/rerank models.
    """
    retriever = runtime.context.retriever
    if retriever is None or not should_retrieve(state):
        return {"retrieved_context": []}
    query = build_retrieval_query(state)
    if not query:
        return {"retrieved_context": []}
    hits = await retriever.retrieve(query, runtime.context.knowledge_top_k)
    return {"retrieved_context": hits}


def _retrieve_knowledge_fallback(state: AgentState) -> AgentStateUpdate:
    del state
    return {"retrieved_context": []}


# --- route --------------------------------------------------------------------


async def route(state: AgentState, runtime: Runtime[GraphContext]) -> AgentStateUpdate:
    """Record the routing decision for this turn on the state."""
    del runtime
    return {"route": select_route(state)}


def _route_fallback(state: AgentState) -> AgentStateUpdate:
    del state
    return {"route": "clarify"}


# --- Specialized agents: dsa_agent / debug_agent / explain_agent -----------

_AGENT_FALLBACK_TEXT: Final = (
    "Something went wrong on my end while working on that -- could you try again, "
    "or rephrase your request?"
)


def _agent_outcome_fallback(state: AgentState) -> AgentStateUpdate:
    del state
    return {"agent_output": AgentOutcome(text=_AGENT_FALLBACK_TEXT, topic=None, solved=None)}


# Last-resort fallback hint-ladder key used when the planner could not infer
# a topic for this turn (e.g. a new learner with an empty `skill_levels`
# profile and no `topic_hint`) *and* the turn carries no problem statement of
# its own to fingerprint *and* there is no earlier ladder on this
# conversation to continue (see `_hint_topic_key`) -- in practice, a bare
# follow-up ("give me the next hint") as the very first DSA turn in a brand
# new conversation.
#
# NOTE on the leading-underscore idea this was first written with: it does
# *not* guarantee no collision. `RawInput.topic_hint` (see `app.graph.state`)
# is arbitrary client-supplied text with no charset restriction beyond
# `Field(max_length=64)`, and `slug_tag` only lowercases and turns literal
# spaces into underscores -- it does not reject or strip a leading
# underscore. A learner (or a curious/adversarial client) could submit
# `topic="_general"` verbatim and it would slug to exactly that. A sentinel
# longer than 64 characters *would* be provably collision-free against any
# `topic_hint`-derived topic (`RawInput.topic_hint` and
# `LearningEventCreate.topic` both cap at 64 chars, and `slug_tag` never
# changes a string's length) -- but `hint_progress.topic` is itself
# `String(64)` in the DB (see `app.db.models.hint_progress.HintProgress`), so
# a >64-char sentinel silently fails to persist instead (the write's
# `except Exception: pass` swallows the resulting `DataError`), which is
# strictly worse than the bug this packet fixes.
#
# So this stays a short, `_`-prefixed, deliberately unusual literal: a
# *practical*, not cryptographic, mitigation. A learner would have to type
# this exact string as their topic to collide with the fallback ladder, in
# which case they simply share the same fallback ladder namespace as a
# topic-less turn -- not a security break, just a shared bucket. A provably
# collision-free scheme would need a schema change (e.g. a separate
# `is_fallback` column) that's out of scope for this fix; flagged for the
# planner to consider separately.
DEFAULT_HINT_TOPIC: Final = "__no_topic_inferred__"


def _problem_fingerprint(structured_input: StructuredInput | None) -> str | None:
    """Fingerprint key for the problem statement this turn carries, or `None`.

    "Carries a problem statement" means `structured_input.problem` is set.
    `app.input.normalize.normalize_text` only ever populates the dedicated
    `.problem` field when the prose actually *looks like* a problem
    statement (see its `_looks_like_problem` heuristic); `.question` is not a
    reliable signal on its own because it is also the sole home of bare
    follow-ups ("give me the next hint") that carry no problem content of
    their own -- treating every non-empty `.question` as a new problem would
    make those follow-ups fork the ladder instead of continuing it.

    `.question` *is* folded into the fingerprint when `.problem` is present,
    because `normalize_text`'s `_extract_direct_ask` can carve a
    trailing/leading "direct ask" line *out of* the very same pasted problem
    into `.question`, leaving the body in `.problem` -- when that happens
    both fields describe the same problem, and dropping `.question` would
    let two pastes of the same problem with slightly different direct-ask
    phrasing fork the ladder. `.code` and `.error` are never included: only
    the problem statement should decide the ladder, so a learner re-pasting
    the same problem with evolving (or broken) code stays on one ladder.

    The returned key is a hash of untrusted learner text, never the text
    itself -- it must never be rendered, logged, or returned to the client.
    """
    if structured_input is None or not structured_input.problem:
        return None
    parts = [structured_input.problem]
    if structured_input.question:
        parts.append(structured_input.question)
    normalized = " ".join(" ".join(parts).lower().split())
    digest = hashlib.sha256(normalized.encode()).hexdigest()[:16]
    return f"_q{digest}"


async def _hint_topic_key(
    structured_input: StructuredInput | None, plan: TeachingPlan | None, ctx: GraphContext
) -> str | None:
    """Return the hint-ladder store key for this turn, or `None` if there is no plan.

    Resolution order:
    1. `plan` is `None` -> `None` (no ladder at all for this turn).
    2. `plan.topic` is set -> `slug_tag(plan.topic)`, exactly as before.
    3. No topic, but `structured_input` carries a problem statement (see
       `_problem_fingerprint`) -> a fingerprint of that problem, so a
       *different* problem in the same topic-less conversation never shares
       a ladder with an unrelated one, and the *same* problem restated
       climbs the one ladder it belongs to.
    4. No topic and no problem statement (a bare follow-up like "Give me the
       next hint.") -> the most recently updated hint-ladder row for this
       `(user_id, conversation_id)`, so the follow-up climbs the ladder it
       actually belongs to instead of restarting or forking one.
    5. Nothing stored yet either -> `DEFAULT_HINT_TOPIC`, a fresh ladder.

    Shared by `resolve_hint_progress` (the read) and `dsa_agent` (the write),
    called with the same `(structured_input, plan)` before either has
    written anything this turn, so the two resolve identically and can never
    key differently. Case 4's DB lookup degrades silently (falls through to
    `DEFAULT_HINT_TOPIC`) on any failure or missing `ctx` dependency -- a
    history lookup must never cost the learner their turn.
    """
    if plan is None:
        return None
    if plan.topic:
        return slug_tag(plan.topic)

    fingerprint = _problem_fingerprint(structured_input)
    if fingerprint is not None:
        return fingerprint

    if ctx.session is not None and ctx.user_id is not None and ctx.conversation_id is not None:
        try:
            async with ctx.session.begin_nested():
                latest = await get_latest_hint_progress(
                    ctx.session, ctx.user_id, ctx.conversation_id
                )
            if latest is not None:
                return latest.topic
        except Exception:
            pass
    return DEFAULT_HINT_TOPIC


async def resolve_hint_progress(state: AgentState, ctx: GraphContext) -> HintProgress:
    """Read this conversation+topic's hint-ladder progress from the dedicated store.

    `AgentState` has nowhere to persist `HintProgress` across turns, so this
    reads it from `app.memory.hint_progress` (a small store dedicated to hint
    ladder state, upserted by `dsa_agent` -- see that function's docstring for
    why this is deliberately *not* rebuilt from `LearningEvent`s). Keyed on
    `_hint_topic_key(structured_input, plan, ctx)`, the same key `dsa_agent`
    writes under, so reads and writes always agree.

    Degrades to a fresh `HintProgress()` (never raises) whenever `ctx.session`
    or `ctx.user_id` is `None`, there is no plan at all, no stored row is
    found, or the DB lookup itself fails -- a history lookup must never cost
    the learner their turn. The read runs in its own savepoint, mirroring
    `load_learner_profile`, so a failure here can never leave the shared
    session's outer transaction aborted for later reads/writes in this turn.
    """
    if ctx.session is None or ctx.user_id is None or ctx.conversation_id is None:
        return HintProgress()
    topic = await _hint_topic_key(state.structured_input, state.plan, ctx)
    if topic is None:
        return HintProgress()

    try:
        async with ctx.session.begin_nested():
            return await get_hint_progress(ctx.session, ctx.user_id, ctx.conversation_id, topic)
    except Exception:
        return HintProgress()


async def dsa_agent(state: AgentState, runtime: Runtime[GraphContext]) -> AgentStateUpdate:
    """Run the DSA solver subgraph (hint ladder) for this turn.

    Persists the rung actually reached this turn to the hint-progress store
    (keyed by `_hint_topic_key`, the same helper `resolve_hint_progress` reads
    with, called with the same `(structured_input, plan)` so it resolves to
    the exact same row -- including any of its fallback cases), so the next
    turn on this conversation+topic resumes the ladder instead of restarting
    at L0. Only `dsa_agent` ever writes this store -- no other agent's turn
    can move (or reset) a DSA hint ladder. `solved` is carried forward
    unchanged from the progress read at the start of this turn: nothing in a
    hint turn itself observes whether the learner ultimately solved the
    problem (see `DSAResult.to_outcome`). Skipped (never raises) whenever
    `ctx.session`, `ctx.user_id`, `ctx.conversation_id`, or the plan itself is
    missing, the ladder produced no hint this turn (already solved), or the
    write itself fails -- a failed write must never cost the learner their
    turn's response.

    `solved` is no longer always absent evidence: when the learner actually
    submitted code this turn, `_resolve_test_suite` is consulted (extraction
    first, LLM synthesis only as a fallback -- see its docstring) and, when a
    suite survives, `run_dsa` runs THEIR code against it internally and
    attaches the resulting verdict to `DSAResult.initial_verdict`, which
    `to_outcome` turns into `solved`. A pure hint-only turn (no code
    submitted) short-circuits before any of that -- no suite lookup, no
    synthesis LLM call, no sandbox run -- so asking for a hint never spends
    budget it does not need and never risks becoming evidence against the
    learner. `suite_source` is recorded either way (`"none"` on the
    short-circuit path) so `_build_learning_event` can gate a synthesised
    suite's verdict on a real problem statement here exactly as it does for
    `debug_agent`.
    """
    ctx = runtime.context
    progress = await resolve_hint_progress(state, ctx)
    if extract_learner_code(state.structured_input) is not None:
        tests, suite_source = await _resolve_test_suite(state, runtime)
    else:
        tests, suite_source = None, "none"
    run = await run_dsa(state, runtime, progress=progress, tests=tests)
    update: AgentStateUpdate = {
        "agent_output": run.result.to_outcome(),
        "agent_result": run.result,
        "suite_source": suite_source,
    }
    if run.execution_request is not None:
        update["execution_request"] = run.execution_request

    topic = await _hint_topic_key(state.structured_input, state.plan, ctx)
    if (
        run.result.hint is not None
        and ctx.session is not None
        and ctx.user_id is not None
        and ctx.conversation_id is not None
        and topic is not None
    ):
        try:
            async with ctx.session.begin_nested():
                await save_hint_progress(
                    ctx.session,
                    ctx.user_id,
                    ctx.conversation_id,
                    topic,
                    level=int(run.result.hint.level),
                    solved=progress.solved,
                )
        except Exception:
            pass

    return update


async def _resolve_test_suite(
    state: AgentState, runtime: Runtime[GraphContext]
) -> tuple[TestSuite | None, SuiteSource]:
    """This turn's `TestSuite`, and where it came from.

    `extract_test_suite` is tried first -- deterministic and free, recovered
    only from worked examples already in the learner's own problem statement.
    Only when it finds nothing does this fall back to
    `synthesize_test_suite` (LLM-proposed, sandbox-validated before it is
    ever trusted -- see `app.execution.synth`), so a real debug/review turn
    can still verify a fix even when the statement has no worked examples.
    """
    tests = extract_test_suite(state.structured_input)
    if tests is not None:
        return tests, "extracted"
    tests = await synthesize_test_suite(
        state.structured_input, runtime.context.llm, runtime.context.runner
    )
    if tests is not None:
        return tests, "synthesised"
    return None, "none"


async def debug_agent(state: AgentState, runtime: Runtime[GraphContext]) -> AgentStateUpdate:
    """Run the debugger subgraph for this turn.

    See `_resolve_test_suite` for how this turn's `TestSuite` (if any) is
    found; that still loses to `state.execution_request.tests` when the
    latter is already set. `suite_source` records which path produced it so
    `_build_learning_event` can gate a synthesised suite's verdict on a real
    problem statement.
    """
    tests, suite_source = await _resolve_test_suite(state, runtime)
    run = await run_debug(state, runtime, tests=tests)
    update: AgentStateUpdate = {
        "agent_output": run.result.to_outcome(),
        "agent_result": run.result,
        "suite_source": suite_source,
    }
    if run.execution_request is not None:
        update["execution_request"] = run.execution_request
    return update


_REVIEW_INTENTS: Final[frozenset[Intent]] = frozenset({Intent.CODE_REVIEW, Intent.OPTIMIZATION})


async def explain_agent(state: AgentState, runtime: Runtime[GraphContext]) -> AgentStateUpdate:
    """Run the code/concept explainer for this turn, or the reviewer for
    `CODE_REVIEW`/`OPTIMIZATION` (see `app.graph.routing.INTENT_ROUTES`: both
    intents route to this node, but need the reviewer's pipeline instead)."""
    intent = state.intent
    if intent is not None and intent.intent in _REVIEW_INTENTS:
        tests, suite_source = await _resolve_test_suite(state, runtime)
        review_run = await review_code(state, runtime, tests=tests)
        update: AgentStateUpdate = {
            "agent_output": review_run.result.to_outcome(),
            "agent_result": review_run.result,
            "suite_source": suite_source,
        }
        if review_run.execution_request is not None:
            update["execution_request"] = review_run.execution_request
        return update

    explain_run = await run_explain(state, runtime)
    update = {
        "agent_output": explain_run.result.to_outcome(),
        "agent_result": explain_run.result,
    }
    if explain_run.execution_request is not None:
        update["execution_request"] = explain_run.execution_request
    return update


# --- execute_code -------------------------------------------------------------

_SANDBOX_UNAVAILABLE_MESSAGE: Final = "the code sandbox is not available right now"
_EXECUTION_NODE_FAILED_MESSAGE: Final = "running your code failed unexpectedly"


async def execute_code(state: AgentState, runtime: Runtime[GraphContext]) -> AgentStateUpdate:
    """Run `state.execution_request` in the sandbox, if an agent set one.

    Nothing here ever executes code itself -- it only ever hands the request
    to `runtime.context.runner` (a `CodeRunner`), which is the one thing in
    the app allowed to talk to the Docker sandbox. No request set (Phase 06:
    always, since no agent sets one yet) is a no-op skip, not an error.
    """
    request = state.execution_request
    if request is None:
        return {}
    runner = runtime.context.runner
    if runner is None:
        return {
            "execution_result": ExecutionResult(
                status="sandbox_error",
                language=request.language,
                error=HarnessError(type="SandboxUnavailable", message=_SANDBOX_UNAVAILABLE_MESSAGE),
            )
        }
    result = await runner.run(request)
    return {"execution_result": result}


def _execute_code_fallback(state: AgentState) -> AgentStateUpdate:
    if state.execution_request is None:
        return {}
    return {
        "execution_result": ExecutionResult(
            status="sandbox_error",
            language=state.execution_request.language,
            error=HarnessError(type="ExecutionNodeFailed", message=_EXECUTION_NODE_FAILED_MESSAGE),
        )
    }


# --- verify_execution -----------------------------------------------------------

_VERIFY_NODE_FAILED_SUMMARY: Final = "verifying your code's result failed unexpectedly"


async def verify_execution(state: AgentState, runtime: Runtime[GraphContext]) -> AgentStateUpdate:
    """Derive this turn's `Verdict` from `state.execution_result`, if anything ran.

    No request and no result (Phase 06: always, since nothing sets a request
    yet) is a no-op skip, matching `execute_code`'s skip. Delegates to the
    pure `app.execution.verification.verify` -- never re-implements the
    pass/fail logic here.
    """
    del runtime
    if state.execution_request is None and state.execution_result is None:
        return {"verification": None}
    return {"verification": verify_result(state.execution_result, state.execution_request)}


def _verify_execution_fallback(state: AgentState) -> AgentStateUpdate:
    if state.execution_request is None and state.execution_result is None:
        return {"verification": None}
    # A failing node must never yield "pass" -- degrade to inconclusive.
    return {
        "verification": Verdict(
            status="inconclusive",
            category="sandbox_error",
            summary=_VERIFY_NODE_FAILED_SUMMARY,
        )
    }


# --- Clarify node -----------------------------------------------------------

_ASK_FOR_INPUT: Final = (
    "I don't have enough to go on yet -- could you share the problem statement, "
    "your code, and/or the error message you're seeing?"
)

_GENERIC_CLARIFY: Final = (
    "Could you tell me a bit more about what you'd like help with -- a hint, "
    "a debugging walkthrough, an explanation, or a code review?"
)

_INTENT_PHRASES: Final[MappingProxyType[Intent, str]] = MappingProxyType(
    {
        Intent.DSA_SOLVE: "solve a DSA problem",
        Intent.DSA_HINT: "get a hint on a DSA problem",
        Intent.APPROACH_DISCUSSION: "discuss an approach to a problem",
        Intent.CODE_DEBUG: "debug your code",
        Intent.ERROR_EXPLANATION: "understand an error",
        Intent.TEST_CASE_ANALYSIS: "analyze a failing test case",
        Intent.CODE_EXPLAIN: "understand what your code does",
        Intent.CONCEPT_EXPLANATION: "explain a concept",
        Intent.IMAGE_CODE_ANALYSIS: "analyze code from an image",
        Intent.CODE_REVIEW: "review your code",
        Intent.OPTIMIZATION: "optimize your code",
    }
)


async def clarify(state: AgentState, runtime: Runtime[GraphContext]) -> AgentStateUpdate:
    """Ask a deterministic clarifying question; never echoes user input."""
    del runtime
    if state.structured_input is None or state.structured_input.is_empty:
        text = _ASK_FOR_INPUT
    elif state.intent is not None:
        phrase = _INTENT_PHRASES[state.intent.intent]
        text = (
            f"It looks like you might want to {phrase}. Could you confirm, or let me "
            "know if you'd prefer a hint, a debugging walkthrough, an explanation, or "
            "a code review?"
        )
    else:
        text = _GENERIC_CLARIFY
    return {"agent_output": AgentOutcome(text=text, topic=None, solved=None)}


# --- final_response -----------------------------------------------------------


async def final_response(state: AgentState, runtime: Runtime[GraphContext]) -> AgentStateUpdate:
    """Assemble the final learner-facing reply from this turn's agent result.

    Delegates to the pure, LLM-free `app.response.generate.generate_response`,
    which selects and arranges the prose already produced by the Phase 07
    agent according to this turn's `TeachingPlan` -- it never generates new
    content and never reveals code above the turn's assistance level.
    """
    del runtime
    generated = generate_response(
        result=state.agent_result,
        plan=state.plan,
        verification=state.verification,
        fallback_text=state.agent_output.text if state.agent_output is not None else None,
    )
    return {"response": generated.text, "generated_response": generated}


def _final_response_fallback(state: AgentState) -> AgentStateUpdate:
    del state
    return {"response": SAFE_FALLBACK_RESPONSE}


# --- update_learner_model -----------------------------------------------------

_BUILD_EVENT_FAILED_MESSAGE: Final = "failed to build this turn's learning event"
_SAVE_EVENT_FAILED_MESSAGE: Final = "failed to save this turn's learning event"
_SAVE_TURN_FAILED_MESSAGE: Final = "failed to save this turn's conversation history"


def _trusted_labels(retrieved_context: Sequence[RetrievalHit]) -> frozenset[str]:
    """Corpus labels this turn's retrieval actually vouches for.

    Only hits at or above `MIN_RETRIEVAL_TOPIC_SCORE` count. The retriever
    always returns its best few chunks, so a turn that names no subject still
    gets an answer back -- just a meaningless one, and corroborating against
    that noise is no corroboration at all.
    """
    return frozenset(
        label
        for hit in retrieved_context
        if hit.score >= MIN_RETRIEVAL_TOPIC_SCORE
        for label in (hit.chunk.pattern, hit.chunk.topic)
        if label
    )


def _event_topic(plan: TeachingPlan | None) -> str | None:
    """The topic this turn is recorded under, or `None` to record nothing.

    A topic here becomes a **key in the learner's skill map**, so it may only
    come from a trusted, closed vocabulary. `plan.topic` is exactly that: it
    resolves from an explicit topic hint, a key already in the profile, or a
    `KnowledgeChunk` label that cleared the relevance floor -- all validated
    through `slug_tag`.

    The solver's own `agent_output.topic` is deliberately NOT used. It is free
    text the LLM was asked to produce ("A short topic tag for this problem"),
    and on a turn that names no subject the model answered `"unknown"` -- which
    was written into the profile as a skill. A key like that can never match
    the vocabulary the planner matches against, so it would sit in the skill
    map forever, unusable. When the plan has no topic, the turn is simply not
    recorded; nothing is lost but noise.
    """
    return plan.topic if plan is not None else None


def _build_learning_event(state: AgentState, ctx: GraphContext, topic: str) -> LearningEventCreate:
    """Build this turn's `LearningEventCreate`.

    Gates a synthesised suite's verdict on a real problem statement: when
    `state.suite_source == "synthesised"` and `state.structured_input.problem`
    is absent/blank, `solved` is forced to `None` here regardless of what the
    agent reported. With no problem statement, the learner's own (possibly
    buggy) code is the only spec `synthesize_test_suite` had to work from, so
    it can propose test cases that match the bug AND a reference solution
    that reproduces it -- a pair that is self-consistent, passes the sandbox
    check, and would otherwise certify broken code as `solved=True`. The
    suite is still worth having (it makes the debugging/review answer
    better); it must simply never be used to judge the learner. An
    `extracted` suite is not gated: its cases come from the statement's own
    worked examples, not an LLM guess.
    """
    agent_output = state.agent_output
    intent_result = state.intent
    if agent_output is None or intent_result is None:
        # Callers only reach here after confirming both are set; this is a
        # future-proofing invariant, not expected control flow.
        raise ValueError("agent_output and intent are required to build a learning event")

    structured = state.structured_input
    problem = (structured.problem or structured.question) if structured is not None else None

    solved = agent_output.solved
    has_problem_statement = bool(
        structured is not None and structured.problem and structured.problem.strip()
    )
    if state.suite_source == "synthesised" and not has_problem_statement:
        solved = None
    # `pattern` becomes a second skill key (see `app.memory.profile.skill_keys`)
    # and is LLM-authored like `topic` was, so it is kept only when this turn's
    # retrieval vouches for it.
    suggested = agent_output.pattern
    pattern = (
        suggested
        if suggested and slug_tag(suggested) in _trusted_labels(state.retrieved_context)
        else None
    )

    return LearningEventCreate(
        conversation_id=ctx.conversation_id,
        intent=intent_result.intent,
        problem=problem,
        topic=topic,
        pattern=pattern,
        difficulty=state.plan.difficulty if state.plan is not None else None,
        requested_help=requested_help_for(intent_result.intent),
        hints_used=agent_output.hints_used,
        needed_full_solution=agent_output.needed_full_solution,
        errors=agent_output.errors,
        solved=solved,
    )


async def _persist_event(
    ctx: GraphContext, event: LearningEventCreate
) -> tuple[UUID | None, NodeError | None]:
    """Attempt to record `event`, isolated in its own savepoint.

    Returns the persisted event id (only when a new row was actually
    inserted) and/or a `NodeError` -- never raises.
    """
    if ctx.session is None or ctx.user_id is None:
        # Callers only reach here after confirming both are set; this is a
        # future-proofing invariant, not expected control flow.
        return None, NodeError(
            node="update_learner_model",
            error_type="RuntimeError",
            message=_SAVE_EVENT_FAILED_MESSAGE,
        )
    try:
        async with ctx.session.begin_nested():
            result = await record_event(ctx.session, ctx.user_id, event)
    except Exception as exc:
        return None, NodeError(
            node="update_learner_model",
            error_type=type(exc).__name__,
            message=_SAVE_EVENT_FAILED_MESSAGE,
        )
    return (result.event.id if result.applied else None), None


async def _persist_turns(state: AgentState, ctx: GraphContext) -> NodeError | None:
    """Save the user (and, if present, assistant) turn, isolated in its own savepoint."""
    if ctx.session is None or ctx.user_id is None or ctx.conversation_id is None:
        # Callers only reach here after confirming all three are set; this is
        # a future-proofing invariant, not expected control flow.
        return NodeError(
            node="update_learner_model",
            error_type="RuntimeError",
            message=_SAVE_TURN_FAILED_MESSAGE,
        )

    user_text = state.input.text
    user_content = user_text if user_text and user_text.strip() else "[image]"
    intent_result = state.intent

    try:
        async with ctx.session.begin_nested():
            await add_turn(
                ctx.session,
                ctx.user_id,
                ctx.conversation_id,
                "user",
                user_content,
                intent=intent_result.intent if intent_result is not None else None,
            )
            if state.response is not None:
                await add_turn(
                    ctx.session, ctx.user_id, ctx.conversation_id, "assistant", state.response
                )
    except Exception as exc:
        return NodeError(
            node="update_learner_model",
            error_type=type(exc).__name__,
            message=_SAVE_TURN_FAILED_MESSAGE,
        )
    return None


async def update_learner_model(
    state: AgentState, runtime: Runtime[GraphContext]
) -> AgentStateUpdate:
    """Emit (and, when safe, persist) this turn's learning event and conversation turns.

    A learning event is built whenever a topic is available (see
    `_event_topic`) and is *saved* (see `_persist_event`) whenever a
    session/user are both available -- regardless of whether this turn
    carries an observed outcome. `agent_output.solved is None` (e.g. a hint
    request) is still persisted: it records that the topic was encountered,
    which `apply_event` treats as exposure, not as a pass or fail (see
    `app.memory.profile.apply_event`). Conversation turns are only saved
    when `conversation_id` is set. Never commits: the caller owns the
    transaction.
    """
    ctx = runtime.context
    events: list[LearningEventCreate] = []
    events_persisted: list[UUID] = []
    errors: list[NodeError] = []

    agent_output = state.agent_output
    if (
        state.route is not None
        and state.route != "clarify"
        and state.intent is not None
        and agent_output is not None
    ):
        event: LearningEventCreate | None = None
        try:
            topic = _event_topic(state.plan)
            if topic is not None:
                event = _build_learning_event(state, ctx, topic)
        except Exception as exc:
            errors.append(
                NodeError(
                    node="update_learner_model",
                    error_type=type(exc).__name__,
                    message=_BUILD_EVENT_FAILED_MESSAGE,
                )
            )

        if event is not None:
            events.append(event)

            can_persist = ctx.session is not None and ctx.user_id is not None
            if can_persist:
                persisted_id, event_error = await _persist_event(ctx, event)
                if persisted_id is not None:
                    events_persisted.append(persisted_id)
                if event_error is not None:
                    errors.append(event_error)

    if ctx.conversation_id is not None and ctx.session is not None and ctx.user_id is not None:
        turn_error = await _persist_turns(state, ctx)
        if turn_error is not None:
            errors.append(turn_error)

    update: AgentStateUpdate = {}
    if events:
        update["events"] = events
    if events_persisted:
        update["events_persisted"] = events_persisted
    if errors:
        update["errors"] = errors
    return update


def _update_learner_model_fallback(state: AgentState) -> AgentStateUpdate:
    del state
    return {}


# --- safe_node ----------------------------------------------------------------

_SAFE_NODE_FAILURE_MESSAGE: Final = "this step failed unexpectedly; a safe fallback was used"


def safe_node(
    name: str,
    fn: Node,
    fallback: Callable[[AgentState], AgentStateUpdate],
) -> Node:
    """Wrap `fn` so any `Exception` degrades to `fallback(state)` plus a `NodeError`.

    Only `Exception` (never `BaseException`) is caught, so cooperative
    cancellation (`asyncio.CancelledError`) always propagates. The log line
    and the recorded `NodeError.message` are both fixed, safe strings --
    never the raw exception text, which may carry secrets or untrusted data.
    """

    async def wrapped(state: AgentState, *, runtime: Runtime[GraphContext]) -> AgentStateUpdate:
        try:
            return await fn(state, runtime=runtime)
        except Exception as exc:
            logger.warning("graph node %s failed: %s", name, type(exc).__name__)
            update = fallback(state)
            node_error = NodeError(
                node=name, error_type=type(exc).__name__, message=_SAFE_NODE_FAILURE_MESSAGE
            )
            existing_errors: list[NodeError] = list(update.get("errors", []))
            existing_errors.append(node_error)
            merged: AgentStateUpdate = cast("AgentStateUpdate", dict(update))
            merged["errors"] = existing_errors
            return merged

    return wrapped


FALLBACKS: Final[MappingProxyType[str, Callable[[AgentState], AgentStateUpdate]]] = (
    MappingProxyType(
        {
            "understand_input": _understand_input_fallback,
            "classify_intent": _classify_intent_fallback,
            "load_learner_profile": _load_learner_profile_fallback,
            "plan_teaching": _plan_teaching_fallback,
            "retrieve_knowledge": _retrieve_knowledge_fallback,
            "route": _route_fallback,
            "dsa_agent": _agent_outcome_fallback,
            "debug_agent": _agent_outcome_fallback,
            "explain_agent": _agent_outcome_fallback,
            "execute_code": _execute_code_fallback,
            "verify": _verify_execution_fallback,
            "clarify": _agent_outcome_fallback,
            "final_response": _final_response_fallback,
            "update_learner_model": _update_learner_model_fallback,
        }
    )
)
