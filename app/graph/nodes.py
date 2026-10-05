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
from functools import cache
from types import MappingProxyType
from typing import Final, Protocol, cast
from uuid import UUID

from langgraph.runtime import Runtime

from app.agents.concept import (
    answer_concept,
    curriculum_references,
    pattern_chunks,
    turn_references,
)
from app.agents.debugger import extract_learner_code
from app.agents.hint_engine import HintProgress
from app.agents.planner import (
    INTENT_DEFAULTS,
    MIN_RETRIEVAL_TOPIC_SCORE,
    TopicSource,
    analyze_problem,
    build_plan,
    clamp_assistance,
    explicit_ask_phrase,
)
from app.agents.practice import render_practice_problem, select_session_problem
from app.agents.reviewer import review_code
from app.execution.synth import synthesize_test_suite, verified_reference
from app.execution.testgen import extract_test_suite
from app.execution.verification import verify as verify_result
from app.graph.routing import asks_for_guidance, meta_followup, select_route
from app.graph.state import (
    AgentOutcome,
    AgentState,
    AgentStateUpdate,
    GraphContext,
    NodeError,
    SuiteSource,
)
from app.graph.subgraphs.debug import run_debug
from app.graph.subgraphs.dsa import (
    corpus_chunks_by_pattern,
    run_dsa,
)
from app.graph.subgraphs.explain import run_explain
from app.input.intent import classify_intent as _classify_intent_llm
from app.input.intent import is_small_talk
from app.input.normalize import merge_inputs, normalize_text
from app.input.vision import ImageValidationError, extract_from_image
from app.knowledge.ingest import load_corpus
from app.llm.base import LLMError
from app.memory.conversation import (
    add_turn,
    get_active_problem,
    get_recent_context,
    get_tutoring_state,
    set_active_problem,
    set_tutoring_state,
)
from app.memory.events import record_event, requested_help_for
from app.memory.hint_progress import get_hint_progress, get_latest_hint_progress, save_hint_progress
from app.memory.profile import FAMILY_PREFIX, PRIOR, apply_event, get_profile
from app.response.format import SAFE_FALLBACK_RESPONSE, SECTION_TITLES, protect_symbols
from app.response.generate import generate_response
from app.schemas.agent_results import DSAResult, ExplainResult, HintLevel
from app.schemas.event import LearningEventCreate, slug_tag
from app.schemas.execution import ExecutionResult, HarnessError, TestSuite, Verdict
from app.schemas.input import ActiveProblem, CodeBlock, ProblemRelation, StructuredInput
from app.schemas.intent import Intent, IntentResult
from app.schemas.knowledge import RetrievalHit
from app.schemas.plan import ASSISTANCE_ORDER, TeachingPlan
from app.schemas.profile import LearnerProfileView
from app.schemas.response import GeneratedResponse, ResponseSection, ResponseSectionKind
from app.schemas.tutoring import (
    ExecutionView,
    PendingCheck,
    PendingView,
    PracticeRecord,
    SessionProgress,
    TutoringView,
)
from app.tutoring.bank import (
    chain_intro,
    chain_start,
    curated_problem,
    curated_problem_for_text,
    curated_problems,
    family_patterns,
    named_pattern,
)
from app.tutoring.grader import asks_for_help, grade_reply, is_dont_know
from app.tutoring.misconceptions import detect_in_code, get_misconception, is_catalog_id
from app.tutoring.progression import requested_difficulty, session_difficulty
from app.tutoring.turn import (
    execution_lines,
    next_progress,
    question_for_turn,
    react,
    surfaced_misconceptions,
    tutoring_sections,
)

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
    "grade_answer",
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

    active_problem: ActiveProblem | None = None
    try:
        async with ctx.session.begin_nested():
            active_problem = await get_active_problem(ctx.session, ctx.user_id, ctx.conversation_id)
    except Exception as exc:
        errors.append(
            NodeError(
                node="load_learner_profile",
                error_type=type(exc).__name__,
                message=_RECENT_CONTEXT_FAILED_MESSAGE,
            )
        )

    pending: PendingCheck | None = None
    progress = SessionProgress.empty()
    try:
        async with ctx.session.begin_nested():
            pending, progress = await get_tutoring_state(
                ctx.session, ctx.user_id, ctx.conversation_id
            )
    except Exception as exc:
        errors.append(
            NodeError(
                node="load_learner_profile",
                error_type=type(exc).__name__,
                message=_RECENT_CONTEXT_FAILED_MESSAGE,
            )
        )

    update = {
        "profile": profile,
        "recent_context": recent_context,
        "active_problem": active_problem,
        "pending_check": pending,
        "session_progress": progress,
    }
    if errors:
        update["errors"] = errors
    return update


def _load_learner_profile_fallback(state: AgentState) -> AgentStateUpdate:
    del state
    return {"profile": LearnerProfileView.empty(), "recent_context": []}


# --- plan_teaching ------------------------------------------------------------


async def plan_teaching(state: AgentState, runtime: Runtime[GraphContext]) -> AgentStateUpdate:
    """Build this turn's `TeachingPlan` from the classified intent and profile.

    `hint_progress` is read here -- via `resolve_hint_progress` called with
    `topic=analysis.topic` (before `plan`, and therefore `plan.topic`, exists
    yet) -- so `build_plan`'s escalation rule can see this problem's
    hint-ladder ceiling and "demonstrated effort" history for THIS turn's
    plan, not just future ones. See `resolve_hint_progress` for why passing
    `analysis.topic` in resolves to exactly the same row `dsa_agent` writes
    (it becomes `plan.topic` verbatim).

    `analysis.topic_source` is threaded onto `AgentState.topic_source` here
    too (see `_topic_is_stable`), so later nodes that write the hint-ladder
    store (`dsa_agent`, `_record_verified_attempt`) can key it exactly as
    this read did, without needing the raw `ProblemAnalysis` themselves.
    """
    ctx = runtime.context
    profile = state.profile if state.profile is not None else LearnerProfileView.empty()
    active = state.active_problem
    inherited_topic = (
        active.topic
        if active is not None and state.problem_relation in ("followup", "same")
        else None
    )
    analysis = analyze_problem(
        state.structured_input,
        profile,
        state.input.topic_hint,
        context=state.retrieved_context,
        inherited_topic=inherited_topic,
    )
    hint_progress = await resolve_hint_progress(
        state,
        ctx,
        topic=analysis.topic,
        topic_is_stable=_topic_is_stable(analysis.topic_source),
    )
    plan = build_plan(
        state.intent,
        profile,
        analysis,
        hint_progress=hint_progress,
        structured_input=state.structured_input,
        teaching_mode=state.input.teaching_mode,
    )
    plan = _apply_scaffold_floor(plan, state)
    plan = clamp_assistance(plan, state.input.assistance_cap)
    return {"plan": plan, "topic_source": analysis.topic_source}


def _apply_scaffold_floor(plan: TeachingPlan, state: AgentState) -> TeachingPlan:
    """Keep the extra help a "don't know" earned on this problem (ADAPTIVE-tutoring G5).

    `session_progress.assistance_floor` holds the level a graded "don't know"
    raised this problem (or topic) to. A later turn on it starts there instead
    of dropping back to the planner's default -- never above `partial` (the
    full solution stays governed by the P4 escalation policy), and never
    lowering an already-higher plan.
    """
    progress = state.session_progress
    if progress is None or not progress.assistance_floor:
        return plan
    key = state.problem_key or plan.topic
    floor = progress.assistance_floor.get(key) if key is not None else None
    if floor is None:
        return plan
    order = ASSISTANCE_ORDER
    if order.index(floor) <= order.index(plan.assistance_level):
        return plan
    return plan.model_copy(
        update={"assistance_level": floor, "rationale": [*plan.rationale, "scaffold_floor"]}
    )


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

    It first settles WHICH problem this turn is about (ADAPTIVE-upgrade P1,
    `_problem_update`, pure and DB-free so the fallback below keeps it even
    when retrieval fails). A follow-up retrieves against the conversation's
    active problem, so its corpus context (hint grounding, citations) is about
    that problem, not about the words "give full answer"; that retrieval skips
    the intent gate, since a bare follow-up often classifies low-confidence.
    """
    update = _problem_update(state)
    effective = state.model_copy(update=dict(update))
    retriever = runtime.context.retriever
    followup = update.get("problem_relation") == "followup"
    if retriever is None or not (followup or should_retrieve(effective)):
        return {**update, "retrieved_context": []}
    query = build_retrieval_query(effective)
    if not query:
        return {**update, "retrieved_context": []}
    hits = await retriever.retrieve(query, runtime.context.knowledge_top_k)
    return {**update, "retrieved_context": hits}


def _code_submission_update(state: AgentState) -> AgentStateUpdate | None:
    """A reply to the agent's pending CODE request, attached to the active problem.

    P1 never lets bare code inherit the active problem (a wrong statement must
    not judge unrelated code). Here the agent itself asked for THIS problem's
    implementation (ADAPTIVE-tutoring G4), so the next code-only message is
    reviewed against it: the stored statement (whose worked examples become
    the sandbox suite) plus this turn's code and question.
    """
    pending = state.pending_check
    inp = state.structured_input
    active = state.active_problem
    if pending is None or pending.kind != "code_submission" or inp is None or active is None:
        return None
    if not inp.code or inp.problem:
        return None
    attached = active.problem.model_copy(
        update={
            "code": list(inp.code),
            "question": inp.question,
            "error": inp.error,
            "language": inp.language or active.problem.language,
        }
    )
    update: AgentStateUpdate = {
        "problem_relation": "same",
        "problem_key": active.key,
        "structured_input": attached,
        "submitted_code": True,
    }
    if state.intent is None or state.intent.intent not in (
        Intent.CODE_DEBUG,
        Intent.CODE_REVIEW,
        Intent.TEST_CASE_ANALYSIS,
        Intent.ERROR_EXPLANATION,
    ):
        update["intent"] = IntentResult(
            intent=Intent.CODE_DEBUG,
            confidence=0.8,
            source="rule",
            rationale="code submitted for the agent's pending implementation request",
        )
    return update


def _problem_update(state: AgentState) -> AgentStateUpdate:
    """This turn's relation to the active problem, its ladder key, and -- for a
    follow-up -- the structured input re-anchored on the active statement."""
    submission = _code_submission_update(state)
    if submission is not None:
        return submission
    relation, key = resolve_problem_relation(
        state.structured_input,
        state.active_problem,
        state.intent.intent if state.intent is not None else None,
        answering=_answering_the_tutor(state),
    )
    update: AgentStateUpdate = {"problem_relation": relation, "problem_key": key}
    if relation == "followup" and state.active_problem is not None:
        update["structured_input"] = inherit_active_problem(
            state.structured_input, state.active_problem
        )
        question = state.structured_input.question if state.structured_input else None
        if _needs_solution_intent(state, question):
            # "give code for that" on the active problem is an explicit ask for
            # ITS solution; the classifier, seeing four words and no problem,
            # called it a low-confidence concept question and the turn went to
            # `clarify` (P4, T9). A fixed phrase match on a follow-up with a
            # known problem is unambiguous, so it is classified deterministically.
            update["intent"] = IntentResult(
                intent=Intent.DSA_SOLVE,
                confidence=0.8,
                source="rule",
                rationale="explicit solution ask on the conversation's active problem",
            )
        elif _continues_active_problem(state, question):
            # "I don't know", or a turn the classifier filed under its catch-all
            # GENERAL_GUIDANCE without any plan/advice ask: the learner is still
            # on the conversation's problem, so the tutor takes the next step on
            # it (one rung more help) instead of a study plan or "could you
            # confirm?" (regression: LeetCode 678 screenshot, turns 2-4).
            update["intent"] = IntentResult(
                intent=Intent.DSA_HINT,
                confidence=0.7,
                source="rule",
                rationale="follow-up on the conversation's active problem",
            )
    return update


def _continues_active_problem(state: AgentState, question: str | None) -> bool:
    if not question or meta_followup(state) is not None:
        return False
    if is_dont_know(question):
        return True
    if _answering_the_tutor(state) and not asks_for_help(question):
        # A reply to the tutor's own question about this problem ("two
        # pointers?") is an answer to weigh, whatever the classifier called it:
        # labelled CONCEPT_EXPLANATION it got a lecture on two pointers instead
        # of the next step on the binary-search problem it was answering.
        return True
    intent = state.intent
    return intent is not None and intent.intent is Intent.GENERAL_GUIDANCE


def _answering_the_tutor(state: AgentState) -> bool:
    """The tutor's last message ended on a question about the active problem
    and nothing is formally pending (a pending check is graded instead), so
    this turn is most likely the learner's answer to it."""
    if state.active_problem is None or state.pending_check is not None:
        return False
    last = state.recent_context[-1] if state.recent_context else None
    return last is not None and last.role == "assistant" and last.content.rstrip().endswith("?")


def _needs_solution_intent(state: AgentState, question: str | None) -> bool:
    intent = state.intent
    if question is None or not explicit_ask_phrase(question):
        return False
    # Only override a classification that is missing or unsure: a confident
    # CODE_EXPLAIN / CONCEPT reading of the same words stands (code review P4).
    return intent is None or intent.low_confidence


def _normalized_statement(text: str) -> str:
    return " ".join(text.lower().split())


#: How much text a re-paste may add around the stored statement and still be
#: the same problem: room for a direct ask ("give full code"), not for a
#: variant with extra constraints (which must start its own ladder).
_REPASTE_SLACK_CHARS: Final = 60


def _is_repaste(new: str, old: str) -> bool:
    """`new` is `old` again, give or take a short ask the normalizer left inside."""
    new_text, old_text = _normalized_statement(new), _normalized_statement(old)
    shorter, longer = sorted((new_text, old_text), key=len)
    return shorter in longer and len(longer) - len(shorter) <= _REPASTE_SLACK_CHARS


@cache
def _corpus_term_re() -> re.Pattern[str]:
    """Word-boundary matcher for the curated corpus's own vocabulary.

    Pattern names, titles, topics and aliases from `app/knowledge/corpus` --
    trusted, closed vocabulary (plus naive singulars: "heaps" -> "heap").
    Used only to decide whether a short turn names a subject of its own.
    """
    terms: set[str] = set()
    for doc in load_corpus():
        for raw in (doc.pattern, doc.topic, doc.title, *doc.aliases):
            term = raw.replace("_", " ").replace("-", " ").strip().lower()
            if len(term) >= 3:
                terms.add(term)
                if term.endswith("s") and len(term) > 4:
                    terms.add(term[:-1])
    alternation = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
    return re.compile(rf"\b(?:{alternation})\b")


def names_corpus_subject(text: str | None) -> bool:
    """Whether `text` names a DSA subject from the corpus vocabulary."""
    if not text:
        return False
    normalized = text.lower().replace("_", " ").replace("-", " ")
    return _corpus_term_re().search(normalized) is not None


def problem_key(structured_input: StructuredInput | None) -> str | None:
    """Hint-ladder key for the problem STATEMENT this turn carries, or `None`.

    Unlike `_problem_fingerprint`, the direct-ask line (`question`) is left
    out: "<statement> + give full code" must resolve to the same ladder as
    "<statement>" alone (F3: a re-paste reset the ladder to Hint 1). A hash
    of untrusted text, never the text -- never rendered, logged or returned.
    """
    if structured_input is None or not structured_input.problem:
        return None
    normalized = " ".join(structured_input.problem.lower().split())
    return "_p" + hashlib.sha256(normalized.encode()).hexdigest()[:16]


_TITLE_NUMBER_RE: Final = re.compile(r"^\s*(?:problem\s*)?#?\d+\s*[.):-]\s*", re.IGNORECASE)
_TITLE_MARKUP_RE: Final = re.compile(r"[*_`#\[\]<>|]")
_MAX_TITLE_CHARS: Final = 80


def problem_title(active: ActiveProblem | None) -> str | None:
    """The stored problem's title: the first line of its statement.

    A LeetCode page or screenshot leads with "678. Valid Parenthesis String";
    the vision prompt keeps that line. Display-only learner data, stripped of
    markdown so it cannot restyle the reply; an over-long first line is prose,
    not a title, and is cut.
    """
    if active is None or not active.problem.problem:
        return None
    for line in active.problem.problem.splitlines():
        cleaned = " ".join(_TITLE_MARKUP_RE.sub("", line).split())
        if cleaned:
            if len(cleaned) > _MAX_TITLE_CHARS:
                return cleaned[:_MAX_TITLE_CHARS].rstrip() + "..."
            return cleaned
    return None


def _names_active_problem(question: str | None, active: ActiveProblem) -> bool:
    """`question` names the active problem by its title ("... solve the valid
    parenthesis string problem"). Checked before the corpus vocabulary, which
    would otherwise read the same words as a subject of the turn's own."""
    title = problem_title(active)
    if not question or title is None or title.endswith("..."):
        return False
    name = _TITLE_NUMBER_RE.sub("", title).lower()
    return len(name.split()) >= 2 and name in " ".join(question.lower().split())


def _is_non_problem_turn(intent: Intent | None, question: str | None) -> bool:
    """A turn that is never about the conversation's active problem: a request
    for a NEW practice problem, an explicit plan/advice ask, or a bare greeting.

    GENERAL_GUIDANCE alone does not qualify: it is the classifier's catch-all,
    so "tell me name of that problem" carried it and lost the problem.
    """
    if intent is Intent.PRACTICE_REQUEST:
        return True
    if intent is Intent.GENERAL_GUIDANCE:
        if question and is_dont_know(question):
            return False  # "I don't know where to start" is about the problem
        return asks_for_guidance(question) or is_small_talk(question)
    return False


def resolve_problem_relation(
    structured_input: StructuredInput | None,
    active: ActiveProblem | None,
    intent: Intent | None = None,
    *,
    answering: bool = False,
) -> tuple[ProblemRelation, str | None]:
    """How this turn relates to the conversation's active problem, and its ladder key.

    - A turn carrying a statement is "same" when it is the active statement --
      equal after normalization, or one contains the other (a re-paste with a
      trailing "give full code" that normalization kept inside the statement)
      -- else "new".
    - A turn with no statement AND no code is a "followup" when there is an
      active problem and the turn names no corpus subject of its own ("give
      full answer", "next hint", "why does that work?"), versus "what is a
      trie?", which is its own question. Deliberately NOT decided by
      retrieval score: measured, "give full answer" retrieves `heaps` at
      -4.79 (above the -5.0 topic floor) and "hi" retrieves `binary_search`
      at +3.85, so a score floor cannot tell a follow-up from a question.
    - Code or an error without a statement never inherits: attaching the
      active problem would let a wrong statement judge unrelated code, and
      would drop a pasted traceback the debugger needs.
    """
    if structured_input is None:
        return "none", None
    key = problem_key(structured_input)
    if key is not None and structured_input.problem is not None:
        if active is not None and active.problem.problem:
            if active.key == key:
                return "same", active.key
            if _is_repaste(structured_input.problem, active.problem.problem):
                return "same", active.key
        return "new", key
    if active is None or structured_input.code or structured_input.error:
        return "none", None
    if _is_non_problem_turn(intent, structured_input.question):
        return "none", None
    if _names_active_problem(structured_input.question, active):
        return "followup", active.key
    question = structured_input.question
    if answering and question and not asks_for_help(question):
        # Naming a technique in reply to the tutor's question IS the answer,
        # not a new subject ("two pointers?" after "what would you try?").
        return "followup", active.key
    if names_corpus_subject(question):
        return "none", None
    return "followup", active.key


def inherit_active_problem(
    structured_input: StructuredInput | None, active: ActiveProblem
) -> StructuredInput:
    """This follow-up turn, re-anchored on the active problem's statement.

    Keeps this turn's own question (the follow-up ask) and takes the problem
    statement and constraints from the stored problem. Both halves are
    untrusted learner data and stay in the untrusted slots they came from.
    Stored code is NOT carried over, so a "next hint" never re-runs a stale
    attempt in the sandbox.
    """
    question = structured_input.question if structured_input is not None else None
    language = structured_input.language if structured_input is not None else None
    return active.problem.model_copy(
        update={
            "question": question,
            "code": [],
            "error": None,
            "language": language or active.problem.language,
        }
    )


def _retrieve_knowledge_fallback(state: AgentState) -> AgentStateUpdate:
    # Retrieval failed, but which problem this turn is about does not depend on
    # it: keep continuity (relation, ladder key, inherited statement).
    return {**_problem_update(state), "retrieved_context": []}


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


def _topic_is_stable(topic_source: TopicSource | None) -> bool:
    """Whether this turn's inferred topic is a deliberate-enough signal to
    fork/select a bare follow-up's ladder on, vs merely a turn-local guess.

    `topic_source` is `app.agents.planner.ProblemAnalysis.topic_source`,
    threaded onto `AgentState` by `plan_teaching`. Only `"retrieval"` is
    unstable: it comes from querying the knowledge corpus with THIS turn's
    own prose alone (see `_hint_topic_key`), which for a follow-up with no
    problem statement of its own is often thin, generic, problem-agnostic
    text ("walk me through the approach") -- a guess that can legitimately
    differ from one such follow-up to the next even within one conversation
    about one problem. `"hint"` (an explicit client-supplied topic) and
    `"profile_match"` (the learner's own established vocabulary appearing in
    their own prose) are both deliberate signals, trusted as before. `None`
    (a state built without ever going through `analyze_problem`, e.g. by an
    older test/caller) is also treated as stable -- the historical,
    trusted-topic behaviour -- so this only ever downgrades a turn actually
    run through `analyze_problem` and explicitly identified as `"retrieval"`.
    """
    return topic_source != "retrieval"


async def _latest_conversation_topic(ctx: GraphContext) -> str | None:
    """This `(user_id, conversation_id)`'s most recently updated ladder topic, if any.

    Degrades silently to `None` (never raises) on any failure or missing
    `ctx` dependency -- a history lookup must never cost the learner their
    turn.
    """
    if ctx.session is None or ctx.user_id is None or ctx.conversation_id is None:
        return None
    session, user_id, conversation_id = ctx.session, ctx.user_id, ctx.conversation_id
    try:
        async with session.begin_nested():
            latest = await get_latest_hint_progress(session, user_id, conversation_id)
        if latest is not None:
            return latest.topic
    except Exception:
        pass
    return None


async def _hint_topic_key(
    structured_input: StructuredInput | None,
    topic: str | None,
    ctx: GraphContext,
    *,
    topic_is_stable: bool = True,
    problem_key: str | None = None,
) -> str:
    """Return the hint-ladder store key for this turn.

    0. `problem_key` (ADAPTIVE-upgrade P1) wins whenever this turn is about a
       known problem statement -- a new one, a re-paste, or a follow-up on the
       conversation's active one. One ladder per PROBLEM, not per topic: two
       different trees problems in one conversation used to share (and
       corrupt) one `trees` ladder (F3). Everything below is the pre-P1
       resolution, still used for turns with no problem in play.

    `topic` is whatever topic this turn has already resolved to -- normally
    `plan.topic`, but `plan_teaching` also calls this (via
    `resolve_hint_progress`) with `analysis.topic` *before* `plan` exists,
    since `build_plan` sets `plan.topic = analysis.topic` verbatim, so the
    two are always the same value once the plan is built.

    Resolution order:
    1. `structured_input` carries no problem statement of its own (a bare
       follow-up: "Give me the next hint", "Walk me through the approach")
       AND `topic_is_stable` is `False` (see `_topic_is_stable`) -> the
       conversation's existing ladder, if any, continues -- `topic` is not
       consulted at all. Trusting an unstable, turn-local topic guess to key
       the ladder here (the old behaviour: `topic` was checked first,
       unconditionally, regardless of source) forked a fresh, empty ladder
       every time it happened to differ, silently discarding all prior
       progress and permanently keeping `HintProgress.last_level` from ever
       reaching the ceiling `app.agents.planner.build_plan`'s escalation
       rule checks for -- measured live: 6+ consecutive "walk me through"
       turns all denied via `escalation_denied_ceiling_not_reached`, never
       advancing. No existing ladder yet falls through to step 2.
    2. `topic` is set -> `slug_tag(topic)`. This is still the common case:
       reached whenever this turn carries its own (new or restated) problem
       statement, whenever `topic_is_stable` is `True` (an explicit
       `topic_hint` or a profile-vocabulary match -- deliberate signals, not
       a guess), and as the fallback when step 1 found no existing ladder.
    3. No topic, but `structured_input` carries a problem statement (see
       `_problem_fingerprint`) -> a fingerprint of that problem, so a
       *different* problem in the same topic-less conversation never shares
       a ladder with an unrelated one, and the *same* problem restated
       climbs the one ladder it belongs to.
    4. No topic and no problem statement -> the most recently updated
       hint-ladder row for this `(user_id, conversation_id)` (this repeats
       step 1's lookup when it wasn't already tried there).
    5. Nothing stored yet either -> `DEFAULT_HINT_TOPIC`, a fresh ladder.

    Shared by `resolve_hint_progress` (the read) and `dsa_agent`/
    `_record_verified_attempt` (the writes), called with the same
    `(structured_input, topic, topic_is_stable)` before any of them has
    written anything this turn, so all resolve identically and can never key
    differently.
    """
    if problem_key is not None:
        return problem_key
    has_problem = structured_input is not None and bool(structured_input.problem)
    unstable_followup = not has_problem and not topic_is_stable

    if unstable_followup:
        latest = await _latest_conversation_topic(ctx)
        if latest is not None:
            return latest

    if topic and not unstable_followup:
        return slug_tag(topic)

    if has_problem:
        fingerprint = _problem_fingerprint(structured_input)
        if fingerprint is not None:
            return fingerprint
    else:
        latest = await _latest_conversation_topic(ctx)
        if latest is not None:
            return latest

    if topic:
        return slug_tag(topic)

    return DEFAULT_HINT_TOPIC


async def resolve_hint_progress(
    state: AgentState,
    ctx: GraphContext,
    *,
    topic: str | None = None,
    topic_is_stable: bool | None = None,
) -> HintProgress:
    """Read this conversation+topic's hint-ladder progress from the dedicated store.

    `AgentState` has nowhere to persist `HintProgress` across turns, so this
    reads it from `app.memory.hint_progress` (a small store dedicated to hint
    ladder state, upserted by `dsa_agent` -- see that function's docstring for
    why this is deliberately *not* rebuilt from `LearningEvent`s).

    `topic`, if given, is used as-is (this is `plan_teaching` calling in with
    `analysis.topic` *before* `state.plan` exists yet, so it can feed
    `HintProgress` into `build_plan`'s escalation rule). When omitted, it is
    derived from `state.plan.topic` -- the historical behaviour, used by
    `dsa_agent`'s post-plan call. `topic_is_stable`, if omitted, is derived
    from `state.topic_source` via `_topic_is_stable` -- the same source
    `plan_teaching` threads onto `AgentState` alongside `plan.topic`, so a
    caller need not already have the raw `ProblemAnalysis` in scope. Either
    way this resolves through `_hint_topic_key`, the same key `dsa_agent`
    writes under, so every caller this turn agrees on one row.

    Degrades to a fresh `HintProgress()` (never raises) whenever `ctx.session`
    or `ctx.user_id` is `None`, or the DB lookup itself fails -- a history
    lookup must never cost the learner their turn. The read runs in its own
    savepoint, mirroring `load_learner_profile`, so a failure here can never
    leave the shared session's outer transaction aborted for later
    reads/writes in this turn.
    """
    if ctx.session is None or ctx.user_id is None or ctx.conversation_id is None:
        return HintProgress()
    if topic is None:
        topic = state.plan.topic if state.plan is not None else None
    if topic_is_stable is None:
        topic_is_stable = _topic_is_stable(state.topic_source)
    key = await _hint_topic_key(
        state.structured_input,
        topic,
        ctx,
        topic_is_stable=topic_is_stable,
        problem_key=state.problem_key,
    )

    try:
        async with ctx.session.begin_nested():
            return await get_hint_progress(ctx.session, ctx.user_id, ctx.conversation_id, key)
    except Exception:
        return HintProgress()


async def _anchored_ladder_topic(state: AgentState, ctx: GraphContext) -> str | None:
    """This turn's hint-ladder topic, anchored to the conversation's existing
    ladder when this turn's own `plan.topic` is `None` (Packet P5b).

    Calls `_hint_topic_key` with the exact same arguments `resolve_hint_progress`
    and `dsa_agent`'s own write already use (`state.plan.topic`,
    `_topic_is_stable(state.topic_source)`), so this always resolves to the
    same row -- it just also hands the key back, rather than discarding it
    after the DB lookup, so `run_dsa` can use it as `next_hint`'s topic
    fallback (see `app.agents.hint_engine.next_hint`'s docstring).

    Returns `None` when the resolved key is not an actual topic slug --
    `DEFAULT_HINT_TOPIC` (no topic ever inferred on this conversation, nothing
    to anchor to) or a problem-statement fingerprint (`_problem_fingerprint`'s
    `_q...` keys, which key a topic-less problem's ladder by hashed text, not
    by pattern name) -- since neither is safe to show a learner as "the
    pattern for this problem".
    """
    topic_plan = state.plan.topic if state.plan is not None else None
    key = await _hint_topic_key(
        state.structured_input,
        topic_plan,
        ctx,
        topic_is_stable=_topic_is_stable(state.topic_source),
        problem_key=state.problem_key,
    )
    if key == DEFAULT_HINT_TOPIC or key.startswith(("_q", "_p")):
        return None
    return key


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

    `has_verified_attempt` is likewise carried forward from the progress read
    at the start of this turn, OR'd with whether this turn's own
    `initial_verdict` came back `pass`/`fail` (never `inconclusive`/
    `skipped`) -- monotonic, like `solved`, but a distinct signal (see
    `HintProgress.has_verified_attempt`): it is the "demonstrated effort"
    input `app.agents.planner.build_plan` reads back, via `plan_teaching`'s
    own `resolve_hint_progress` call on a *later* turn, to decide whether an
    explicit ask for the full solution may ever be granted.
    """
    ctx = runtime.context
    progress = await resolve_hint_progress(state, ctx)
    if extract_learner_code(state.structured_input) is not None:
        tests, suite_source = await _resolve_test_suite(state, runtime)
    else:
        tests, suite_source = None, "none"
    ladder_topic = await _anchored_ladder_topic(state, ctx)
    # AD-4: a granted escalation reveals ONLY a sandbox-verified reference
    # solution. If none can be verified this turn, nothing is revealed.
    solution = None
    if state.plan is not None and state.plan.assistance_level == "full" and not progress.solved:
        solution = await verified_reference(state.structured_input, ctx.llm, ctx.runner)
    run = await run_dsa(
        state,
        runtime,
        progress=progress,
        tests=tests,
        ladder_topic=ladder_topic,
        solution=solution,
        reveal_requested=state.plan is not None and state.plan.assistance_level == "full",
    )
    update: AgentStateUpdate = {
        "agent_output": run.result.to_outcome(),
        "agent_result": run.result,
        "suite_source": suite_source,
    }
    if run.execution_request is not None:
        update["execution_request"] = run.execution_request
    if run.plan is not None:
        # The solver corrected a guessed topic: the rest of the turn (the
        # pattern card, the learning event, the stored active problem) uses it.
        update["plan"] = run.plan
        update["topic_source"] = "retrieval"

    verdict = run.result.initial_verdict
    ran_this_turn = verdict is not None and verdict.status in ("pass", "fail")
    has_verified_attempt = progress.has_verified_attempt or ran_this_turn

    if (
        run.result.hint is not None
        and ctx.session is not None
        and ctx.user_id is not None
        and ctx.conversation_id is not None
    ):
        topic_plan = state.plan.topic if state.plan is not None else None
        topic = await _hint_topic_key(
            state.structured_input,
            topic_plan,
            ctx,
            topic_is_stable=_topic_is_stable(state.topic_source),
            problem_key=state.problem_key,
        )
        try:
            async with ctx.session.begin_nested():
                await save_hint_progress(
                    ctx.session,
                    ctx.user_id,
                    ctx.conversation_id,
                    topic,
                    level=int(run.result.hint.level),
                    solved=progress.solved,
                    has_verified_attempt=has_verified_attempt,
                    asks_at_ceiling=progress.asks_at_ceiling + _refused_ask_at_ceiling(state),
                    # A client cap lowers THIS turn only; never let it become
                    # the ladder's permanent N (stored once, never updated).
                    ceiling=(
                        None
                        if state.plan is not None and "assistance_capped" in state.plan.rationale
                        else int(run.result.hint.ceiling)
                    ),
                )
        except Exception:
            pass

    return update


def _refused_ask_at_ceiling(state: AgentState) -> int:
    """1 when this turn explicitly asked for the solution at the ceiling and the
    plan still refused it (P4: Balanced reveals on the second such ask)."""
    plan = state.plan
    if plan is None or "escalated" in plan.rationale:
        return 0
    refused_for_effort = "escalation_denied_no_verified_attempt" in plan.rationale
    asked_at_ceiling = (
        "escalation_denied_ceiling_not_reached" not in plan.rationale
        and "escalation_denied_no_explicit_ask" not in plan.rationale
    )
    return 1 if refused_for_effort and asked_at_ceiling else 0


async def _record_verified_attempt(
    state: AgentState, ctx: GraphContext, verdict: Verdict | None
) -> None:
    """Mark this problem's hint-ladder row as having a demonstrated attempt.

    `app.agents.planner.build_plan`'s Packet P3 escalation rule reads
    `HintProgress.has_verified_attempt` as its "demonstrated effort" evidence,
    but historically only `dsa_agent` ever wrote it. A learner whose
    sandbox-verified submission got classified `CODE_DEBUG`/`CODE_REVIEW` --
    the ordinary shape for "here's my code, it's failing" -- had that effort
    recorded nowhere, so a later, genuine "just give me the answer" turn on
    the SAME problem could never see it (measured live: a `pass` verdict on
    turn 2, then four straight `escalation_denied_...` turns that could never
    have been anything but denied). `debug_agent` and `explain_agent`'s
    review branch now call this after their own subgraph runs, passing
    whichever field is verified sandbox ground truth on the LEARNER'S OWN
    submitted code for that route -- `DebugResult.initial_verdict` (the
    pre-patch run) and `ReviewResult.correctness_verdict` respectively --
    never an agent-patched or solution-preview verdict, which would credit
    the agent's own work as the learner's.

    Keyed by the same `_hint_topic_key` the DSA route reads and writes, so a
    later DSA turn on this problem sees exactly the same row. A no-op (never
    raises) whenever `verdict` is `None`/`inconclusive`/`skipped`,
    `ctx.session`/`ctx.user_id`/`ctx.conversation_id` is missing, the row
    already has `has_verified_attempt=True` (monotonic -- nothing to add),
    or the read/write itself fails -- a failed write must never cost the
    learner their turn's response.
    """
    if verdict is None or verdict.status not in ("pass", "fail"):
        return
    if ctx.session is None or ctx.user_id is None or ctx.conversation_id is None:
        return
    topic_plan = state.plan.topic if state.plan is not None else None
    topic = await _hint_topic_key(
        state.structured_input,
        topic_plan,
        ctx,
        topic_is_stable=_topic_is_stable(state.topic_source),
        problem_key=state.problem_key,
    )
    try:
        async with ctx.session.begin_nested():
            progress = await get_hint_progress(ctx.session, ctx.user_id, ctx.conversation_id, topic)
            if progress.has_verified_attempt:
                return
            level = (
                int(progress.last_level)
                if progress.last_level is not None
                else int(HintLevel.L0_NUDGE)
            )
            await save_hint_progress(
                ctx.session,
                ctx.user_id,
                ctx.conversation_id,
                topic,
                level=level,
                solved=progress.solved,
                has_verified_attempt=True,
            )
    except Exception:
        pass


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

    Also records `run.result.initial_verdict` (the sandbox's verdict on the
    learner's OWN code, before any patch) as this problem's "demonstrated
    effort" evidence -- see `_record_verified_attempt` -- so a later DSA turn
    on the same problem can see it even though this turn was classified
    `CODE_DEBUG`/`ERROR_EXPLANATION`/`TEST_CASE_ANALYSIS`, not a DSA intent.
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
    await _record_verified_attempt(state, runtime.context, run.result.initial_verdict)
    return update


_NO_PRACTICE_TOPIC_TEXT: Final = (
    "Tell me which pattern you want to practise -- two pointers, sliding window, "
    "binary search, graphs, dynamic programming -- and I will pick a problem at the "
    "right level for you."
)


async def practice_agent(state: AgentState, runtime: Runtime[GraphContext]) -> AgentStateUpdate:
    """Hand the learner a practice problem from the corpus, at their level.

    Difficulty comes from `plan.difficulty`, which the planner already derived
    from this learner's skill in this topic via `difficulty_for` -- there is no
    second difficulty scale here. The problem itself is parsed out of the
    curated corpus (`app.agents.practice`), never generated, so it cannot be a
    problem that does not exist.

    `solved` is always `None`: asking for practice is exposure to a topic, not
    evidence about whether the learner can do it. The rendered problem is
    display data and nothing else -- see `app.agents.practice`'s module
    docstring for how that is preserved.

    ADAPTIVE-tutoring (G5, and the P1 gap the owner found live):
    - the difficulty honours an explicit ask ("an easy problem", "something
      harder") and this conversation's graded results
      (`app.tutoring.progression.session_difficulty`), on top of the
      skill-based `plan.difficulty`;
    - a request that names no subject stays on the conversation's last topic;
    - a family-level ask ("a hard graph problem") draws from the whole family,
      preferring problems with a curated, sandbox-testable statement;
    - the chosen problem is recorded on `state.practice`, and
      `update_learner_model` makes it the conversation's ACTIVE problem, so a
      follow-up "hint?" is about THIS problem.
    """
    del runtime
    plan = state.plan
    progress = state.session_progress or SessionProgress.empty()
    request_text = state.structured_input.question if state.structured_input is not None else None
    named = names_corpus_subject(request_text)
    topic = plan.topic if plan is not None else None
    if named:
        # The request's own words decide ("a hard graph problem" -> graphs);
        # the planner may not have inferred a topic from a request alone.
        topic = named_pattern(request_text) or topic
    elif progress.last_topic is not None:
        topic = progress.last_topic
    if topic is None:
        return {"agent_output": AgentOutcome(text=_NO_PRACTICE_TOPIC_TEXT, topic=None, solved=None)}
    base = plan.difficulty if plan is not None else "medium"
    difficulty, reason = session_difficulty(base, progress, requested_difficulty(request_text))
    practised = frozenset({progress.last_practice.title} if progress.last_practice else set[str]())
    problem = select_session_problem(
        topic,
        difficulty,
        corpus_chunks_by_pattern(),
        patterns=family_patterns(topic),
        preferred_titles=frozenset(p.title for p in curated_problems()),
        exclude_titles=practised,
    )
    if problem is None:
        return {
            "agent_output": AgentOutcome(text=_NO_PRACTICE_TOPIC_TEXT, topic=topic, solved=None)
        }
    curated = curated_problem(problem.title)
    statement = (
        curated.statement if curated is not None else f"Problem: {problem.title}\n\n{problem.url}"
    )
    record = PracticeRecord(
        title=problem.title,
        topic=problem.topic,
        difficulty=problem.difficulty,
        curated=curated is not None,
        reason=reason,
        statement=statement,
    )
    text = render_practice_problem(problem, curated.statement if curated is not None else None)
    lead = _progression_line(progress, reason, problem.difficulty)
    if lead:
        text = f"{lead}\n\n{text}"
    return {
        "practice": record,
        "agent_output": AgentOutcome(
            text=text,
            topic=problem.topic,
            solved=None,
            hints_used=0,
            needed_full_solution=False,
            errors=[],
        ),
    }


def _progression_line(progress: SessionProgress, reason: str, difficulty: str) -> str | None:
    """Why this problem's level, from THIS session's graded answers (G5).

    Built only from grade counts and the closed topic slugs of the questions
    answered -- never from anything the learner wrote.
    """
    graded = progress.grades[progress.grades_at_practice :]
    right = [g for g in graded if g.grade == "correct"]
    topics = sorted({g.topic.replace("_", " ") for g in right if g.topic})
    if reason in ("harder_after_success", "up_after_success") and right:
        about = f" on {', '.join(topics)}" if topics else ""
        return (
            f"Based on this session -- {len(right)} of your last {len(graded)} answers "
            f"correct{about} -- let's step up to **{difficulty}**."
        )
    if reason in ("same_after_struggle", "harder_denied_struggle"):
        return (
            f"The last problem was a stretch, so let's stay at **{difficulty}** and I'll "
            "give you more guidance along the way."
        )
    return None


async def grade_answer(state: AgentState, runtime: Runtime[GraphContext]) -> AgentStateUpdate:
    """Grade the learner's reply to the agent's pending question (ADAPTIVE-tutoring G1).

    The reply (`structured_input.question`, untrusted) is graded against the
    pending check (agent-authored, from the bank) by `app.tutoring.grader`:
    deterministic rules first, then the LLM judge with the reply as delimited
    data. The grade decides the move (`app.tutoring.turn.react`): advance,
    narrow, reframe, or -- on "don't know" -- raise assistance one step and,
    on a problem, hand the next rung to `dsa_agent` (`grade_after`).

    Never judges code and never sets `solved`: a conceptual grade is recorded
    as `concept_check` evidence by `update_learner_model` (G2).
    """
    pending = state.pending_check
    if pending is None or state.plan is None:
        return {"agent_output": AgentOutcome(text=_GENERIC_CLARIFY, topic=None, solved=None)}
    reply = state.structured_input.question if state.structured_input is not None else ""
    grade = await grade_reply(pending, reply or "", runtime.context.llm)
    progress = state.session_progress or SessionProgress.empty()
    reacted = react(
        pending,
        grade,
        mode=state.input.teaching_mode,
        cap=state.input.assistance_cap,
        progress=progress,
        has_active_problem=state.active_problem is not None,
    )
    topic = pending.topic or state.plan.topic
    plan = state.plan.model_copy(
        update={
            "topic": topic,
            "assistance_level": reacted.assistance_after,
            "rationale": [*state.plan.rationale, f"graded_{grade.grade}"],
        }
    )
    errors = [grade.misconception_id] if is_catalog_id(grade.misconception_id) else []
    return {
        "answer_grade": grade,
        "reaction": reacted.reaction,
        "grade_feedback": reacted.feedback,
        "grade_lesson": reacted.lesson,
        "assistance_before": reacted.assistance_before,
        "grade_handoff": reacted.handoff,
        "next_pending": reacted.next_pending,
        "plan": plan,
        "agent_output": AgentOutcome(
            text=reacted.feedback or "Thanks -- let's keep going.",
            topic=topic,
            solved=None,
            hints_used=0,
            needed_full_solution=False,
            errors=[e for e in errors if e is not None],
        ),
    }


_REVIEW_INTENTS: Final[frozenset[Intent]] = frozenset({Intent.CODE_REVIEW, Intent.OPTIMIZATION})
_CONCEPT_INTENTS: Final[frozenset[Intent]] = frozenset(
    {Intent.CONCEPT_EXPLANATION, Intent.GENERAL_GUIDANCE}
)


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
        # See `_record_verified_attempt`: `correctness_verdict` is sandbox
        # ground truth on the learner's own submitted code -- the reviewer
        # never patches it -- so a `CODE_REVIEW`/`OPTIMIZATION` turn's
        # verified attempt counts toward this problem's ladder too.
        await _record_verified_attempt(
            state, runtime.context, review_run.result.correctness_verdict
        )
        return update

    if (
        intent is not None
        and intent.intent in _CONCEPT_INTENTS
        and extract_learner_code(state.structured_input) is None
    ):
        # A concept question or general guidance with no code to explain:
        # answer it FROM the corpus and cite only what the answer used (P3, B1).
        result = await _concept_answer(state, runtime)
        if result.answer:
            return {"agent_output": result.to_outcome(), "agent_result": result}
        # Nothing to ground on (no relevant hit, no known topic): fall back to
        # the explainer rather than answering with nothing (code review P3).

    explain_run = await run_explain(state, runtime)
    update = {
        "agent_output": explain_run.result.to_outcome(),
        "agent_result": explain_run.result,
    }
    if explain_run.execution_request is not None:
        update["execution_request"] = explain_run.execution_request
    return update


async def _concept_answer(state: AgentState, runtime: Runtime[GraphContext]) -> ExplainResult:
    """A corpus-grounded answer for a concept question or general guidance.

    General guidance (a study plan) is grounded in the curriculum built from
    the corpus's own front matter; a concept question in the topic's own
    overview/intuition/recognition sections plus this turn's relevant hits.
    """
    intent = state.intent.intent if state.intent is not None else None
    if intent is Intent.GENERAL_GUIDANCE:
        references = list(curriculum_references())
    else:
        topic = state.plan.topic if state.plan is not None else None
        references = turn_references(
            state.retrieved_context,
            pattern_chunks(topic),
            ("overview", "core_intuition", "when_to_recognize_it", "complexity"),
        )
    answer = await answer_concept(
        state.structured_input,
        references,
        runtime.context.llm,
        guidance=intent is Intent.GENERAL_GUIDANCE,
    )
    return ExplainResult(answer=answer.answer or None, citations=answer.citations)


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

_IMAGE_UNREADABLE_REPLY: Final = (
    "I couldn't read the image you attached -- the image reader didn't respond this time. "
    "Could you try sending it again, or paste the problem statement as text?"
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
        Intent.PRACTICE_REQUEST: "practise on a new problem",
    }
)

#: A greeting / acknowledgement (low-confidence GENERAL_GUIDANCE) gets an
#: invitation, not "it looks like you might want to ..." (P2).
_GREETING_REPLY: Final = (
    "Hi! Share a problem statement, your code, or a concept you'd like to learn, "
    "and tell me whether you want a hint, a debugging walkthrough, an explanation, "
    "or a code review."
)


_META_NO_PROBLEM: Final = (
    "Yes, I can see everything we've said in this conversation -- but no problem has been "
    "shared in it yet. Paste the statement or a screenshot and we'll start from there."
)


def _meta_reply(state: AgentState) -> str:
    """Answer a question about the conversation itself from stored state.

    Built from the conversation's active problem (title only, see
    `problem_title`) and the question already pending -- no model call, so it
    cannot drift into a study plan or deny having the history it has.
    """
    active = state.active_problem
    title = problem_title(active)
    if active is None or title is None:
        return _META_NO_PROBLEM
    shared = "the screenshot you shared" if active.problem.source == "image" else "what you shared"
    if meta_followup(state) == "name":
        lead = f"That's **{title}** -- the problem from {shared} earlier in this conversation."
    else:
        lead = (
            "Yes -- I can see this whole conversation, including "
            f"{shared}. We're working on **{title}**."
        )
    pending = state.pending_check
    if pending is not None and pending.kind == "question":
        return f"{lead}\n\nBack to it: {pending.question}"
    return f"{lead}\n\nShall we keep going -- what would you try first on it?"


async def clarify(state: AgentState, runtime: Runtime[GraphContext]) -> AgentStateUpdate:
    """Ask a deterministic clarifying question, or answer a question about the
    conversation (`route == "meta"`); never echoes the learner's message."""
    del runtime
    if state.route == "meta":
        return {"agent_output": AgentOutcome(text=_meta_reply(state), topic=None, solved=None)}
    image_failed = any(
        err.node == "understand_input" and err.message == _IMAGE_EXTRACTION_FAILED_MESSAGE
        for err in state.errors
    )
    if image_failed:
        # Say WHY instead of a generic "could you confirm?": the learner sent a
        # screenshot and nothing in the reply admitted it was never read.
        text = _IMAGE_UNREADABLE_REPLY
    elif state.structured_input is None or state.structured_input.is_empty:
        text = _ASK_FOR_INPUT
    elif state.intent is not None and state.intent.intent not in _INTENT_PHRASES:
        text = _GREETING_REPLY
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

    The tutoring layer (`_with_tutoring`, also LLM-free) then adds this turn's
    grade feedback, misconception teaching, "Execution" lines and the ONE
    guiding question it ends with, and decides what is pending next.
    """
    del runtime
    graded_only = state.answer_grade is not None and not state.grade_handoff
    generated = generate_response(
        result=None if graded_only else state.agent_result,
        plan=state.plan,
        verification=state.verification,
        fallback_text=state.agent_output.text if state.agent_output is not None else None,
    )
    return _protect_symbols(_with_tutoring(state, generated))


def _protect_symbols(update: AgentStateUpdate) -> AgentStateUpdate:
    """Keep literal `*`, `(`, `_` ... in the reply from being read as markdown
    (LeetCode 678's `'*'` rendered as an empty pair of quotes)."""
    generated = update.get("generated_response")
    if generated is None:
        return update
    sections = [s.model_copy(update={"body": protect_symbols(s.body)}) for s in generated.sections]
    text = protect_symbols(generated.text)
    update["generated_response"] = generated.model_copy(update={"sections": sections, "text": text})
    update["response"] = text
    return update


def _guided_hint(state: AgentState) -> bool:
    """This turn's hint is the tutor's own problem-specific step (it ends with
    its question), not the generic ladder rung."""
    result = state.agent_result
    return isinstance(result, DSAResult) and result.hint is not None and result.hint.guided


def _learner_code(state: AgentState) -> str | None:
    """This turn's own submitted code (never a stored attempt)."""
    inp = state.structured_input
    if inp is None or not inp.code:
        return None
    return "\n\n".join(block.content for block in inp.code)


def _learner_verdict(state: AgentState) -> Verdict | None:
    """The sandbox verdict on the learner's OWN code this turn (`initial_verdict`)."""
    result = state.agent_result
    initial = getattr(result, "initial_verdict", None)
    if isinstance(initial, Verdict):
        return initial
    correctness = getattr(result, "correctness_verdict", None)
    return correctness if isinstance(correctness, Verdict) else None


def _practice_key(state: AgentState) -> str | None:
    practice = state.practice
    if practice is None or not practice.statement:
        return None
    return problem_key(StructuredInput(source="text", problem=practice.statement))


#: Where a topic came from that is solid enough to TEACH it ("which technique
#: fits?" graded against it). A retrieval guess is not: a problem missing from
#: the corpus (Longest Palindromic Substring -> "sliding window") would be
#: taught, and graded, as the wrong pattern.
_TRUSTED_TOPIC_SOURCES: Final[frozenset[str]] = frozenset(
    {"title", "conversation", "hint", "profile_match"}
)

#: Fixed phrases a learner uses to ASK for a section -> the section kinds.
_SECTION_ASKS: Final[tuple[tuple[re.Pattern[str], frozenset[str]], ...]] = (
    (re.compile(r"\bintuition\b", re.IGNORECASE), frozenset({"intuition", "key_insight"})),
    (
        re.compile(
            r"\b(recogni[sz]e|recognition|identify the pattern|which approach)\b", re.IGNORECASE
        ),
        frozenset({"recognition"}),
    ),
    (
        re.compile(r"\b(complexity|complexities|time and space|big[- ]?o)\b", re.IGNORECASE),
        frozenset({"complexity"}),
    ),
    (re.compile(r"\bbrute[- ]?force\b", re.IGNORECASE), frozenset({"brute_force", "why_slow"})),
    (re.compile(r"\b(common mistakes|pitfalls)\b", re.IGNORECASE), frozenset({"common_mistakes"})),
    (re.compile(r"\bconstraints?\b to keep", re.IGNORECASE), frozenset({"constraints"})),
    (
        re.compile(r"\b(walk me through|explain the (problem|approach))\b", re.IGNORECASE),
        frozenset({"understanding", "key_insight"}),
    ),
)


def requested_sections(text: str | None) -> frozenset[str]:
    """Section kinds the learner explicitly asked for (fixed phrases only)."""
    if not text:
        return frozenset()
    kinds: set[str] = set()
    for pattern, sections in _SECTION_ASKS:
        if pattern.search(text):
            kinds |= sections
    return frozenset(kinds)


#: Survey-style sections dropped from a tutoring turn that asks or reacts.
_VERBOSE_SECTION_KINDS: Final[frozenset[str]] = frozenset(
    {
        "recognition",
        "intuition",
        "understanding",
        "constraints",
        "brute_force",
        "why_slow",
        "key_insight",
        "complexity",
        "common_mistakes",
        "next_steps",
    }
)


#: Survey sections left out of a full-solution turn unless asked for by
#: name: the reveal is the insight, the tested code and its cost -- not a
#: second walk through the brute force and the pattern's textbook page.
_REVEAL_DROPPED_KINDS: Final[frozenset[str]] = frozenset(
    {
        "recognition",
        "intuition",
        "understanding",
        "constraints",
        "brute_force",
        "why_slow",
        "common_mistakes",
        "pseudocode",
        "next_steps",
    }
)


def _with_tutoring(state: AgentState, generated: GeneratedResponse) -> AgentStateUpdate:
    """Add the tutoring sections to `generated` and decide the next pending check."""
    plan = state.plan
    route = state.route
    progress = state.session_progress or SessionProgress.empty()
    grade = state.answer_grade
    pending = state.pending_check
    learner_code = _learner_code(state)
    reviewed_code = route in ("debug", "explain") and learner_code is not None

    found: list[str] = []
    if grade is not None and is_catalog_id(grade.misconception_id) and grade.misconception_id:
        found.append(grade.misconception_id)
    if learner_code is not None:
        topic_hint = plan.topic if plan is not None else None
        found += [m for m in detect_in_code(learner_code, topic_hint) if m not in found]

    topic = (plan.topic if plan is not None else None) or (pending.topic if pending else None)
    if state.practice is not None:
        topic = state.practice.topic
    problem_key_now = _practice_key(state) or state.problem_key
    assistance = plan.assistance_level if plan is not None else "hint"

    inp = state.structured_input
    problem_text = None
    if inp is not None:
        problem_text = "\n".join(part for part in (inp.problem, inp.question) if part)
    if grade is not None:
        question = state.next_pending
    else:
        question = question_for_turn(
            route=route,
            progress=progress,
            problem_key=problem_key_now,
            problem_text=problem_text,
            topic=topic,
            assistance=assistance,
            reveals_code=generated.reveals_code,
            misconceptions=found,
            practice=state.practice,
            first_turn_on_problem=state.problem_relation == "new",
            topic_trusted=state.topic_source in _TRUSTED_TOPIC_SOURCES,
        )
    guided = grade is None and _guided_hint(state)
    if (
        guided
        and question is not None
        and not found
        and question.question_id != chain_start(problem_text)
    ):
        # One question per turn: the guided step already ends with one about
        # THIS problem, so the pattern's generic recognition question waits.
        question = None
    carried: PendingCheck | None = None
    if route == "meta":
        carried = pending  # a question about the conversation answers nothing
    elif (
        question is None
        and grade is None
        and pending is not None
        and pending.kind == "code_submission"
        and not state.submitted_code
        and state.problem_relation != "new"
        and route not in ("practice", "grade")
    ):
        carried = pending  # still waiting for the code; not re-asked

    surfaced: list[str] = []
    if plan is not None and (
        route == "practice" or (route == "dsa" and state.problem_relation == "new")
    ):
        surfaced = surfaced_misconceptions(plan.watch_errors, topic, found)

    learner_verdict = _learner_verdict(state) if reviewed_code else None
    exec_lines = execution_lines(
        learner=learner_verdict,
        final=state.verification,
        learner_submitted=reviewed_code,
        reveals_code=generated.reveals_code,
    )
    # A curated chain's first question replaces the long answer with its one-line
    # opener (spec: "teach the missing piece, not the whole topic").
    # An explicit ask ("explain the intuition, how to recognize it, the
    # complexity") overrides the short-answer rule for exactly those sections,
    # the same way "don't explain yet" is respected (owner decision, T1).
    requested = requested_sections(problem_text)
    lead: str | None = None
    if (
        grade is None
        and not requested
        and question is not None
        and route in ("dsa", "explain")
        and question.question_id == chain_start(problem_text)
    ):
        lead = chain_intro(problem_text)
    extra = tutoring_sections(
        grade=grade,
        feedback=state.grade_feedback or "",
        misconceptions=found,
        surfaced=surfaced,
        execution_lines=exec_lines,
        question=question,
        lead=lead,
        lesson=state.grade_lesson
        or (pending.lesson if state.submitted_code and pending is not None else None),
    )

    base: list[ResponseSection] = list(generated.sections)
    if not base and lead is None and grade is None and (extra.before or extra.after):
        kind: ResponseSectionKind = "practice_problem" if route == "practice" else "explanation"
        base = [ResponseSection(kind=kind, title=SECTION_TITLES[kind], body=generated.text)]
    if lead is not None:
        base = []
    elif (question is not None and question.kind == "question") or grade is not None:
        # One question at a time, short and targeted: when the turn ends by
        # asking the learner something (or reacts to their answer), drop the
        # survey sections and keep the hint, code and verification.
        keep_complexity = generated.reveals_code
        base = [
            s
            for s in base
            if s.kind not in _VERBOSE_SECTION_KINDS
            or s.kind in requested
            or (s.kind == "complexity" and keep_complexity)
        ]
    elif route == "dsa" and generated.reveals_code:
        base = [s for s in base if s.kind not in _REVEAL_DROPPED_KINDS or s.kind in requested]
    elif guided and question is None:
        # Conversational turn: the step and its question, no survey sections
        # unless the learner asked for them by name.
        base = [s for s in base if s.kind not in _VERBOSE_SECTION_KINDS or s.kind in requested]
    after = list(extra.after)
    verification_index = next((i for i, s in enumerate(base) if s.kind == "verification"), None)
    if verification_index is not None and exec_lines:
        section = base[verification_index]
        base[verification_index] = section.model_copy(
            update={"body": "\n\n".join([*exec_lines, section.body])}
        )
        after = [s for s in after if s.kind != "execution"]
    sections = [*extra.before, *base, *after]
    revealed = route == "dsa" and generated.reveals_code
    if ((guided and question is None) or revealed) and sections:
        # The guided step (and the line introducing a revealed solution) reads
        # as the tutor talking, so it carries no "Your next hint" header.
        text = "\n\n".join(
            s.body if s.kind == "next_hint" else f"## {s.title}\n\n{s.body}" for s in sections
        )
        generated = generated.model_copy(update={"sections": sections, "text": text})
    elif sections and (extra.before or extra.after):
        text = "\n\n".join(f"## {s.title}\n\n{s.body}" for s in sections)
        generated = generated.model_copy(update={"sections": sections, "text": text})

    floor_key: str | None = None
    if state.reaction == "scaffold" and pending is not None:
        floor_key = pending.problem_key or pending.topic
    view = TutoringView(
        grade=grade,
        reaction=state.reaction,
        pending=(
            PendingView(
                kind=question.kind,
                question_id=question.question_id,
                question=question.question,
                options=[o.label for o in question.options],
            )
            if question is not None
            else None
        ),
        misconceptions=found,
        surfaced_misconceptions=surfaced,
        assistance_before=state.assistance_before
        or (pending.assistance_at_ask if pending else None),
        assistance_after=assistance,
        practice=state.practice,
        submission_reviewed=state.submitted_code
        and route == "debug"
        and learner_verdict is not None,
        execution=ExecutionView(learner=learner_verdict, final=state.verification)
        if exec_lines
        else None,
    )
    progress_after = next_progress(
        progress,
        topic=topic,
        grade=grade,
        graded=pending if grade is not None else None,
        asked=question,
        practice=state.practice,
        misconceptions=found,
        floor_key=floor_key,
        floor=assistance if floor_key is not None else None,
    )
    return {
        "response": generated.text,
        "generated_response": generated,
        "tutoring": view,
        "next_pending": question or carried,
        "next_progress": progress_after,
    }


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


def _misconception_topic(state: AgentState) -> str | None:
    """The corpus pattern of a catalog misconception found this turn, if any.

    A debug turn on unnamed code ("I'm getting a KeyError") often has no
    plan topic, but a detected misconception names its pattern from the
    closed catalog -- a trusted key -- so the misconception is still counted.
    """
    if state.tutoring is None:
        return None
    for misconception_id in state.tutoring.misconceptions:
        item = get_misconception(misconception_id)
        if item is not None:
            return item.pattern
    return None


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

    # Misconceptions this turn found (catalog ids only -- `tutoring` is built
    # from the closed catalog) are counted in `common_errors` (G3).
    errors = list(agent_output.errors)
    if state.tutoring is not None:
        errors += [m for m in state.tutoring.misconceptions if is_catalog_id(m) and m not in errors]
    # A graded conceptual answer is `concept_check` evidence (G2): weighted by
    # CONCEPT_ALPHA in `apply_event`, and it never sets `solved` -- code
    # correctness only ever comes from a sandbox verdict.
    grade = state.answer_grade
    concept_grade = grade.grade if grade is not None else None
    if concept_grade is not None:
        solved = None
    evidence_source = state.suite_source if solved is not None else "none"
    if concept_grade is not None:
        evidence_source = "concept_check"

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
        errors=errors,
        solved=solved,
        # Provenance of the OUTCOME: when the gate above dropped it, the
        # event is exposure only and its source is honestly "none".
        evidence_source=evidence_source,
        concept_grade=concept_grade,
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


_SAVE_ACTIVE_PROBLEM_FAILED_MESSAGE: Final = "failed to remember this conversation's problem"


def _named_curated_problem(state: AgentState) -> ActiveProblem | None:
    """A DSA turn that only NAMES a curated problem ("help me solve Two Sum")
    and carries no statement of its own: its curated statement becomes the
    active problem (ADAPTIVE-tutoring G4), so later replies, hints and the
    learner's code are about it. Only when nothing is active yet, or a
    different problem was."""
    if state.route != "dsa" or state.problem_relation in ("new", "same", "followup"):
        return None
    inp = state.structured_input
    text = "\n".join(part for part in (inp.problem, inp.question) if part) if inp else None
    curated = curated_problem_for_text(text)
    if curated is None:
        return None
    problem = StructuredInput(source="text", problem=curated.statement)
    key = problem_key(problem)
    if key is None or (state.active_problem is not None and state.active_problem.key == key):
        return None
    return ActiveProblem(problem=problem, key=key, topic=curated.topic)


def active_problem_update(state: AgentState) -> ActiveProblem | None:
    """The active problem to store after this turn, or `None` to leave it as is.

    - A NEW statement becomes the active problem, with this turn's topic.
    - A re-paste/follow-up on a problem stored WITHOUT a topic adopts this
      turn's topic once one is inferred -- but never a topic inherited from
      the conversation (there is none to inherit) and never an explicit
      client `topic` hint, which describes the request, not the problem.
    - Anything else (no problem in play) leaves the stored one untouched.
    """
    if state.route == "practice" and state.practice is not None and state.practice.statement:
        # An agent-chosen practice problem becomes the active problem, so the
        # learner's follow-ups ("hint?", their code) are about IT (tutoring Q1).
        practice_input = StructuredInput(source="text", problem=state.practice.statement)
        practice_key = problem_key(practice_input)
        if practice_key is not None:
            return ActiveProblem(
                problem=practice_input, key=practice_key, topic=state.practice.topic
            )
    named = _named_curated_problem(state)
    if named is not None:
        return named
    plan_topic = state.plan.topic if state.plan is not None else None
    trusted_topic = (
        plan_topic if state.topic_source in ("title", "retrieval", "profile_match") else None
    )
    if state.problem_relation == "new":
        if state.structured_input is None or state.problem_key is None:
            return None
        problem = state.structured_input.model_copy(update={"code": [], "error": None})
        return ActiveProblem(problem=problem, key=state.problem_key, topic=trusted_topic)
    active = state.active_problem
    if (
        state.problem_relation in ("same", "followup")
        and active is not None
        and active.topic is None
        and trusted_topic is not None
    ):
        return active.model_copy(update={"topic": trusted_topic})
    return None


_SAVE_TUTORING_FAILED_MESSAGE: Final = "failed to remember this conversation's pending question"


async def _persist_tutoring_state(state: AgentState, ctx: GraphContext) -> NodeError | None:
    """Store the pending check this turn ends with (NULL clears it) and the
    session progress (ADAPTIVE-tutoring), in a savepoint."""
    if ctx.session is None or ctx.user_id is None or ctx.conversation_id is None:
        return None
    progress = state.next_progress or state.session_progress or SessionProgress.empty()
    try:
        async with ctx.session.begin_nested():
            await set_tutoring_state(
                ctx.session, ctx.user_id, ctx.conversation_id, state.next_pending, progress
            )
    except Exception as exc:
        return NodeError(
            node="update_learner_model",
            error_type=type(exc).__name__,
            message=_SAVE_TUTORING_FAILED_MESSAGE,
        )
    return None


async def _persist_active_problem(state: AgentState, ctx: GraphContext) -> NodeError | None:
    """Store this turn's active problem (see `active_problem_update`), in a savepoint."""
    active = active_problem_update(state)
    if active is None or ctx.session is None or ctx.user_id is None:
        return None
    if ctx.conversation_id is None:
        return None
    try:
        async with ctx.session.begin_nested():
            await set_active_problem(ctx.session, ctx.user_id, ctx.conversation_id, active)
    except Exception as exc:
        return NodeError(
            node="update_learner_model",
            error_type=type(exc).__name__,
            message=_SAVE_ACTIVE_PROBLEM_FAILED_MESSAGE,
        )
    return None


def _skill_deltas(
    profile: LearnerProfileView | None, event: LearningEventCreate
) -> dict[str, float]:
    """How far this turn moved each skill it touched.

    Computed with `apply_event` -- the same pure projection `record_event` uses
    -- against the profile as it was at the START of this turn, so the numbers
    here are exactly what was written, not an estimate. Only non-zero moves are
    returned: an exposure event creates a key at `PRIOR` without moving it, and
    reporting "trees +0.0" as an adaptation would be noise dressed as feedback.

    The frontend renders these; it never derives them. The learner model has one
    owner, and it is the server.
    """
    before = dict(profile.skill_levels) if profile is not None else {}
    after, _errors = apply_event(before, {}, event)
    deltas: dict[str, float] = {}
    for key, new_value in after.items():
        if key.startswith(FAMILY_PREFIX):
            continue  # internal family estimate (P6), not a skill the UI shows
        # An unseen key starts from `PRIOR`, not from its own new value --
        # comparing it against itself reported no movement at all for the very
        # first outcome on a topic, which is the one a learner most wants to see.
        moved = round(new_value - before.get(key, PRIOR), 6)
        if moved:
            deltas[key] = moved
    return deltas


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
    skill_deltas: dict[str, float] = {}

    agent_output = state.agent_output
    if (
        state.route is not None
        and state.route not in ("clarify", "meta")
        and state.intent is not None
        and agent_output is not None
    ):
        event: LearningEventCreate | None = None
        try:
            topic = _event_topic(state.plan) or _misconception_topic(state)
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
                    skill_deltas = _skill_deltas(state.profile, event)
                if event_error is not None:
                    errors.append(event_error)

    if ctx.conversation_id is not None and ctx.session is not None and ctx.user_id is not None:
        turn_error = await _persist_turns(state, ctx)
        if turn_error is not None:
            errors.append(turn_error)
        active_error = await _persist_active_problem(state, ctx)
        if active_error is not None:
            errors.append(active_error)
        tutoring_error = await _persist_tutoring_state(state, ctx)
        if tutoring_error is not None:
            errors.append(tutoring_error)

    update: AgentStateUpdate = {}
    if events:
        update["events"] = events
    if events_persisted:
        update["events_persisted"] = events_persisted
    if errors:
        update["errors"] = errors
    if skill_deltas:
        update["skill_deltas"] = skill_deltas
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
            "practice_agent": _agent_outcome_fallback,
            "grade_answer": _agent_outcome_fallback,
            "execute_code": _execute_code_fallback,
            "verify": _verify_execution_fallback,
            "clarify": _agent_outcome_fallback,
            "final_response": _final_response_fallback,
            "update_learner_model": _update_learner_model_fallback,
        }
    )
)
