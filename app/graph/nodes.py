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

import hashlib
import logging
from collections.abc import Callable
from types import MappingProxyType
from typing import Final, Protocol, cast
from uuid import UUID

from langgraph.runtime import Runtime

from app.agents.hint_engine import HintProgress
from app.agents.planner import INTENT_DEFAULTS, analyze_problem, build_plan, clamp_assistance
from app.agents.reviewer import review_code
from app.execution.testgen import extract_test_suite
from app.execution.verification import verify as verify_result
from app.graph.routing import select_route
from app.graph.state import (
    AgentOutcome,
    AgentState,
    AgentStateUpdate,
    GraphContext,
    NodeError,
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
from app.schemas.execution import ExecutionResult, HarnessError, Verdict
from app.schemas.input import StructuredInput
from app.schemas.intent import Intent
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
    analysis = analyze_problem(state.structured_input, profile, state.input.topic_hint)
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

    False whenever `select_route` would send this turn to "clarify" (nothing
    usable to search with yet: no/empty structured input, no/low-confidence
    intent, or a plan that already decided to clarify -- the same checks
    `select_route` makes, reused here rather than duplicated) or when this is
    a pure runtime-error debugging turn (the debugger works from the
    traceback itself, not pattern docs). Otherwise True: every DSA/explain
    intent, plus the other debug-route intents (`ERROR_EXPLANATION`,
    `TEST_CASE_ANALYSIS`), retrieve.
    """
    intent = state.intent
    structured = state.structured_input
    return select_route(state) != "clarify" and not (
        intent is not None
        and structured is not None
        and intent.intent == Intent.CODE_DEBUG
        and structured.error
    )


def build_retrieval_query(state: AgentState) -> str:
    """Build the retrieval query text from trusted-shape signal fields only.

    Joins (newline-separated) the plan's topic (`_` -> space), the question,
    and the problem statement -- never `code` or `error` text, which is far
    more likely to swamp a short knowledge-corpus query with noise. Duplicate
    parts (e.g. the topic verbatim inside the question) are deduped,
    order-preserving, so BM25 doesn't over-weight the repeated text. May
    return "" if none of those are present.
    """
    parts: list[str] = []
    plan = state.plan
    if plan is not None and plan.topic:
        parts.append(plan.topic.replace("_", " "))
    structured = state.structured_input
    if structured is not None:
        if structured.question:
            parts.append(structured.question)
        if structured.problem:
            parts.append(structured.problem)
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
    """
    ctx = runtime.context
    progress = await resolve_hint_progress(state, ctx)
    run = await run_dsa(state, runtime, progress=progress)
    update: AgentStateUpdate = {
        "agent_output": run.result.to_outcome(),
        "agent_result": run.result,
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


async def debug_agent(state: AgentState, runtime: Runtime[GraphContext]) -> AgentStateUpdate:
    """Run the debugger subgraph for this turn.

    `extract_test_suite` derives a fallback `TestSuite` from the learner's own
    problem statement + code (deterministic, no LLM) so a real debug turn can
    still verify a fix even when nothing earlier in the graph populated
    `state.execution_request.tests` -- that still wins when present.
    """
    tests = extract_test_suite(state.structured_input)
    run = await run_debug(state, runtime, tests=tests)
    update: AgentStateUpdate = {
        "agent_output": run.result.to_outcome(),
        "agent_result": run.result,
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
        tests = extract_test_suite(state.structured_input)
        review_run = await review_code(state, runtime, tests=tests)
        update: AgentStateUpdate = {
            "agent_output": review_run.result.to_outcome(),
            "agent_result": review_run.result,
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


def _event_topic(plan: TeachingPlan | None, agent_output: AgentOutcome) -> str | None:
    return agent_output.topic or (plan.topic if plan is not None else None)


def _build_learning_event(state: AgentState, ctx: GraphContext, topic: str) -> LearningEventCreate:
    agent_output = state.agent_output
    intent_result = state.intent
    if agent_output is None or intent_result is None:
        # Callers only reach here after confirming both are set; this is a
        # future-proofing invariant, not expected control flow.
        raise ValueError("agent_output and intent are required to build a learning event")

    structured = state.structured_input
    problem = (structured.problem or structured.question) if structured is not None else None

    return LearningEventCreate(
        conversation_id=ctx.conversation_id,
        intent=intent_result.intent,
        problem=problem,
        topic=topic,
        pattern=agent_output.pattern,
        difficulty=state.plan.difficulty if state.plan is not None else None,
        requested_help=requested_help_for(intent_result.intent),
        hints_used=agent_output.hints_used,
        needed_full_solution=agent_output.needed_full_solution,
        errors=agent_output.errors,
        solved=agent_output.solved if agent_output.solved is not None else False,
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

    A learning event is only ever *saved* (see `_persist_event`) when the
    agent reported an observed outcome (`agent_output.solved is not None`)
    and a session/user/topic are all available -- a stub's `solved=None`
    guess is surfaced via `events` for `/chat` to see, but never written to
    the profile. Conversation turns are only saved when `conversation_id` is
    set. Never commits: the caller owns the transaction.
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
            topic = _event_topic(state.plan, agent_output)
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

            can_persist = (
                agent_output.solved is not None
                and ctx.session is not None
                and ctx.user_id is not None
            )
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
