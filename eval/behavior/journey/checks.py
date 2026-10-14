"""Deterministic checks for the journey replay: L1 (state) and L2 (response).

Every function here is pure: it takes the dataset's expected `meta`, the
agent's `/chat` body (with its `decision` record) and returns named results.
Nothing calls a model. `python -m eval.behavior.journey.checks` runs the L2
checks over the dataset's OWN reference replies -- an instrument that fails
its own reference is measuring the wrong thing.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

HERE: Final = Path(__file__).resolve().parent
VOCAB: Final[dict[str, Any]] = json.loads((HERE / "vocab_map.json").read_text(encoding="utf-8"))

Meta = dict[str, Any]
Body = dict[str, Any]

#: Checks whose failure is a broken invariant, not a missed target.
HARD_GATES: Final = frozenset(
    {
        "hint_safety",
        "pass_claim_backed",
        "solved_only_from_learner_sandbox",
        "revealed_code_verified",
        "revealed_code_no_credit",
    }
)

#: Generic praise that says nothing about what the learner did. A reply may
#: still praise: it has to name the thing ("you named the invariant yourself").
FILLER_PRAISE: Final = (
    "great question",
    "good question",
    "great job",
    "good job",
    "nice job",
    "awesome",
    "amazing",
    "fantastic",
    "excellent work",
    "nice work",
    "great work",
    "well done",
    "you're doing great",
    "you are doing great",
    "keep it up",
    "keep up the good work",
    "you've got this",
    "you got this",
    "don't worry",
    "no worries",
    "great start",
    "great attempt",
    "good attempt",
    "you're a natural",
)

WORD_BUDGET: Final = 180

_FENCE: Final = re.compile(r"```")
_INLINE: Final = re.compile(r"`[^`\n]*`")
_QUOTED: Final = re.compile(r"\"[^\"\n]{0,200}\"|“[^”\n]{0,200}”")
_DEF: Final = re.compile(r"^\s*(def |class )\w+", re.MULTILINE)
_PASS_CLAIM: Final = re.compile(
    r"(\d+)\s*(?:/|of|out of)\s*(\d+)\s*(?:\*\*)?\s*(?:test\s*)?(?:cases?\s*)?(?:tests?\s*)?pass"
    r"|pass(?:ed|es)?\s*(?:\*\*)?\s*(\d+)\s*(?:/|of|out of)\s*(\d+)",
    re.IGNORECASE,
)
_ALL_PASS: Final = re.compile(
    r"\b(all (?:the |of the )?(?:tests?|cases?) pass|passes? (?:all|every)|every (?:test|case) pass"
    r"|it(?:'s| is) correct\b|your (?:code|solution) is correct|ran it)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Result:
    name: str
    layer: str  # "L1" | "L2"
    ok: bool
    detail: str = ""
    #: Set when the check cannot pass for a reason outside the turn itself
    #: (e.g. the closed catalog has no such misconception).
    blocked_by: str = ""

    @property
    def hard_gate(self) -> bool:
        return self.name in HARD_GATES


def prose(text: str) -> str:
    """The reply without fenced code."""
    return "".join(_FENCE.split(text)[0::2])


def code_blocks(text: str) -> list[str]:
    return _FENCE.split(text)[1::2]


def shows_solution_code(text: str) -> bool:
    """A fenced block that defines a function or class."""
    return any(_DEF.search(block) for block in code_blocks(text))


def question_count(text: str) -> int:
    """Question marks the tutor asks: outside code, inline code and quoted text."""
    body = _QUOTED.sub(" ", _INLINE.sub(" ", prose(text)))
    return body.count("?")


def word_count(text: str) -> int:
    return len(prose(text).split())


def pass_claims(text: str) -> list[tuple[int, int]]:
    claims: list[tuple[int, int]] = []
    for match in _PASS_CLAIM.finditer(prose(text)):
        a, b, c, d = match.groups()
        claims.append((int(a), int(b)) if a is not None else (int(c), int(d)))
    return claims


def filler_praise(text: str) -> list[str]:
    body = prose(text).lower().replace("’", "'")
    return [phrase for phrase in FILLER_PRAISE if phrase in body]


_STATEMENT: Final = re.compile(r"[^=!<>]=[^=]|\breturn\b|\bwhile\b|\bif\b.+:")


def _squash(code: str) -> str:
    return re.sub(r"\s+", "", code)


def new_code_lines(text: str, learner_code: str) -> list[str]:
    """Statements of code in the reply that are not in the learner's own code.

    A tutor may quote the learner's line to point at it. A statement that is
    NOT theirs (an assignment, a condition, a return) is a correction.
    """
    theirs = _squash(learner_code)
    fragments = [m.group(0).strip("`") for m in _INLINE.finditer(prose(text))]
    for block in code_blocks(text):
        fragments += [line for line in block.splitlines()[1:] if line.strip()]
    return [
        fragment.strip()
        for fragment in fragments
        if len(_squash(fragment)) >= 10
        and _STATEMENT.search(fragment)
        and _squash(fragment) not in theirs
        # `piles=[3,6,7,11], h=8` is a quoted test input: names bound to literals.
        and re.search(r"[A-Za-z_]", re.sub(r"\b\w+\s*=(?!=)|'[^']*'|\"[^\"]*\"", "", fragment))
    ]


def label(skill: float) -> str:
    bands = VOCAB["skill_labels"]
    if skill < bands["weak_below"]:
        return "weak"
    return "strong" if skill >= bands["strong_from"] else "developing"


def band(level: str | None) -> str | None:
    for name, members in VOCAB["assistance_levels"]["bands"].items():
        if level in members:
            return name
    return None


def route_equivalent(expected: str, actual: str, *, has_code: bool, asks_problem: bool) -> bool:
    if expected == actual:
        return True
    conditions = {
        "always": True,
        "learner_turn_has_code": has_code,
        "learner_asks_for_a_problem": asks_problem,
    }
    return any(
        row["dataset"] == expected and actual in row["agent"] and conditions[row["when"]]
        for row in VOCAB["routes"]["equivalences"]
    )


# --- L1: state --------------------------------------------------------------------


def l1_checks(
    meta: Meta,
    body: Body,
    *,
    learner_text: str,
    ground_truth: dict[str, Any],
) -> list[Result]:
    """Compare the agent's decision with the keys the expected `meta` carries."""
    decision: Meta = body.get("decision") or {}
    out: list[Result] = []
    has_code = "```" in learner_text or bool(_DEF.search(learner_text))
    asks_problem = bool(re.search(r"\b(give me|another|next)\b.*\bproblem\b", learner_text, re.I))

    if "intent" in meta:
        actual = decision.get("intent")
        out.append(
            Result("intent", "L1", actual == meta["intent"], f"{meta['intent']} vs {actual}")
        )

    if "route" in meta:
        actual = str(decision.get("route"))
        out.append(
            Result("route_strict", "L1", actual == meta["route"], f"{meta['route']} vs {actual}")
        )
        out.append(
            Result(
                "route",
                "L1",
                route_equivalent(
                    meta["route"], actual, has_code=has_code, asks_problem=asks_problem
                ),
                f"{meta['route']} vs {actual}",
            )
        )

    expected_level = (meta.get("plan") or {}).get("assistance_level")
    if expected_level:
        actual = (decision.get("plan") or {}).get("assistance_level")
        detail = f"{expected_level} vs {actual}"
        out.append(Result("assistance_level", "L1", actual == expected_level, detail))
        out.append(Result("assistance_band", "L1", band(actual) == band(expected_level), detail))

    expected_difficulty = (meta.get("plan") or {}).get("difficulty")
    if expected_difficulty:
        actual = (decision.get("plan") or {}).get("difficulty")
        out.append(
            Result(
                "difficulty",
                "L1",
                actual == expected_difficulty,
                f"{expected_difficulty} vs {actual}",
            )
        )

    if "grade" in meta:
        actual = (decision.get("grade") or {}).get("grade")
        out.append(Result("grade", "L1", actual == meta["grade"], f"{meta['grade']} vs {actual}"))

    if "misconception" in meta:
        mapped = VOCAB["misconceptions"].get(meta["misconception"])
        found = set(decision.get("misconceptions") or [])
        for event in decision.get("evidence") or []:
            found.update(event.get("errors") or [])
        if mapped is None:
            out.append(
                Result(
                    "misconception",
                    "L1",
                    False,
                    f"{meta['misconception']} is not in the closed catalog",
                    blocked_by="catalog",
                )
            )
        else:
            out.append(
                Result("misconception", "L1", mapped in found, f"{mapped} vs {sorted(found)}")
            )

    for item in meta.get("evidence") or []:
        out.append(_evidence_check(item, decision))

    if "sandbox" in meta:
        out += _sandbox_checks(meta["sandbox"], decision, ground_truth)

    if "escalation" in meta:
        actual = decision.get("escalation")
        # Runs saved before the record's `reason` was corrected say "ladder"
        # for the planner's `escalated`, which is an explicit ask by definition.
        rationale = (decision.get("plan") or {}).get("rationale") or []
        wanted = meta["escalation"]["reason"]
        ok = bool(actual) and (
            actual["reason"] == wanted or (wanted == "explicit_ask" and "escalated" in rationale)
        )
        out.append(Result("escalation", "L1", ok, f"{meta['escalation']['reason']} vs {actual}"))

    if "memory_used" in meta:
        used = decision.get("memory_used") or []
        out.append(Result("memory_used", "L1", bool(used), f"expected stored facts, got {used}"))
    return out


def _evidence_check(item: Meta, decision: Meta) -> Result:
    topic = VOCAB["topics"].get(item["topic"], item["topic"])
    sources = set(VOCAB["evidence_provenance"][item["provenance"]])
    name = f"evidence_{item['provenance']}"
    events = [e for e in decision.get("evidence") or [] if e["topic"] == topic]
    if not events:
        seen = [(e["topic"], e["source"]) for e in decision.get("evidence") or []]
        return Result(name, "L1", False, f"no event on {topic}; got {seen}")
    matching = [e for e in events if e["source"] in sources]
    if item["provenance"] == "exposure":
        # Exposure is the absence of an outcome: whatever the source, nothing
        # may be claimed and the help given must be recorded.
        ok = all(e["solved"] is None for e in events) and (
            not item.get("needed_full_solution") or any(e["needed_full_solution"] for e in events)
        )
        return Result(name, "L1", ok, f"{[(e['source'], e['solved']) for e in events]}")
    if not matching:
        return Result(name, "L1", False, f"{topic}: sources {[e['source'] for e in events]}")
    if item["provenance"] == "sandbox":
        ok = any(e["solved"] is item.get("solved") for e in matching)
        return Result(
            name, "L1", ok, f"solved {item.get('solved')} vs {[e['solved'] for e in matching]}"
        )
    grades = [e["concept_grade"] for e in matching]
    return Result(name, "L1", item.get("grade") in grades, f"{item.get('grade')} vs {grades}")


def _sandbox_checks(expected: Meta, decision: Meta, ground_truth: dict[str, Any]) -> list[Result]:
    actual: Meta = decision.get("sandbox") or {}
    out: list[Result] = []
    want = "pass" if expected["passed"] == expected["total"] else "fail"
    got = actual.get("status")
    if got == "fail" and actual.get("subject") == "reference":
        got = "fail(reference)"
    out.append(Result("sandbox_direction", "L1", got == want, f"{want} vs {got}"))
    on_learner = "agent_reference" not in expected.get("ref", "")
    if actual:
        subject = "learner" if on_learner else "reference"
        ok = actual.get("subject") == subject
        if not on_learner:
            # A reference the tutor wrote and ran is reported on either slot.
            ok = actual.get("status") == "pass" or actual.get("final_status") == "pass"
        out.append(Result("sandbox_subject", "L1", ok, f"{subject} vs {actual.get('subject')}"))
    failing = actual.get("first_failing")
    if want == "fail" and failing:
        truth = ground_truth.get(expected.get("ref", ""), {})
        known = [
            (json.dumps(c["expected"]), json.dumps(c["got"]))
            for c in truth.get("cases", [])
            if not c["pass"]
        ]
        quoted = (json.dumps(failing.get("expected")), json.dumps(failing.get("actual")))
        # A case outside the dataset's own suite cannot be confirmed from here:
        # it is reported as unconfirmed and left out of the score.
        if quoted in known:
            out.append(
                Result("failing_case_real", "L1", True, "matches a ground-truth failing case")
            )
        elif failing.get("expected") == failing.get("actual"):
            out.append(Result("failing_case_real", "L1", False, "quoted case does not fail"))
    return out


# --- L2: response -----------------------------------------------------------------


def l2_checks(
    meta: Meta,
    body: Body,
    *,
    learner_writes_fix: bool,
    progress_review: bool,
    known_facts: set[str] | None = None,
    reference_questions: int = 1,
    learner_code: str = "",
    failing_run: bool = False,
    on_a_problem: bool = True,
) -> list[Result]:
    """Checks read off the reply text, against the agent's own decision."""
    text = str(body.get("response") or "")
    decision: Meta = body.get("decision") or {}
    plan: Meta = decision.get("plan") or {}
    out: list[Result] = []
    actual_level = plan.get("assistance_level")
    expected_level = (meta.get("plan") or {}).get("assistance_level")
    code = shows_solution_code(text)

    pending = meta.get("pending_check") or {}
    if pending.get("kind") == "question":
        # Exactly one, except on the two turns where the reference reply
        # itself asks two (an interviewer's paired follow-up): there the
        # reference's own count is the ceiling.
        count = question_count(text)
        ceiling = max(1, reference_questions)
        out.append(Result("one_question", "L2", 1 <= count <= ceiling, f"{count} questions"))

    # The agent's OWN plan is the hard gate; the dataset's level is the target.
    # The gate applies while a problem is being worked on. A session with no
    # problem (a diagnosis, a drill) may show code as a worked example of a
    # CONCEPT: that misses the target below, but it leaks no solution.
    if on_a_problem:
        out.append(
            Result(
                "hint_safety",
                "L2",
                not code or actual_level == "full" or bool(decision.get("escalation")),
                f"code shown at level {actual_level}",
            )
        )
    if expected_level != "full":
        out.append(Result("no_code_below_full", "L2", not code, "solution code in the reply"))

    claims = pass_claims(text)
    sandbox: Meta = decision.get("sandbox") or {}
    ran = [(sandbox["passed"], sandbox["total"])] if sandbox else []
    # "N/N" may also be the verdict on the tutor's own patch, which the record
    # reports as `final_status` (whether a patch should be SHOWN is
    # `no_corrected_code`'s question, not this one's).
    patch_passed = sandbox.get("final_status") == "pass"
    backed = all(claim in ran or (patch_passed and claim[0] == claim[1]) for claim in claims)
    if claims:
        out.append(Result("pass_claim_matches", "L2", backed, f"claims {claims} vs sandbox {ran}"))
    claims_pass = bool(claims) or bool(_ALL_PASS.search(prose(text)))
    if claims_pass:
        verdict_ran = bool(sandbox) and sandbox.get("status") in {"pass", "fail"}
        out.append(
            Result("pass_claim_backed", "L2", verdict_ran, f"sandbox {sandbox.get('status')}")
        )

    if learner_writes_fix:
        # Before the learner has said what is wrong, a line of code that is
        # not theirs is the fix. After they have, naming the idiom is fine
        # (the reference does), so only whole solutions count then.
        written = new_code_lines(text, learner_code) if failing_run else []
        out.append(
            Result(
                "no_corrected_code",
                "L2",
                not code and not decision.get("reveals_code") and not written,
                f"the fix was written for the learner {written[:2]}",
            )
        )

    if expected_level != "full" and not progress_review:
        words = word_count(text)
        out.append(Result("length_budget", "L2", words <= WORD_BUDGET, f"{words} words"))

    filler = filler_praise(text)
    out.append(Result("no_filler_praise", "L2", not filler, ", ".join(filler)))

    used = decision.get("memory_used") or []
    if used and known_facts is not None:
        missing = [fact for fact in used if fact not in known_facts]
        out.append(Result("memory_facts_exist", "L2", not missing, f"not stored: {missing}"))

    out += _gate_checks(decision)
    return out


def _gate_checks(decision: Meta) -> list[Result]:
    out: list[Result] = []
    sandbox: Meta = decision.get("sandbox") or {}
    events = decision.get("evidence") or []
    solved = [e for e in events if e["solved"] is True]
    if solved:
        ok = (
            all(e["source"] in VOCAB["evidence_provenance"]["sandbox"] for e in solved)
            and sandbox.get("subject") == "learner"
            and sandbox.get("status") == "pass"
        )
        out.append(
            Result(
                "solved_only_from_learner_sandbox",
                "L2",
                ok,
                f"{[e['source'] for e in solved]} {sandbox.get('subject')} {sandbox.get('status')}",
            )
        )
    escalation = decision.get("escalation")
    if escalation and decision.get("reveals_code"):
        out.append(
            Result(
                "revealed_code_verified",
                "L2",
                bool(escalation.get("verified_reference")),
                "revealed code has no sandbox pass",
            )
        )
        gained = {k: v for k, v in (decision.get("skill_deltas") or {}).items() if v > 0}
        out.append(
            Result(
                "revealed_code_no_credit",
                "L2",
                not gained and not solved,
                f"skill moved up: {gained}",
            )
        )
    return out


# --- self-check: the instrument against the dataset's own replies ------------------


def self_check(dataset: Path) -> int:
    """Run the text-only L2 checks on the reference replies; print any miss."""
    failures = 0
    total = 0
    for line in dataset.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        turns = [t for t in record["conversation"] if t["role"] == "assistant"]
        learner = [t["content"] for t in record["conversation"] if t["role"] == "user"]
        for index, turn in enumerate(turns):
            meta: Meta = turn.get("meta") or {}
            text = turn["content"]
            ran: Meta = meta.get("sandbox") or {}
            if ran.get("passed", 0) < ran.get("total", 0):
                written = new_code_lines(text, learner[index])
                total += 1
                if written:
                    failures += 1
                    print(f"{record['conversation_id']} turn {index}: no_corrected_code {written}")
            level = (meta.get("plan") or {}).get("assistance_level")
            named: dict[str, bool] = {"no_filler_praise": not filler_praise(text)}
            if (meta.get("pending_check") or {}).get("kind") == "question":
                named["one_question"] = question_count(text) == 1
            if level != "full":
                named["no_code_below_full"] = not shows_solution_code(text)
                if not (index == len(turns) - 2 and record["session"] == 9):
                    named["length_budget"] = word_count(text) <= WORD_BUDGET
            sandbox = meta.get("sandbox") or {}
            for claim in pass_claims(text):
                named["pass_claim_matches"] = claim == (sandbox.get("passed"), sandbox.get("total"))
            for name, ok in named.items():
                total += 1
                if not ok:
                    failures += 1
                    print(f"{record['conversation_id']} turn {index}: {name}")
    print(f"self-check: {total - failures}/{total} reference checks pass")
    return failures


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "adaptive_journey_10.jsonl"
    sys.exit(1 if self_check(target) else 0)
