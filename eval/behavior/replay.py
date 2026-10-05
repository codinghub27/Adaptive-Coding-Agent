"""Replay the five reference conversations through the LIVE API (ADAPTIVE-tutoring Q0).

    .\\venv\\Scripts\\python.exe -m eval.behavior.replay --base-url http://127.0.0.1:8000

Each conversation in `adaptive_examples.jsonl` is driven turn by turn: only
the LEARNER turns are sent (the assistant turns are the reference behaviour,
never sent), on a FRESH account, through `/chat/stream` exactly like the web
UI, never with a `topic` form field. After every learner turn the checks
declared for it in `CHECKS` run against the `done` frame; profile checks run
once at the end of the conversation.

THE CHECKS ARE THE FIXED INSTRUMENT. Every before/after number in
`docs/features/ADAPTIVE-tutoring.md` comes from this file unchanged; changing
a check makes earlier numbers incomparable, so a change must be recorded there.

The tutoring contract the checks read (all additive on `ChatResponse`):
`body["tutoring"] = {grade: {grade, method, misconception_id, confidence, ...}
| None, reaction, pending: {kind, question_id, question, options} | None,
misconceptions: [ids found this turn], surfaced_misconceptions: [ids],
assistance_before, assistance_after, practice: {title, topic, difficulty} |
None, submission_reviewed: bool}`. Before Q1 it does not exist, so the
baseline is (by design) low.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from eval.transcript_probes import Api, Turn

__all__ = ["CHECKS", "ExampleResult", "execution_lines_ok", "main"]

EXAMPLES_PATH: Final = Path(__file__).with_name("adaptive_examples.jsonl")
RESULTS_DIR: Final = Path(__file__).resolve().parent.parent / "results"

ASSISTANCE_ORDER: Final = ("hint", "concept", "pseudocode", "partial", "full")

#: Teaching mode per example, from the learner's stated preference.
MODES: Final[dict[str, str]] = {
    "adaptive_001": "guidance",
    "adaptive_002": "balanced",
    "adaptive_003": "challenge",
    "adaptive_004": "balanced",
    "adaptive_005": "balanced",
}

# --------------------------------------------------------------------------
# Accessors over one turn's `done` frame
# --------------------------------------------------------------------------


def tutoring(turn: Turn) -> dict[str, Any]:
    return dict(turn.body.get("tutoring") or {})


def grade_of(turn: Turn) -> str | None:
    grade = tutoring(turn).get("grade") or {}
    return grade.get("grade")


def pending(turn: Turn) -> dict[str, Any]:
    return dict(tutoring(turn).get("pending") or {})


def has_code(turn: Turn) -> bool:
    return turn.reveals_code or "```" in turn.text


def assistance_step(turn: Turn) -> int | None:
    """How many levels assistance moved on this turn (after - before)."""
    t = tutoring(turn)
    before, after = t.get("assistance_before"), t.get("assistance_after")
    if before not in ASSISTANCE_ORDER or after not in ASSISTANCE_ORDER:
        return None
    return ASSISTANCE_ORDER.index(after) - ASSISTANCE_ORDER.index(before)


def expected_execution_line(verification: dict[str, Any] | None, label: str = "") -> str:
    """The ONE rendering of a sandbox verdict an "Execution" line may show.

    `label` is "" (the turn's authoritative verdict), " (your code)" (the
    learner's own submission, `initial_verdict`) or " (suggested fix)".
    """
    head = f"Execution{label}:"
    if not verification:
        return f"{head} not executed"
    status = verification.get("status")
    passed, total = verification.get("cases_passed"), verification.get("cases_total")
    if status == "pass":
        # Wording changed 2026-10-03 (✓/✗ marks, per the behaviour spec);
        # the check itself -- line must render the verdict -- is unchanged.
        return f"{head} ✓ passed {passed}/{total} test cases in the sandbox"
    if status == "fail":
        return f"{head} ✗ failed -- {passed}/{total} test cases passed in the sandbox"
    if status == "inconclusive":
        return f"{head} ran in the sandbox, not verified (no test cases)"
    return f"{head} not executed"


_EXECUTION_LINE_RE: Final = re.compile(r"^\s*\**Execution\b.*$", re.IGNORECASE | re.MULTILINE)


def execution_lines(turn: Turn) -> list[str]:
    return [
        m.group(0).strip().replace("**", "").strip() for m in _EXECUTION_LINE_RE.finditer(turn.text)
    ]


def execution_lines_ok(turn: Turn, *, required: bool) -> tuple[bool, str]:
    """Every "Execution" line equals the rendering of the verdict it names.

    "(your code)" lines must render `tutoring.execution.learner` (the learner's
    own submission's sandbox verdict); every other line must render the turn's
    authoritative `verification` (the outer execute/verify of the final code).
    A line backed by no verdict must say "not executed". When `required`, at
    least one line must exist.
    """
    lines = execution_lines(turn)
    learner = (tutoring(turn).get("execution") or {}).get("learner")
    final = turn.body.get("verification")
    if required and not lines:
        return False, f"no Execution line (verification={final})"
    bad: list[str] = []
    for line in lines:
        if line.startswith("Execution (your code)"):
            want = expected_execution_line(learner, " (your code)")
        elif line.startswith("Execution (suggested fix)"):
            want = expected_execution_line(final, " (suggested fix)")
        else:
            want = expected_execution_line(final)
        if line != want:
            bad.append(f"{line!r} != {want!r}")
    return not bad, f"lines={lines} bad={bad}"


def question_count(turn: Turn) -> int:
    return sum(1 for s in turn.sections if s.get("kind") == "check_question")


def one_question(turn: Turn) -> tuple[bool, str]:
    p = pending(turn)
    n = question_count(turn)
    return p.get("kind") == "question" and n == 1, f"pending={p.get('kind')} sections={n}"


def graded(turn: Turn, *labels: str) -> tuple[bool, str]:
    g = grade_of(turn)
    return "grade_answer" in turn.stages and g in labels, f"grade={g} stages={turn.stages}"


def reaction(turn: Turn, *names: str) -> tuple[bool, str]:
    r = tutoring(turn).get("reaction")
    return r in names, f"reaction={r}"


def found(turn: Turn, misconception_id: str) -> tuple[bool, str]:
    ids = list(tutoring(turn).get("misconceptions") or [])
    return misconception_id in ids, f"found={ids}"


def hint_safe(turn: Turn) -> tuple[bool, str]:
    """Revealed code is always sandbox-verified (the upgrade's hard gate)."""
    if not turn.reveals_code:
        return True, "no code"
    status = turn.verification_status
    return status == "pass", f"reveals_code with verification={status}"


# --------------------------------------------------------------------------
# The frozen check table
# --------------------------------------------------------------------------

TurnCheck = Callable[[Turn, list[Turn]], tuple[bool, str]]


def _route(*routes: str) -> TurnCheck:
    return lambda t, _h: (t.route in routes, f"route={t.route}")


def _no_code() -> TurnCheck:
    return lambda t, _h: (not has_code(t), f"reveals_code={t.reveals_code}")


def _one_question() -> TurnCheck:
    return lambda t, _h: one_question(t)


def _graded(*labels: str) -> TurnCheck:
    return lambda t, _h: graded(t, *labels)


def _reaction(*names: str) -> TurnCheck:
    return lambda t, _h: reaction(t, *names)


def _pending_kind(kind: str) -> TurnCheck:
    return lambda t, _h: (pending(t).get("kind") == kind, f"pending={pending(t)}")


def _assist_up() -> TurnCheck:
    def check(t: Turn, _h: list[Turn]) -> tuple[bool, str]:
        step = assistance_step(t)
        return step == 1, f"step={step} tutoring={tutoring(t)}"

    return check


def _assist_rose() -> TurnCheck:
    def check(t: Turn, _h: list[Turn]) -> tuple[bool, str]:
        step = assistance_step(t)
        return step is not None and step >= 1, f"step={step}"

    return check


def _verified_reveal() -> TurnCheck:
    return lambda t, _h: (
        t.reveals_code and t.verification_status == "pass",
        f"reveals_code={t.reveals_code} verdict={t.verification_status}",
    )


def _misconception(misconception_id: str) -> TurnCheck:
    return lambda t, _h: found(t, misconception_id)


def _execution(required: bool) -> TurnCheck:
    return lambda t, _h: execution_lines_ok(t, required=required)


def _concept_event() -> TurnCheck:
    def check(t: Turn, _h: list[Turn]) -> tuple[bool, str]:
        sources = [e.get("evidence_source") for e in t.body.get("events") or []]
        solved = [e.get("solved") for e in t.body.get("events") or []]
        ok = "concept_check" in sources and all(
            s is None for s, src in zip(solved, sources, strict=False) if src == "concept_check"
        )
        return ok, f"sources={sources} solved={solved}"

    return check


def _practice(difficulty: str | None = None, family: Sequence[str] | None = None) -> TurnCheck:
    def check(t: Turn, _h: list[Turn]) -> tuple[bool, str]:
        p = tutoring(t).get("practice") or {}
        ok = bool(p.get("title"))
        if difficulty is not None:
            ok = ok and p.get("difficulty") == difficulty
        if family is not None:
            ok = ok and p.get("topic") in family
        return ok, f"practice={p}"

    return check


def _difficulty_rose(previous_turn: int) -> TurnCheck:
    order = ("easy", "medium", "hard")

    def check(t: Turn, history: list[Turn]) -> tuple[bool, str]:
        before = (tutoring(history[previous_turn]).get("practice") or {}).get("difficulty")
        after = (tutoring(t).get("practice") or {}).get("difficulty")
        ok = before in order and after in order and order.index(after) > order.index(before)
        return ok, f"{before} -> {after}"

    return check


def _surfaced() -> TurnCheck:
    def check(t: Turn, _h: list[Turn]) -> tuple[bool, str]:
        ids = list(tutoring(t).get("surfaced_misconceptions") or [])
        return bool(ids), f"surfaced={ids}"

    return check


def _reviewed() -> TurnCheck:
    def check(t: Turn, _h: list[Turn]) -> tuple[bool, str]:
        reviewed = bool(tutoring(t).get("submission_reviewed"))
        status = t.verification_status
        return reviewed and status in ("pass", "fail"), f"reviewed={reviewed} verdict={status}"

    return check


_REFUSALS: Final = (
    "don't cover",
    "do not cover",
    "doesn't cover",
    "does not cover",
    "not covered",
    "i cannot answer",
    "i can't answer",
)


def _explained(*must_mention: str) -> TurnCheck:
    """A real answer: long enough, on the subject, and not a refusal."""

    def check(turn: Turn, _history: list[Turn]) -> tuple[bool, str]:
        lowered = turn.text.lower()
        refused = [phrase for phrase in _REFUSALS if phrase in lowered]
        missing = [word for word in must_mention if word.lower() not in lowered]
        ok = not refused and not missing and len(turn.text) >= 200
        return ok, f"refused={refused} missing={missing} chars={len(turn.text)}"

    return check


def _topic(expected: str | None) -> TurnCheck:
    """The turn's topic label -- what the topic card shows -- is `expected`."""
    return lambda t, _h: (t.topic == expected, f"topic={t.topic}")


def _never_mentions(*phrases: str) -> TurnCheck:
    def check(turn: Turn, _history: list[Turn]) -> tuple[bool, str]:
        lowered = turn.text.lower()
        found = [phrase for phrase in phrases if phrase.lower() in lowered]
        return not found, f"found={found}"

    return check


def _cited(*, expected: bool) -> TurnCheck:
    return lambda t, _h: (bool(t.citations) is expected, f"citations={t.citations}")


def _says(phrase: str, *, expected: bool = True) -> TurnCheck:
    return lambda t, _h: ((phrase.lower() in t.text.lower()) is expected, f"says {phrase!r}")


GRAPH_FAMILY: Final = (
    "graphs",
    "bfs",
    "dfs",
    "dijkstra",
    "topological_sort",
    "union_find",
    "bellman_ford",
)

#: (learner-turn index in the JSONL conversation) -> [(check name, predicate)].
CHECKS: Final[dict[str, dict[int, list[tuple[str, TurnCheck]]]]] = {
    "adaptive_001": {
        0: [
            ("route_dsa", _route("dsa")),
            ("no_code", _no_code()),
            ("one_question", _one_question()),
        ],
        2: [
            ("graded_correct", _graded("correct")),
            ("advanced", _reaction("advance")),
            ("one_question", _one_question()),
            ("concept_event", _concept_event()),
        ],
        4: [
            ("graded_correct", _graded("correct")),
            ("advanced", _reaction("advance")),
            ("awaits_code", _pending_kind("code_submission")),
        ],
        6: [
            ("graded_dont_know", _graded("dont_know")),
            # CHANGED 2026-10-03 (owner's behaviour spec, Example 1): in Guidance
            # mode, "I don't know how" to a code request after the concept was
            # graded correct builds it together -> the VERIFIED solution, so
            # assistance rises to `full` (was: exactly +1). Code must then be
            # sandbox-verified (hint_safety) and carry an Execution line.
            ("assistance_up", _assist_rose()),
            ("verified_solution", _verified_reveal()),
            ("execution_line", _execution(required=True)),
        ],
    },
    "adaptive_002": {
        0: [
            ("route_debug", _route("debug")),
            ("misconception", _misconception("hashing.checked_key_vs_accessed_key")),
            ("execution_line", _execution(required=True)),
            ("one_question", _one_question()),
        ],
        2: [("graded_correct", _graded("correct")), ("advanced", _reaction("advance"))],
    },
    "adaptive_003": {
        0: [
            ("route_practice", _route("practice")),
            ("practice_hard", _practice("hard", GRAPH_FAMILY)),
            ("no_code", _no_code()),
            ("one_question", _one_question()),
        ],
        2: [
            ("graded_ok", _graded("correct", "partial")),
            ("moved_on", _reaction("advance", "narrow")),
            ("one_question", _one_question()),
        ],
        4: [
            ("graded_correct", _graded("correct")),
            ("advanced", _reaction("advance")),
            ("one_question", _one_question()),
        ],
        6: [
            ("graded_correct", _graded("correct")),
            ("awaits_code", _pending_kind("code_submission")),
        ],
        8: [
            ("route_debug", _route("debug")),
            ("reviewed_in_sandbox", _reviewed()),
            ("misconception", _misconception("graphs.parent_node_vs_parent_edge")),
            ("execution_line", _execution(required=True)),
        ],
    },
    "adaptive_004": {
        0: [
            ("misconception", _misconception("trees.returnable_vs_global_path")),
            ("no_code", _no_code()),
            ("one_question", _one_question()),
        ],
        2: [("graded_correct", _graded("correct")), ("advanced", _reaction("advance"))],
    },
    "adaptive_005": {
        0: [
            ("route_explain", _route("explain")),
            ("no_code", _no_code()),
            ("one_question", _one_question()),
        ],
        2: [
            ("graded_incorrect", _graded("incorrect")),
            ("misconception", _misconception("bfs.dfs_for_unweighted_shortest_path")),
            ("reframed", _reaction("reframe")),
            ("one_question", _one_question()),
        ],
        4: [("graded_correct", _graded("correct"))],
        6: [
            ("route_practice", _route("practice")),
            ("practice_graph", _practice(None, GRAPH_FAMILY)),
            ("one_question", _one_question()),
        ],
        8: [
            ("graded_correct", _graded("correct")),
            ("awaits_code", _pending_kind("code_submission")),
        ],
        10: [
            ("route_debug", _route("debug")),
            ("reviewed_in_sandbox", _reviewed()),
            ("misconception", _misconception("bfs.mark_visited_on_dequeue")),
            ("execution_line", _execution(required=True)),
        ],
        12: [
            ("route_practice", _route("practice")),
            ("difficulty_rose", _difficulty_rose(6)),
            ("surfaced_misconception", _surfaced()),
        ],
        14: [("graded_dont_know", _graded("dont_know")), ("assistance_up", _assist_up())],
    },
    # ADDED 2026-10-05 (two examples; with the per-turn checks every example
    # gets they add 18: totals before this date are out of 130, after it 148).
    # A concept the corpus does NOT cover is
    # explained from the tutor's own knowledge, with examples that were run.
    "adaptive_006": {
        0: [
            ("route_explain", _route("explain")),
            ("explained_not_refused", _explained("recursion", "base case")),
            ("no_wrong_label", _topic(None)),
            ("no_dp_in_reply", _never_mentions("dynamic programming")),
            ("says_not_from_corpus", _says("not from the curated material")),
            ("no_citations", _cited(expected=False)),
            ("example_was_run", _says("run in the sandbox")),
        ],
    },
    # ...and one it DOES cover stays grounded in it and cites what it used.
    "adaptive_007": {
        0: [
            ("route_explain", _route("explain")),
            ("explained_not_refused", _explained("binary search", "sorted")),
            ("labelled_from_the_question", _topic("binary_search")),
            ("cites_references", _cited(expected=True)),
            ("example_was_run", _says("run in the sandbox")),
        ],
    },
}

#: Checks on `/profile` after the conversation: misconception ids remembered.
PROFILE_ERRORS: Final[dict[str, tuple[str, ...]]] = {
    "adaptive_002": ("hashing.checked_key_vs_accessed_key",),
    "adaptive_004": ("trees.returnable_vs_global_path",),
    "adaptive_005": ("bfs.dfs_for_unweighted_shortest_path", "bfs.mark_visited_on_dequeue"),
}


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------


@dataclass(slots=True)
class CheckResult:
    example: str
    turn: int
    name: str
    passed: bool
    detail: str = ""


@dataclass(slots=True)
class ExampleResult:
    example: str
    checks: list[CheckResult]
    turns: list[Turn]

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)


def load_examples(path: Path = EXAMPLES_PATH) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


async def replay_example(api: Api, example: dict[str, Any], pace: float = 0.0) -> ExampleResult:
    example_id = str(example["conversation_id"])
    mode = MODES.get(example_id, "balanced")
    await api.fresh_account(example_id.replace("adaptive_", "ad"))
    table = CHECKS.get(example_id, {})
    conversation_id: str | None = None
    turns: list[Turn] = []
    by_index: dict[int, Turn] = {}
    results: list[CheckResult] = []
    for index, message in enumerate(example["conversation"]):
        if message["role"] != "user":
            continue
        if pace and turns:
            # Pacing only (free-tier LLM rate limits); never changes a check.
            await asyncio.sleep(pace)
        turn = await api.turn(
            f"{example_id}#{index}", mode, str(message["content"]), conversation_id
        )
        if conversation_id is None and turn.body.get("conversation_id"):
            conversation_id = str(turn.body["conversation_id"])
        turns.append(turn)
        by_index[index] = turn
        history = [by_index.get(i, turn) for i in range(index + 1)]
        # Hard gates on EVERY learner turn.
        results.append(
            CheckResult(example_id, index, "no_error", turn.error is None, str(turn.error))
        )
        ok, detail = hint_safe(turn)
        results.append(CheckResult(example_id, index, "hint_safety", ok, detail))
        ok, detail = execution_lines_ok(turn, required=False)
        results.append(CheckResult(example_id, index, "execution_lines_match", ok, detail))
        for name, predicate in table.get(index, []):
            try:
                ok, detail = predicate(turn, history)
            except Exception as exc:  # noqa: BLE001 - a malformed frame is a failed check
                ok, detail = False, f"{type(exc).__name__}: {exc}"
            results.append(CheckResult(example_id, index, name, ok, detail))
    wanted = PROFILE_ERRORS.get(example_id, ())
    if wanted:
        try:
            profile = await api.profile()
            remembered = list(profile.get("common_errors") or [])
        except Exception as exc:  # noqa: BLE001
            remembered = [f"{type(exc).__name__}"]
        for misconception_id in wanted:
            results.append(
                CheckResult(
                    example_id,
                    -1,
                    f"profile_remembers:{misconception_id}",
                    misconception_id in remembered,
                    f"common_errors={remembered}",
                )
            )
    return ExampleResult(example_id, results, turns)


def summarize(results: Sequence[ExampleResult]) -> dict[str, Any]:
    checks = [c for r in results for c in r.checks]
    passed = sum(c.passed for c in checks)
    gates = [c for c in checks if c.name in ("hint_safety", "execution_lines_match", "no_error")]
    return {
        "checks_passed": passed,
        "checks_total": len(checks),
        "check_rate": round(passed / len(checks), 4) if checks else 0.0,
        "examples_passed": sum(r.passed for r in results),
        "examples_total": len(results),
        "hard_gate_failures": [f"{c.example}#{c.turn}:{c.name}" for c in gates if not c.passed],
        "by_example": {
            r.example: {
                "passed": sum(c.passed for c in r.checks),
                "total": len(r.checks),
                "failed": [
                    f"#{c.turn} {c.name}: {c.detail[:160]}" for c in r.checks if not c.passed
                ],
            }
            for r in results
        },
    }


def _dump_turn(turn: Turn) -> dict[str, Any]:
    return {
        "probe": turn.probe,
        "route": turn.route,
        "stages": turn.stages,
        "error": turn.error,
        "seconds": turn.seconds,
        "tutoring": tutoring(turn),
        "verification": turn.body.get("verification"),
        "assistance": turn.generated.get("assistance_level"),
        "reveals_code": turn.reveals_code,
        "section_kinds": [s.get("kind") for s in turn.sections],
    }


def _write(out: Path, record: dict[str, Any]) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")


async def run(args: argparse.Namespace) -> int:
    examples = load_examples()
    if args.only:
        examples = [e for e in examples if e["conversation_id"] in args.only]
    api = Api(args.base_url, args.timeout)
    results: list[ExampleResult] = []
    try:
        for example in examples:
            result = await replay_example(api, example, args.pace)
            results.append(result)
            ok = sum(c.passed for c in result.checks)
            print(f"{result.example}: {ok}/{len(result.checks)} checks", flush=True)
    finally:
        await api.close()
    summary = summarize(results)
    print(json.dumps({k: v for k, v in summary.items() if k != "by_example"}, indent=2))
    for example_id, info in summary["by_example"].items():
        for failure in info["failed"]:
            print(f"  FAIL {example_id} {failure}")
    if args.out:
        record = {
            "started_at": datetime.now(UTC).isoformat(),
            "summary": summary,
            "turns": [_dump_turn(t) for r in results for t in r.turns],
        }
        _write(Path(args.out), record)
    if args.gate is not None:
        failed = summary["check_rate"] < args.gate or bool(summary["hard_gate_failures"])
        return 1 if failed else 0
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--only", nargs="*", default=None, help="example ids to replay")
    parser.add_argument(
        "--pace", type=float, default=0.0, help="seconds between turns (LLM rate limits)"
    )
    parser.add_argument("--out", default=None, help="write the JSON record here")
    parser.add_argument(
        "--gate",
        type=float,
        default=None,
        help="exit 1 when the check rate is below this, or any hard gate fails",
    )
    return asyncio.run(run(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
