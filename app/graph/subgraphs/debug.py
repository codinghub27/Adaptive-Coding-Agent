"""The debugger subgraph: static analysis + sandbox-grounded bug fixing.

`DebugState` is this subgraph's own **private** scratch state -- never
`AgentState`, which is frozen with `extra="forbid"` and must not grow scratch
fields for one capability (mirrors `app.graph.subgraphs.dsa.DSAState`).
`run_debug` is the clean boundary the outer graph (Phase 07 P7's
`debug_agent` node) calls: it maps `AgentState` onto a fresh `DebugState`,
runs the compiled subgraph once, and maps the finished `DebugState` back onto
a `DebugResult` plus the `ExecutionRequest` P7 should hand to the outer
`execute_code`/`verify` edges for the authoritative re-verification.

Pipeline: `static_analysis -> infer_approach -> run_tests -> find_failing_case
-> localize -> explain -> patch -> re_run -> verify_final`, with a bounded
conditional edge from `verify_final` back to `patch` (at most
`MAX_PATCH_ATTEMPTS` cycles) when the patched code still fails. Every stage
that produces free text is either a pure function of already-established
sandbox/static facts (`find_failing_case`, `localize`) or one of exactly
three possible LLM calls per run (`infer_approach`, `explain`, `patch` --
`patch` may repeat up to the attempt cap), keeping this subgraph inside the
run's `BudgetedLLMClient` budget.

**Never executes code directly.** The only place anything in this subgraph
is ever run is `runtime.context.runner` (`run_tests`/`re_run`); everything
else only parses (`app.agents.debugger.static_analysis`) or calls an LLM.
`app.execution.verification.verify` is the single, pure source of pass/fail
truth -- `DebugResult.fixed` can only ever become `True` by mirroring a
passing `final_verdict` from an actual second sandbox run of the patched
code; nothing here ever trusts an LLM's own claim that a fix works.

`state["problem"]` (sourced from `AgentState.structured_input`) is untrusted
learner content, exactly as documented in `app.graph.nodes`'s module
docstring and `app.agents.debugger`'s; it is only ever handed to the LLM as
clearly-delimited data to reason about, never echoed into `DebugResult`'s
free-text fields.
"""

import ast
import re
from dataclasses import dataclass
from functools import cache
from typing import Final, Literal, TypedDict

from langgraph.graph import END, START, StateGraph  # pyright: ignore[reportMissingTypeStubs]
from langgraph.graph.state import (  # pyright: ignore[reportMissingTypeStubs]
    CompiledStateGraph,
)
from langgraph.runtime import Runtime

from app.agents.concept import Reference, pattern_chunks, turn_references
from app.agents.debugger import (
    annotate_code,
    extract_learner_code,
    failing_case_summary,
    infer_approach,
    localize_bug,
    patch_code,
    read_code,
    static_analysis,
)
from app.execution.verification import verify
from app.graph.state import AgentState, GraphContext
from app.response.plain_python import has_type_hints, without_type_hints
from app.schemas.agent_results import BugLocation, DebugResult, StaticFinding
from app.schemas.execution import (
    ExecutionRequest,
    ExecutionResult,
    HarnessError,
    TestSuite,
    Verdict,
)
from app.schemas.input import StructuredInput
from app.tutoring.adaptation import turn_context

__all__ = [
    "MAX_PATCH_ATTEMPTS",
    "DebugRunResult",
    "DebugState",
    "build_debug_graph",
    "get_debug_graph",
    "run_debug",
]

MAX_PATCH_ATTEMPTS: Final = 2
"""Hard cap on patch+re-verify cycles per run. Impossible to exceed: the
conditional edge out of `verify_final` only ever routes back to `patch` while
`attempts < MAX_PATCH_ATTEMPTS`."""

_SANDBOX_RUN_FAILED_MESSAGE: Final = "running your code failed unexpectedly"


class DebugState(TypedDict, total=False):
    """Private scratch state threaded through the debugger subgraph only.

    `problem`/`code`/`tests` are this run's inputs (set once by `run_debug`
    and never rewritten by any node); every other field is a stage output.
    `attempts` counts real `patch` calls made so far -- the single source of
    truth `run_debug` copies onto `DebugResult.attempts`.
    """

    problem: StructuredInput | None
    code: str | None
    tests: TestSuite | None
    #: Trusted corpus excerpts for the explanation prompt (ADAPTIVE-upgrade P3).
    references: list[Reference]
    #: The learner asked to be GIVEN the code (the plan is at `full`).
    wants_code: bool
    #: Lines the snippet repair put above the learner's own first line (F5).
    line_offset: int
    #: `<tutor_state>` and `<conversation_so_far>` for the explanation prompt.
    turn_context: str
    #: This turn follows up on code the tutor already looked at.
    is_reply: bool

    presented_code: str | None
    presented_label: str | None
    presented_notes: list[str]

    static_findings: list[StaticFinding]
    inferred_approach: str | None

    request: ExecutionRequest | None
    result: ExecutionResult | None
    initial_verdict: Verdict | None

    failing_case: str | None
    has_established_failure: bool
    bug_location: BugLocation | None
    bug_explanation: str | None
    citations: list[str]

    patched_code: str | None
    final_request: ExecutionRequest | None
    final_result: ExecutionResult | None
    final_verdict: Verdict | None
    attempts: int


_CompiledDebugGraph = CompiledStateGraph[DebugState, GraphContext, DebugState, DebugState]

_AfterVerify = Literal["patch", "end"]


# --------------------------------------------------------------------------
# Sandbox helpers (never execute anything themselves -- only build the
# request/degrade a runner failure into a safe, non-leaking ExecutionResult)
# --------------------------------------------------------------------------


def _sandbox_error_result(request: ExecutionRequest) -> ExecutionResult:
    return ExecutionResult(
        status="sandbox_error",
        language=request.language,
        error=HarnessError(type="DebuggerRunFailed", message=_SANDBOX_RUN_FAILED_MESSAGE),
    )


async def _run_in_sandbox(
    request: ExecutionRequest, runtime: Runtime[GraphContext]
) -> ExecutionResult:
    """Run `request` via `runtime.context.runner`, degrading any raised
    exception to a `sandbox_error` result rather than propagating it (which
    could otherwise leak raw exception text from an untrusted run)."""
    runner = runtime.context.runner
    if runner is None:
        return _sandbox_error_result(request)
    try:
        return await runner.run(request)
    except Exception:  # noqa: BLE001 - never leak the raw exception from an untrusted run
        return _sandbox_error_result(request)


# --------------------------------------------------------------------------
# Nodes
# --------------------------------------------------------------------------


_LINE_IN_SUMMARY_RE: Final = re.compile(r"\bline (\d+)\b")
_LINE_DIAGNOSTIC_RE: Final = re.compile(r"^line:(\d+)$")


def _learner_line(lineno: int, offset: int) -> int:
    return max(lineno - offset, 1)


def _in_learner_lines(verdict: Verdict, offset: int) -> Verdict:
    """`verdict` with its line numbers counted the way the learner typed the
    code (F5): the repair may have put a `def` header above their first line,
    and "line 10" must mean THEIR line 10."""
    if offset <= 0:
        return verdict
    summary = _LINE_IN_SUMMARY_RE.sub(
        lambda m: f"line {_learner_line(int(m.group(1)), offset)}", verdict.summary
    )
    diagnostics: list[str] = []
    for item in verdict.diagnostics:
        match = _LINE_DIAGNOSTIC_RE.match(item)
        diagnostics.append(f"line:{_learner_line(int(match.group(1)), offset)}" if match else item)
    return verdict.model_copy(update={"summary": summary, "diagnostics": diagnostics})


async def _static_analysis(state: DebugState, runtime: Runtime[GraphContext]) -> DebugState:
    """Pure, LLM-free static findings for the learner's code (`ast`, tree-sitter fallback)."""
    del runtime
    offset = state.get("line_offset", 0)
    findings = static_analysis(state.get("code"))
    if offset > 0:
        findings = [
            f.model_copy(update={"lineno": _learner_line(f.lineno, offset)})
            if f.lineno is not None
            else f
            for f in findings
        ]
    return {"static_findings": findings}


async def _run_tests(state: DebugState, runtime: Runtime[GraphContext]) -> DebugState:
    """Run the learner's own code once, unmodified, to establish ground truth."""
    code = state.get("code")
    if code is None:
        return {"request": None, "result": None, "initial_verdict": verify(None)}
    request = ExecutionRequest(code=code, tests=state.get("tests"))
    result = await _run_in_sandbox(request, runtime)
    verdict = _in_learner_lines(verify(result, request), state.get("line_offset", 0))
    if verdict.status == "fail" and verdict.error_type == "EOFError" and reads_keyboard(code):
        # The sandbox has no keyboard. `input()` failing there is not a bug in
        # the learner's code and must not be reported as one (measured live:
        # "It fails on this case: runtime error EOFError on line 1").
        verdict = Verdict(status="inconclusive", category="no_tests", summary=NEEDS_INPUT_SUMMARY)
        return {"request": None, "result": None, "initial_verdict": verdict}
    return {"request": request, "result": result, "initial_verdict": verdict}


async def _find_failing_case(state: DebugState, runtime: Runtime[GraphContext]) -> DebugState:
    """Read the failing case straight from the `Verdict` -- never from the LLM."""
    del runtime
    verdict = state.get("initial_verdict")
    return {
        "failing_case": failing_case_summary(verdict),
        "has_established_failure": verdict is not None and verdict.status == "fail",
    }


async def _localize(state: DebugState, runtime: Runtime[GraphContext]) -> DebugState:
    """Combine static findings + the failing case's own diagnostics into a `BugLocation`."""
    del runtime
    location = localize_bug(state.get("static_findings", []), state.get("initial_verdict"))
    return {"bug_location": location}


async def _explain(state: DebugState, runtime: Runtime[GraphContext]) -> DebugState:
    """ONE LLM call: what the learner is attempting, and why the bug happens.

    Deliberately NOT gated on `has_established_failure`. That flag means "the
    sandbox proved a failure", and it is false whenever no test suite could be
    derived -- which is the common shape of a real debug turn. Gating the
    explanation on it meant a learner who pasted broken code and asked "find
    the error" got "no code was executed" and no diagnosis at all. An unproven
    turn gets the prompt that allows "no bug found" (A-14).

    Explaining is not claiming correctness: `_patch` below is still gated on a
    sandbox-proven failure, `fixed` still comes only from `verify_final`, and
    `DebugResult.to_outcome` still derives `solved` from `initial_verdict`.

    The approach used to be a separate call (`infer_approach`); it is now a
    key of this one (F4). Code that PASSED gets no explanation -- asking for a
    bug there gets one invented -- so only then is the approach asked for on
    its own, and not even then when `_present` is about to ask for it anyway.
    """
    problem = state.get("problem")
    if problem is None or problem.is_empty:
        return {"bug_explanation": None, "inferred_approach": None}
    if not state.get("code"):
        if not (problem.error or problem.question):
            return {"bug_explanation": None, "inferred_approach": None}
        # No code, but an error or a traceback: that is read on its own
        # (target behaviour section 12), never answered with "no code was
        # executed".
        reading = await read_code(
            problem,
            static_findings=[],
            failing_case=None,
            bug_location=None,
            llm=runtime.context.llm,
            failure_established=False,
            turn_context=state.get("turn_context", ""),
            traceback_only=True,
        )
        return {
            "bug_explanation": reading.explanation or _ERROR_NOT_READ,
            "inferred_approach": None,
        }
    verdict = state.get("initial_verdict")
    if verdict is not None and verdict.status == "pass":
        if state.get("wants_code", False):
            return {"bug_explanation": None}  # `_present` names the approach
        if not (problem.question or "").strip():
            approach = await infer_approach(problem, runtime.context.llm)
            return {"bug_explanation": None, "inferred_approach": approach}
        # The code passed and the learner asked something about it: the same
        # one call names the approach AND answers the question.
        answer = await read_code(
            problem,
            static_findings=state.get("static_findings", []),
            failing_case=None,
            bug_location=None,
            llm=runtime.context.llm,
            failure_established=False,
            turn_context=state.get("turn_context", ""),
            passed=True,
        )
        return {"bug_explanation": answer.explanation, "inferred_approach": answer.approach}
    reading = await read_code(
        problem,
        static_findings=state.get("static_findings", []),
        failing_case=state.get("failing_case"),
        bug_location=state.get("bug_location"),
        llm=runtime.context.llm,
        references=state.get("references", []),
        failure_established=state.get("has_established_failure", False),
        turn_context=state.get("turn_context", ""),
        reply=state.get("is_reply", False),
    )
    return {
        "bug_explanation": reading.explanation,
        "citations": reading.citations,
        "inferred_approach": reading.approach,
    }


_PRESENT_PASSED: Final = "Verified in sandbox: it passes the same {passed}/{total} test cases."
_PRESENT_RAN: Final = (
    "Executed in sandbox, not verified: it ran cleanly, but there were no test cases to "
    "check its output against."
)
_PRESENT_AS_SHARED: Final = "This is your code as you shared it (made runnable)."
_PRESENT_TIDY_REJECTED: Final = (
    "This is your code as you shared it (made runnable). A tidied, commented version was "
    "written too, but it did not hold up in the sandbox, so it is not shown."
)


async def _present(state: DebugState, runtime: Runtime[GraphContext]) -> DebugState:
    """The learner asked for the code and there is no fix to show: hand THEIR
    code back, tidied and commented (F2). Never "keep your version" alone.

    The tidied version is a candidate until the sandbox agrees: it must pass
    the same cases their code passed, or run cleanly when there were none.
    Otherwise their own code is returned exactly as shared.
    """
    code = state.get("code")
    if not state.get("wants_code", False) or code is None:
        return {}
    if state.get("has_established_failure", False):
        return {}  # the fix is what gets shown
    problem = state.get("problem")
    initial = state.get("initial_verdict")
    if problem is None or initial is None or initial.status not in ("pass", "inconclusive"):
        return {}
    update: DebugState = {"presented_code": code, "presented_label": _PRESENT_AS_SHARED}
    annotated = await annotate_code(problem, runtime.context.llm)
    if annotated is None:
        return update
    if annotated.approach and not state.get("inferred_approach"):
        update["inferred_approach"] = annotated.approach
    request = ExecutionRequest(code=annotated.code, tests=state.get("tests"))
    verdict = verify(await _run_in_sandbox(request, runtime), request)
    if initial.status == "pass" and verdict.status == "pass":
        label = _PRESENT_PASSED.format(passed=verdict.cases_passed, total=verdict.cases_total)
    elif initial.category == "no_tests" and verdict.category == "no_tests":
        label = _PRESENT_RAN
    else:
        update["presented_label"] = _PRESENT_TIDY_REJECTED
        return update
    update["presented_code"] = annotated.code
    update["presented_label"] = label
    update["presented_notes"] = annotated.notes
    return update


async def _patch(state: DebugState, runtime: Runtime[GraphContext]) -> DebugState:
    """LLM call #3 (repeated up to `MAX_PATCH_ATTEMPTS`): produce a candidate fix.

    Never itself decides correctness -- `re_run`/`verify_final` are the only
    source of truth for whether this attempt actually worked.
    """
    attempts = state.get("attempts", 0)
    if not state.get("has_established_failure", False):
        return {"patched_code": None}
    problem = state.get("problem")
    if problem is None:
        return {"patched_code": None, "attempts": attempts + 1}
    previous_verdict = state.get("final_verdict")
    patched = await patch_code(
        problem,
        static_findings=state.get("static_findings", []),
        failing_case=state.get("failing_case"),
        bug_location=state.get("bug_location"),
        bug_explanation=state.get("bug_explanation"),
        previous_patch=state.get("patched_code") if attempts > 0 else None,
        previous_failure=previous_verdict.summary if attempts > 0 and previous_verdict else None,
        llm=runtime.context.llm,
    )
    return {"patched_code": patched, "attempts": attempts + 1}


async def _re_run(state: DebugState, runtime: Runtime[GraphContext]) -> DebugState:
    """Every patch must be re-verified in the sandbox: run the patched code, unconditionally."""
    patched = state.get("patched_code")
    if patched is None:
        return {"final_request": None, "final_result": None}
    request = ExecutionRequest(code=patched, tests=state.get("tests"))
    result = await _run_in_sandbox(request, runtime)
    return {"final_request": request, "final_result": result}


async def _verify_final(state: DebugState, runtime: Runtime[GraphContext]) -> DebugState:
    """Derive this cycle's authoritative `Verdict` from the actual re-run -- never from
    the LLM's own claim about the patch."""
    del runtime
    return {"final_verdict": verify(state.get("final_result"), state.get("final_request"))}


def _after_verify(state: DebugState) -> _AfterVerify:
    """Bounded loop guard: retry `patch` only while there is an established failure
    still unresolved and the attempt cap has not been reached -- impossible to exceed
    `MAX_PATCH_ATTEMPTS` patch calls."""
    if not state.get("has_established_failure", False):
        return "end"
    verdict = state.get("final_verdict")
    if verdict is not None and verdict.status == "pass":
        return "end"
    if state.get("attempts", 0) >= MAX_PATCH_ATTEMPTS:
        return "end"
    return "patch"


# --------------------------------------------------------------------------
# Graph assembly
# --------------------------------------------------------------------------


def build_debug_graph() -> _CompiledDebugGraph:
    """Build and compile a fresh debugger subgraph."""
    builder = StateGraph(DebugState, context_schema=GraphContext)
    builder.add_node("static_analysis", _static_analysis)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("present", _present)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("run_tests", _run_tests)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("find_failing_case", _find_failing_case)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("localize", _localize)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("explain", _explain)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("patch", _patch)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("re_run", _re_run)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("verify_final", _verify_final)  # pyright: ignore[reportUnknownMemberType]

    builder.add_edge(START, "static_analysis")
    builder.add_edge("static_analysis", "run_tests")
    builder.add_edge("run_tests", "find_failing_case")
    builder.add_edge("find_failing_case", "localize")
    builder.add_edge("localize", "explain")
    builder.add_edge("explain", "patch")
    builder.add_edge("patch", "re_run")
    builder.add_edge("re_run", "verify_final")
    builder.add_conditional_edges(  # pyright: ignore[reportUnknownMemberType]
        "verify_final", _after_verify, {"patch": "patch", "end": "present"}
    )
    builder.add_edge("present", END)

    return builder.compile()  # pyright: ignore[reportUnknownMemberType]


@cache
def get_debug_graph() -> _CompiledDebugGraph:
    """The compiled debugger subgraph, built once and cached for reuse.

    Stateless (no checkpointer, no module-level mutable state), so sharing
    this one instance across concurrent runs is safe -- mirrors
    `app.graph.subgraphs.dsa.get_dsa_graph`.
    """
    return build_debug_graph()


# --------------------------------------------------------------------------
# AgentState <-> DebugState boundary
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DebugRunResult:
    """The outcome of one `run_debug` call.

    `execution_request` is the final (patched, if any) request -- P7's
    `debug_agent` node body puts it on `AgentState.execution_request` so the
    outer `execute_code -> verify` edges (Phase 06) re-run it and produce the
    turn's authoritative `Verdict`. That deliberate double-run (once here to
    establish/patch the bug, again in the outer graph) is an accepted
    architecture decision.
    """

    result: DebugResult
    execution_request: ExecutionRequest | None


_NOT_EXECUTED_LABEL: Final = (
    "Not executed: the code sandbox is not available. This is your code as you shared it "
    "(made runnable)."
)


async def _static_review(
    state: AgentState, runtime: Runtime[GraphContext], *, wants_code: bool, offset: int
) -> DebugRunResult:
    """No sandbox: the `ast` checks plus ONE model call reading the code
    (owner decision A-15). Nothing ran, so nothing is claimed: both verdicts
    are the verifier's "skipped", no patch is attempted, `fixed` is False,
    and the reply is labelled "Not executed"."""
    skipped = verify(None)
    problem = state.structured_input
    code = extract_learner_code(problem)
    if problem is None or code is None:
        return DebugRunResult(
            result=DebugResult(
                initial_verdict=skipped, final_verdict=skipped, fixed=False, not_executed=True
            ),
            execution_request=None,
        )
    findings = (await _static_analysis({"code": code, "line_offset": offset}, runtime)).get(
        "static_findings", []
    )
    reading = await read_code(
        problem,
        static_findings=findings,
        failing_case=None,
        bug_location=localize_bug(findings, None),
        llm=runtime.context.llm,
        references=turn_references(
            state.retrieved_context,
            pattern_chunks(state.plan.topic if state.plan is not None else None),
            ("common_mistakes", "when_not_to_use"),
        ),
        failure_established=False,
        turn_context=_turn_context(state),
    )
    result = DebugResult(
        static_findings=findings,
        inferred_approach=reading.approach,
        bug_explanation=reading.explanation,
        bug_location=localize_bug(findings, None),
        presented_code=code if wants_code else None,
        presented_label=_NOT_EXECUTED_LABEL if wants_code else None,
        initial_verdict=skipped,
        final_verdict=skipped,
        fixed=False,
        not_executed=True,
        citations=reading.citations,
    )
    return DebugRunResult(result=result, execution_request=None)


def _turn_context(state: AgentState) -> str:
    """The turn decision, the adaptation and the recent exchange, for the
    prompt that explains the bug."""
    recent = [(message.role, message.content) for message in state.recent_context]
    return turn_context(state.decision, state.adaptation, recent)


_REPORTED_SYNTAX_ERROR_RE: Final = re.compile(r"SyntaxError|invalid syntax", re.IGNORECASE)
_SYNTAX_ELSEWHERE: Final = (
    "**Syntax, where you ran it.** The SyntaxError you saw does not happen in the sandbox: "
    "this code parses under Python 3 here. So it comes from where it was run, not from the "
    "logic."
)
_SYNTAX_FROM_HINTS: Final = (
    " The line it points at uses type hints, which Python 2 cannot read, and LeetCode's "
    '"Python" option is Python 2. Either choose "Python3" as the language, or write the '
    "line without hints:\n\n```python\n{line}\n```"
)
_SYNTAX_UNKNOWN: Final = (
    " The most likely causes are an older Python version than the code needs, or stray "
    "text pasted around it. Which language option did you select, and what are the two "
    "lines above the one the error points at?"
)
_LOGIC_SEPARATELY: Final = (
    "**Logic, separately.** With the syntax out of the way, this is a different problem:"
)


def _environment_note(problem: StructuredInput | None, verdict: Verdict | None) -> str | None:
    """What to say when the learner reports a SyntaxError the sandbox does not
    reproduce: the code parses here, so the error belongs to their environment.

    Measured live: a learner pasted code with type hints and LeetCode's
    "SyntaxError: invalid syntax" on the annotated line. The sandbox ran the
    code under Python 3, and the reply reported a wrong-answer failure as if
    the SyntaxError had never been mentioned. `None` when no SyntaxError was
    reported, or when the sandbox could not parse the code either (then it IS
    a syntax error and the ordinary report covers it)."""
    if problem is None or verdict is None or not problem.error:
        return None
    if not _REPORTED_SYNTAX_ERROR_RE.search(problem.error):
        return None
    if verdict.category == "syntax_error" or verdict.status == "skipped":
        return None
    code = extract_learner_code(problem)
    if code is None:
        return None
    if has_type_hints(code):
        plain = without_type_hints(code).splitlines()
        changed = [
            new.strip()
            for old, new in zip(code.splitlines(), plain, strict=False)
            if old != new and new.strip()
        ]
        if changed:
            return _SYNTAX_ELSEWHERE + _SYNTAX_FROM_HINTS.format(line=changed[0])
    return _SYNTAX_ELSEWHERE + _SYNTAX_UNKNOWN


def _in_their_style(
    code: str | None, problem: StructuredInput | None, *, plain: bool = False
) -> str | None:
    """Code handed back to the learner, without type hints the tutor added.

    Generated code is shown as plain Python (owner decision). A fix or a
    tidied copy of the learner's code keeps THEIR hints when they wrote some;
    it never gains hints they did not write."""
    if code is None:
        return None
    original = extract_learner_code(problem)
    if not plain and original is not None and has_type_hints(original):
        return code
    return without_type_hints(code)


_THEIR_FIX_IS_RIGHT: Final = (
    "Yes -- that is the fix. With `{line}` in place your code passes {passed}/{total} "
    "test cases in the sandbox. Make the change and send it back if you want it run again."
)


def _credit_for_their_fix(
    message: str | None,
    patched_code: str | None,
    verdict: Verdict | None,
    original_code: str | None = None,
) -> str | None:
    """The sandbox's own answer to a line of code the learner proposes.

    When the line they sent IS a line of the fix that just passed in the
    sandbox, the verdict on their proposal is a fact, not a model's opinion:
    it is said in fixed words with the real count. Compared as text with
    whitespace collapsed; nothing of theirs is executed here."""
    if not message or patched_code is None or verdict is None or verdict.status != "pass":
        return None
    line = " ".join(message.strip().strip("`").split())
    if not line or "\n" in message.strip() or len(line) > 200:
        return None
    patched = {" ".join(row.split()) for row in patched_code.splitlines()}
    unchanged = {" ".join(row.split()) for row in (original_code or "").splitlines()}
    if line not in patched or line in unchanged:
        # Not a line of the fix -- or a line their failing code already had,
        # which the fix merely kept: quoting it back changes nothing.
        return None
    return _THEIR_FIX_IS_RIGHT.format(
        line=line, passed=verdict.cases_passed, total=verdict.cases_total
    )


_ERROR_NOT_READ: Final = (
    "I could not analyse that error just now. Send it again, with the few lines of code "
    "around the line it names if you can."
)

NEEDS_INPUT_SUMMARY: Final = (
    "it waits for keyboard input with input(), which the sandbox does not provide, so it "
    "was not run to the end"
)


def reads_keyboard(code: str) -> bool:
    """Does `code` call the built-in `input()`? Read with `ast`, never run."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return "input(" in code
    return any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "input"
        for node in ast.walk(tree)
    )


def _is_fixed(verdict: Verdict | None) -> bool:
    return verdict is not None and verdict.status == "pass"


async def run_debug(
    state: AgentState, runtime: Runtime[GraphContext], *, tests: TestSuite | None = None
) -> DebugRunResult:
    """Run the debugger subgraph for this turn and map the result back.

    `runtime.context.runner is None` (sandbox disabled/unavailable) short-circuits
    before the subgraph -- and therefore before any LLM call -- to a
    `DebugResult` whose `initial_verdict`/`final_verdict` are both the
    verifier's own "skipped" output and `fixed=False`: never raises, never
    claims a fix, never spends the run's LLM budget on an unverifiable guess.

    `state.execution_request.tests`, when already set, always wins over the
    `tests` parameter -- callers that already have a real `ExecutionRequest`
    on the state must not be overridden by a caller-derived fallback suite.
    """
    problem = state.structured_input
    wants_code = state.plan is not None and state.plan.assistance_level == "full"
    blocks = problem.code if problem is not None else []
    offset = blocks[0].line_offset if len(blocks) == 1 else 0
    if runtime.context.runner is None:
        return await _static_review(state, runtime, wants_code=wants_code, offset=offset)

    available_tests = (
        state.execution_request.tests if state.execution_request is not None else None
    ) or tests
    initial: DebugState = {
        "problem": problem,
        "code": extract_learner_code(problem),
        "tests": available_tests,
        "attempts": 0,
        "wants_code": wants_code,
        "line_offset": offset,
        "turn_context": _turn_context(state),
        "is_reply": (
            state.problem_relation == "followup" and bool(state.recent_context) and not wants_code
        ),
        "references": turn_references(
            state.retrieved_context,
            pattern_chunks(state.plan.topic if state.plan is not None else None),
            ("common_mistakes", "when_not_to_use"),
        ),
    }
    final_state = await get_debug_graph().ainvoke(  # pyright: ignore[reportUnknownMemberType]
        initial, context=runtime.context
    )

    final_verdict = final_state.get("final_verdict")
    first_seen = final_state.get("initial_verdict")
    is_reply = bool(initial.get("is_reply"))
    explanation = final_state.get("bug_explanation")
    credited = _credit_for_their_fix(
        problem.question if problem is not None else None,
        final_state.get("patched_code"),
        final_verdict,
        extract_learner_code(problem),
    )
    if is_reply and credited is not None:
        explanation = credited
    environment = _environment_note(problem, first_seen)
    if environment is not None:
        # Two different failures, reported as two: the parsing error the
        # learner saw where THEY ran it, and whatever the sandbox found.
        logic = explanation if first_seen is not None and first_seen.status == "fail" else None
        explanation = f"{environment}\n\n{_LOGIC_SEPARATELY} {logic}" if logic else environment
    result = DebugResult(
        # On a follow-up the static findings, the approach and the failing
        # case were all said on the turn before; only the reply is new.
        static_findings=[] if is_reply else final_state.get("static_findings") or [],
        inferred_approach=None if is_reply else final_state.get("inferred_approach"),
        failing_case=None if is_reply else final_state.get("failing_case"),
        bug_explanation=explanation,
        bug_location=final_state.get("bug_location"),
        patched_code=_in_their_style(
            final_state.get("patched_code"), problem, plain=environment is not None
        ),
        presented_code=_in_their_style(
            final_state.get("presented_code"), problem, plain=environment is not None
        ),
        presented_label=final_state.get("presented_label"),
        presented_notes=final_state.get("presented_notes") or [],
        attempts=final_state.get("attempts", 0),
        initial_verdict=final_state.get("initial_verdict"),
        final_verdict=final_verdict,
        fixed=_is_fixed(final_verdict),
        citations=final_state.get("citations") or [],
    )
    execution_request = final_state.get("final_request") or final_state.get("request")
    first_verdict = final_state.get("initial_verdict")
    if first_verdict is not None and first_verdict.summary == NEEDS_INPUT_SUMMARY:
        # It waits for keyboard input: running it again would only report the
        # sandbox's missing keyboard as a failure of a "suggested fix".
        execution_request = None
    return DebugRunResult(result=result, execution_request=execution_request)
