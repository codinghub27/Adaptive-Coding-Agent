"""Replay the 10-session learner journey through the LIVE API and score it.

    # server: EVAL_MODE=true python -m uvicorn app.main:app --port 8000
    python -m eval.behavior.journey.replay_journey --dataset dev --runs 3 --label baseline

ONE fresh account plays all ten sessions in order, so the learner profile
carries from one to the next. Each session is a new conversation, sent with
the record's teaching mode and with the clock placed on the record's `day`
(`eval_now`, EVAL_MODE only). The `topic` form field is never sent.

The replay is teacher-forced: the scripted learner turns are sent whatever the
agent answered. A reply after which the next scripted turn no longer makes
sense is a DIVERGENCE, scored on its own.

Three layers per assistant turn (`checks.py`, `judge.py`):
  L1 state     the `decision` record against the expected `meta`
  L2 response  what can be read off the reply text
  L3 judge     a six-dimension rubric, 1-5 (counts only once calibrated)
and after each session the PROFILE TRAJECTORY: skill labels and values against
`profile_after`, and the recorded mistakes.

Numbers from this file are comparable only with numbers from this file.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import secrets
import statistics
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import httpx

from eval.behavior.journey import checks
from eval.behavior.journey.judge import Judge, JudgeInput

HERE: Final = Path(__file__).resolve().parent
ROOT: Final = HERE.parents[2]
RESULTS: Final = ROOT / "eval" / "results" / "journey"
DATASETS: Final = {
    "dev": HERE,
    "heldout": HERE / "heldout",
}
VERIFY_IMAGE: Final = "python:3.11-slim-bookworm"
PRIOR: Final = 0.5
SAFE_FALLBACK_MARK: Final = "could not be completed"

_CODE: Final = re.compile(r"```|^\s*def \w+", re.MULTILINE)
_FIX_TAGS: Final = frozenset(
    {"learner_writes_the_fix", "show_failing_case_not_the_fix", "point_at_line_not_rewrite"}
)

Json = dict[str, Any]


# --- dataset ----------------------------------------------------------------------


def dataset_file(folder: Path) -> Path:
    found = sorted(folder.glob("*.jsonl"))
    if len(found) != 1:
        raise SystemExit(f"expected one .jsonl in {folder}, found {len(found)}")
    return found[0]


def load(folder: Path) -> tuple[list[Json], Json]:
    records = [
        json.loads(line)
        for line in dataset_file(folder).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    truth: Json = json.loads((folder / "sandbox_ground_truth.json").read_text(encoding="utf-8"))
    return records, truth


def verify_ground_truth(folder: Path) -> tuple[bool, str]:
    """Re-run the dataset's snippets and compare with the stored ground truth.

    The snippets are run in a throwaway container with no network and a
    read-only root: nothing from a dataset is executed on the host.
    """
    with tempfile.TemporaryDirectory() as scratch:
        command = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--memory",
            "256m",
            "--pids-limit",
            "64",
            "--cap-drop",
            "ALL",
            "-v",
            f"{folder}:/dataset:ro",
            "-v",
            f"{scratch}:/out",
            "-w",
            "/out",
            VERIFY_IMAGE,
            "python",
            "/dataset/verify_examples.py",
        ]
        try:
            done = subprocess.run(command, capture_output=True, text=True, timeout=180, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"could not run the verifier: {type(exc).__name__}"
        produced = Path(scratch) / "sandbox_ground_truth.json"
        if done.returncode != 0 or not produced.exists():
            return False, f"verifier exited {done.returncode}: {done.stderr[-300:]}"
        fresh: Json = json.loads(produced.read_text(encoding="utf-8"))
    stored: Json = json.loads((folder / "sandbox_ground_truth.json").read_text(encoding="utf-8"))
    if fresh != stored:
        changed = sorted(k for k in set(fresh) | set(stored) if fresh.get(k) != stored.get(k))
        return False, f"ground truth differs for {changed}"
    return True, f"{len(fresh)} snippets match the stored ground truth"


def pairs(record: Json) -> list[tuple[str, Json]]:
    """(learner message, the assistant turn that answers it), in order."""
    out: list[tuple[str, Json]] = []
    pending: str | None = None
    for turn in record["conversation"]:
        if turn["role"] == "user":
            pending = turn["content"]
        elif pending is not None:
            out.append((pending, turn))
            pending = None
    return out


def learner_state(record: Json) -> str:
    before: Json = record.get("profile_before") or {}
    skills = {k: checks.label(v) for k, v in (before.get("skills") or {}).items()}
    return (
        f"Day {record['day']} of a 30-day journey, session '{record['title']}'. "
        f"Skills so far: {skills or 'none recorded'}. "
        f"Recorded mistakes: {before.get('common_errors') or 'none'}. "
        f"Preferences: {before.get('learning_preferences') or {}}."
    )


# --- the API ----------------------------------------------------------------------


class Api:
    def __init__(self, base_url: str, timeout: float, pace: float) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=timeout)
        self._pace = pace
        self._headers: dict[str, str] = {}
        self._account: dict[str, str] = {}

    def new_account(self) -> str:
        self._account = {
            "username": "jrn_" + secrets.token_hex(5),
            "password": secrets.token_urlsafe(18) + "aA1!",
        }
        self._client.post("/auth/register", json=self._account).raise_for_status()
        self._login()
        return self._account["username"]

    def _login(self) -> None:
        # A local-model journey runs for hours; the access token does not.
        token = self._client.post("/auth/login", json=self._account).json()["access_token"]
        self._headers = {"Authorization": f"Bearer {token}"}

    def chat(self, text: str, mode: str, conversation_id: str | None, moment: datetime) -> Json:
        data = {"text": text, "teaching_mode": mode, "eval_now": moment.isoformat()}
        if conversation_id:
            data["conversation_id"] = conversation_id
        last: Exception | None = None
        for attempt in range(3):
            started = time.perf_counter()
            try:
                response = self._client.post("/chat", data=data, headers=self._headers)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                last = exc
                if exc.response.status_code == 401 and self._account:
                    self._login()  # the token expired mid-journey: same account
                    continue
                time.sleep(15.0 * (attempt + 1))
                continue
            except httpx.HTTPError as exc:
                last = exc
                time.sleep(15.0 * (attempt + 1))
                continue
            body: Json = response.json()
            body["_seconds"] = round(time.perf_counter() - started, 2)
            if self._pace > 0:
                time.sleep(self._pace)
            return body
        raise RuntimeError(f"/chat failed three times: {last!r}")

    def profile(self, moment: datetime) -> Json:
        params = {"eval_now": moment.isoformat()}
        response = self._client.get("/profile", params=params, headers=self._headers)
        if response.status_code == 401 and self._account:
            self._login()
            response = self._client.get("/profile", params=params, headers=self._headers)
        response.raise_for_status()
        return response.json()


def degraded(body: Json) -> bool:
    """The turn did not get a model's reading (rate limit, node error)."""
    decision: Json = body.get("decision") or {}
    return (
        bool(body.get("errors"))
        or decision.get("intent_source") == "fallback"
        or SAFE_FALLBACK_MARK in str(body.get("response") or "")
    )


# --- one run ----------------------------------------------------------------------


def replay(api: Api, records: Sequence[Json], only: set[int]) -> Json:
    """Play every session on one account; return the RAW run.

    Nothing is scored here: `score` reads the raw turns, so a saved run can be
    scored again when a check changes and every run is scored by one version
    of the instrument.
    """
    username = api.new_account()
    # The journey ends "now": day 30 is today, so no stamp lies in the future.
    last_day = max(record["day"] for record in records)
    start = datetime.now(UTC).replace(microsecond=0) - timedelta(days=last_day)
    sessions: list[Json] = []
    used_days: dict[int, int] = defaultdict(int)
    for number, record in enumerate(records, start=1):
        if only and number not in only:
            continue
        day = record["day"]
        moment = start + timedelta(days=day, hours=2 * used_days[day])
        used_days[day] += 1
        conversation_id: str | None = None
        turns: list[Json] = []
        for index, (message, expected) in enumerate(pairs(record)):
            moment += timedelta(minutes=2)
            body = api.chat(message, record["teaching_mode"].lower(), conversation_id, moment)
            conversation_id = body["conversation_id"]
            turns.append(
                {
                    "index": index,
                    "learner": message,
                    "reference": expected["content"],
                    "meta": expected.get("meta") or {},
                    "response": str(body.get("response") or ""),
                    "decision": body.get("decision"),
                    "errors": body.get("errors") or [],
                    "llm_calls": body.get("llm_calls"),
                    "seconds": body["_seconds"],
                    "degraded": degraded(body),
                }
            )
            print(
                f"  s{number:02d} t{index} route={body.get('route')} {body['_seconds']}s",
                flush=True,
            )
        after = api.profile(moment + timedelta(minutes=1))
        sessions.append(
            {
                "number": number,
                "conversation_id": record["conversation_id"],
                "title": record["title"],
                "day": day,
                "mode": record["teaching_mode"],
                "tags": sorted(record["agent_behavior"]),
                "learner_state": learner_state(record),
                "expected_outcome": record["expected_outcome"],
                "turns": turns,
                "profile_view": after,
                "profile": trajectory(record, after),
            }
        )
    return {"account": username, "sessions": sessions}


def score(run: Json, records: Sequence[Json], truth: Json) -> None:
    """Fill every turn's `checks`, `diverged_rule` and `off_script` from the raw run."""
    by_id = {record["conversation_id"]: record for record in records}
    #: Facts the agent may cite: whatever an EARLIER turn of this journey
    #: recorded (a mistake id, a topic with an event).
    known: set[str] = set()
    for session in run["sessions"]:
        record = by_id[session["conversation_id"]]
        expected_turns = pairs(record)
        code_turns = [i for i, (message, _) in enumerate(expected_turns) if _CODE.search(message)]
        levels = {
            (t.get("meta") or {}).get("plan", {}).get("assistance_level") for _, t in expected_turns
        }
        guards_fix = "full" not in levels and bool(code_turns)
        tags = set(record["agent_behavior"])
        title = str(record.get("problem") or "")
        learner_code = ""
        off_script = False
        if "profile_view" in session:
            session["profile"] = trajectory(record, session["profile_view"])
        for turn in session["turns"]:
            index: int = turn["index"]
            message: str = turn["learner"]
            meta: Json = turn["meta"]
            reply: str = turn["response"]
            body: Json = {"response": reply, "decision": turn["decision"]}
            sandbox_expected: Json = meta.get("sandbox") or {}
            if _CODE.search(message):
                learner_code = message
            review = "evidence_based_progress_summary" in tags and any(
                "skills" in fact for fact in meta.get("memory_used") or []
            )
            results = checks.l1_checks(meta, body, learner_text=message, ground_truth=truth)
            results += checks.l2_checks(
                meta,
                body,
                learner_writes_fix=guards_fix and index < code_turns[-1],
                progress_review=review,
                known_facts=set(known),
                reference_questions=checks.question_count(turn["reference"]),
                learner_code=learner_code,
                failing_run=sandbox_expected.get("passed", 0) < sandbox_expected.get("total", 0),
                on_a_problem=bool(title),
            )
            decision: Json = turn["decision"] or {}
            for event in decision.get("evidence") or []:
                known.add(f"skills.{event['topic']}")
                known.update(f"common_errors.{tag}" for tag in event.get("errors") or [])
            known.update(f"common_errors.{tag}" for tag in decision.get("misconceptions") or [])
            pending = (meta.get("pending_check") or {}).get("kind")
            # The scripted learner goes on to work on the record's problem. A
            # reply that set a DIFFERENT problem (where the reference names
            # this one) leaves every later scripted turn talking past it.
            other_problem = (
                bool(title) and title in turn["reference"] and title.lower() not in reply.lower()
            )
            diverged = (
                (pending == "question" and checks.question_count(reply) == 0)
                or (pending == "code_submission" and checks.shows_solution_code(reply))
                or other_problem
            ) and index + 1 < len(expected_turns)
            off_script = off_script or other_problem
            turn["diverged_rule"] = bool(diverged)
            #: The agent set a different problem earlier in this session: later
            #: misses here follow from that one, not from this turn.
            turn["off_script"] = off_script
            turn["checks"] = [
                {
                    "name": r.name,
                    "layer": r.layer,
                    "ok": r.ok,
                    "detail": r.detail,
                    "blocked_by": r.blocked_by,
                    "hard_gate": r.hard_gate,
                }
                for r in results
            ]


def trajectory(record: Json, profile: Json) -> Json:
    """Agent profile after the session against the record's `profile_after`."""
    expected: Json = record["profile_after"]
    skills: Json = profile.get("skill_levels") or {}
    counts: Json = profile.get("common_error_counts") or {}
    rows: list[Json] = []
    for topic, value in expected["skills"].items():
        key = checks.VOCAB["topics"].get(topic, topic)
        actual = float(skills.get(key, PRIOR))
        rows.append(
            {
                "topic": topic,
                "expected": value,
                "actual": round(actual, 4),
                "present": key in skills,
                "label_ok": checks.label(actual) == checks.label(value),
                "abs_error": round(abs(actual - value), 4),
            }
        )
    errors: list[Json] = []
    for tag, state in (expected.get("common_errors") or {}).items():
        mapped = checks.VOCAB["misconceptions"].get(tag)
        if mapped is None:
            errors.append({"id": tag, "ok": False, "blocked_by": "catalog"})
        elif state == "resolved":
            # The agent's profile has no "resolved" state for a mistake.
            errors.append({"id": tag, "ok": False, "blocked_by": "no_resolved_state"})
        else:
            errors.append({"id": tag, "ok": mapped in counts, "blocked_by": ""})
    extra = sorted(set(skills) - {checks.VOCAB["topics"].get(t, t) for t in expected["skills"]})
    return {"skills": rows, "common_errors": errors, "extra_skill_keys": extra}


# --- L3 ---------------------------------------------------------------------------


async def judge_run(run: Json, judge: Judge) -> None:
    for session in run["sessions"]:
        history: list[tuple[str, str]] = []
        turns: list[Json] = session["turns"]
        for position, turn in enumerate(turns):
            following = turns[position + 1]["learner"] if position + 1 < len(turns) else None
            score = await judge.score(
                JudgeInput(
                    learner_state=session["learner_state"],
                    teaching_mode=session["mode"],
                    behaviours=tuple(session["tags"]),
                    expected_outcome=session["expected_outcome"],
                    history=tuple(history),
                    learner_message=turn["learner"],
                    reference_reply=turn["reference"],
                    actual_reply=turn["response"],
                    next_learner_message=following,
                )
            )
            turn["judge"] = (
                {
                    "scores": score.scores,
                    "mean": round(score.mean, 3),
                    "next_turn_fits": score.next_turn_fits,
                    "why": score.why,
                }
                if score is not None
                else None
            )
            history += [("learner", turn["learner"]), ("tutor", turn["response"])]
    run["judge_model"] = judge.model


# --- scoring ----------------------------------------------------------------------


def _rate(items: Sequence[bool]) -> float | None:
    return round(100.0 * sum(items) / len(items), 1) if items else None


def summarize(run: Json) -> Json:
    """Every headline number of one run, from its raw record."""
    layer: dict[str, list[bool]] = defaultdict(list)
    by_check: dict[str, list[bool]] = defaultdict(list)
    by_session: dict[int, dict[str, list[Any]]] = {}
    by_tag: dict[str, dict[str, list[Any]]] = defaultdict(lambda: defaultdict(list))
    gates: list[str] = []
    blocked: dict[str, int] = defaultdict(int)
    judged: dict[str, list[int]] = defaultdict(list)
    seconds: list[float] = []
    diverged: list[bool] = []
    degraded_turns = 0
    turn_count = 0
    for session in run["sessions"]:
        bucket: dict[str, list[Any]] = defaultdict(list)
        by_session[session["number"]] = bucket
        turns: list[Json] = session["turns"]
        for position, turn in enumerate(turns):
            turn_count += 1
            seconds.append(turn["seconds"])
            degraded_turns += turn["degraded"]
            for check in turn["checks"]:
                # `*_strict` and `*_band` are reported, not scored: the scored
                # route is the documented-equivalence one, the scored level exact.
                by_check[check["name"]].append(check["ok"])
                if check["blocked_by"]:
                    blocked[check["blocked_by"]] += 1
                if check["hard_gate"] and not check["ok"]:
                    gates.append(f"s{session['number']} t{turn['index']} {check['name']}")
                if check["name"] in {"route_strict", "intent_strict", "assistance_band"}:
                    continue
                layer[check["layer"]].append(check["ok"])
                bucket[check["layer"]].append(check["ok"])
                for tag in session["tags"]:
                    by_tag[tag]["checks"].append(check["ok"])
            verdict: Json | None = turn.get("judge")
            if verdict:
                for name, value in verdict["scores"].items():
                    judged[name].append(value)
                bucket["L3"].append(verdict["mean"])
                for tag in session["tags"]:
                    by_tag[tag]["L3"].append(verdict["mean"])
            if position + 1 < len(turns):
                fits = verdict.get("next_turn_fits") if verdict else None
                diverged.append(bool(turn["diverged_rule"]) or fits is False)
    skill_rows = [row for s in run["sessions"] for row in s["profile"]["skills"]]
    error_rows = [row for s in run["sessions"] for row in s["profile"]["common_errors"]]
    all_l3 = [v for values in judged.values() for v in values]
    return {
        "turns": turn_count,
        "degraded_turns": degraded_turns,
        "L1": _rate(layer["L1"]),
        "L1_route_strict": _rate(by_check.get("route_strict", [])),
        "L1_intent_strict": _rate(by_check.get("intent_strict", [])),
        "L2": _rate(layer["L2"]),
        "L3": round(statistics.fmean(all_l3), 2) if all_l3 else None,
        "L3_by_dimension": {k: round(statistics.fmean(v), 2) for k, v in judged.items()},
        "divergence": _rate(diverged),
        "profile_label_agreement": _rate([row["label_ok"] for row in skill_rows]),
        "profile_mae": (
            round(statistics.fmean(row["abs_error"] for row in skill_rows), 4)
            if skill_rows
            else None
        ),
        "common_errors": _rate([row["ok"] for row in error_rows]),
        "hard_gate_failures": gates,
        "blocked": dict(blocked),
        "latency_p50": round(statistics.median(seconds), 1) if seconds else None,
        "latency_max": max(seconds) if seconds else None,
        "by_check": {k: _rate(v) for k, v in sorted(by_check.items())},
        "by_check_n": {k: len(v) for k, v in sorted(by_check.items())},
        "by_session": {
            str(number): {
                "L1": _rate(bucket["L1"]),
                "L2": _rate(bucket["L2"]),
                "L3": round(statistics.fmean(bucket["L3"]), 2) if bucket["L3"] else None,
            }
            for number, bucket in sorted(by_session.items())
        },
        "by_tag": {
            tag: {
                "checks": _rate(values["checks"]),
                "L3": round(statistics.fmean(values["L3"]), 2) if values["L3"] else None,
            }
            for tag, values in sorted(by_tag.items())
        },
    }


def _spread(values: Sequence[float | None]) -> Json:
    present = [v for v in values if v is not None]
    if not present:
        return {"mean": None, "min": None, "max": None, "n": 0}
    return {
        "mean": round(statistics.fmean(present), 2),
        "min": min(present),
        "max": max(present),
        "n": len(present),
    }


def aggregate(summaries: Sequence[Json]) -> Json:
    """Mean and spread over runs, for every numeric headline, session and tag."""
    out: Json = {"runs": len(summaries)}
    for key in (
        "L1",
        "L1_route_strict",
        "L1_intent_strict",
        "L2",
        "L3",
        "divergence",
        "profile_label_agreement",
        "profile_mae",
        "common_errors",
        "latency_p50",
        "degraded_turns",
    ):
        out[key] = _spread([s[key] for s in summaries])
    for group, fields in (("by_session", ("L1", "L2", "L3")), ("by_tag", ("checks", "L3"))):
        names = sorted({name for s in summaries for name in s[group]})
        out[group] = {
            name: {f: _spread([s[group].get(name, {}).get(f) for s in summaries]) for f in fields}
            for name in names
        }
    for group in ("by_check", "L3_by_dimension"):
        names = sorted({name for s in summaries for name in s[group]})
        out[group] = {name: _spread([s[group].get(name) for s in summaries]) for name in names}
    out["hard_gate_failures"] = [g for s in summaries for g in s["hard_gate_failures"]]
    return out


def transcript(run: Json) -> str:
    lines: list[str] = []
    for session in run["sessions"]:
        lines.append(
            f"\n# s{session['number']:02d} {session['title']} "
            f"(day {session['day']}, {session['mode']})\n"
        )
        for turn in session["turns"]:
            decision: Json = turn["decision"] or {}
            plan: Json = decision.get("plan") or {}
            failed = [c["name"] for c in turn["checks"] if not c["ok"]]
            verdict: Json | None = turn.get("judge")
            lines.append(
                f"\n## s{session['number']:02d} t{turn['index']} [route={decision.get('route')} "
                f"move={decision.get('move')} level={plan.get('assistance_level')} "
                f"grade={(decision.get('grade') or {}).get('grade')} {turn['seconds']}s"
                f"{' DEGRADED' if turn['degraded'] else ''}]"
            )
            lines.append(f"\nFAILED: {', '.join(failed) or 'none'}")
            if verdict:
                lines.append(
                    f"\nJUDGE {verdict['mean']} {verdict['scores']} "
                    f"fits={verdict['next_turn_fits']}: {verdict['why']}"
                )
            lines.append(f"\n**LEARNER:** {turn['learner']}")
            lines.append(f"\n**AGENT:**\n{turn['response']}")
            lines.append(f"\n**REFERENCE:**\n{turn['reference']}")
        lines.append(f"\nPROFILE: {json.dumps(session['profile'])}")
    return "\n".join(lines) + "\n"


#: A run in which more than this share of turns got no model reading (every
#: credential rate-limited) measures the keyword fallback, not the agent. It is
#: kept on disk and left out of the aggregate.
MAX_DEGRADED_SHARE: Final = 0.10


def valid_run(summary: Json) -> bool:
    return summary["degraded_turns"] <= MAX_DEGRADED_SHARE * max(1, summary["turns"])


def write_aggregate(dataset: str, label: str, summaries: Sequence[Json]) -> Json:
    usable = [summary for summary in summaries if valid_run(summary)]
    total = aggregate(usable)
    total["excluded_runs"] = len(summaries) - len(usable)
    if total["excluded_runs"]:
        print(f"{total['excluded_runs']} run(s) excluded: too many rate-limited turns")
    (RESULTS / f"{dataset}_{label}_aggregate.json").write_text(
        json.dumps(total, indent=1), encoding="utf-8"
    )
    print(json.dumps({k: total[k] for k in ("L1", "L2", "L3", "divergence")}, indent=1))
    return total


def rescore(dataset: str, label: str, pace: float, *, with_judge: bool) -> int:
    """Score saved runs again (and judge them when asked); replay nothing.

    Replaying and judging share one provider quota; doing them apart keeps the
    judge from rate-limiting the agent it is measuring.
    """
    files = sorted(
        path
        for path in RESULTS.glob(f"{dataset}_{label}_*.json")
        if not path.name.endswith("_aggregate.json")
    )
    if not files:
        print(f"no saved runs for {dataset}_{label}")
        return 2
    records, truth = load(DATASETS[dataset])
    judge = Judge(pace=pace) if with_judge else None
    summaries: list[Json] = []
    for path in files:
        run: Json = json.loads(path.read_text(encoding="utf-8"))
        score(run, records, truth)
        if judge is not None:
            asyncio.run(judge_run(run, judge))
        run["summary"] = summarize(run)
        path.write_text(json.dumps(run, indent=1), encoding="utf-8")
        path.with_suffix(".md").write_text(transcript(run), encoding="utf-8")
        summaries.append(run["summary"])
        print(
            f"{path.name}: L1 {run['summary']['L1']} L2 {run['summary']['L2']} "
            f"L3 {run['summary']['L3']} ({run.get('judge_model', 'no judge')})",
            flush=True,
        )
    write_aggregate(dataset, label, summaries)
    return 0


# --- CLI --------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay the learner journey live and score it.")
    parser.add_argument("--dataset", choices=sorted(DATASETS), default="dev")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--label", default="run", help="name used in the result files")
    parser.add_argument("--only", default="", help="comma-separated session numbers (1-10)")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--pace", type=float, default=0.0, help="seconds between turns")
    parser.add_argument("--no-judge", action="store_true")
    parser.add_argument("--no-verify", action="store_true")
    parser.add_argument(
        "--rescore",
        action="store_true",
        help="score the saved runs of --dataset/--label again; replay nothing",
    )
    parser.add_argument(
        "--judge-only",
        action="store_true",
        help="like --rescore, and also run the L3 judge over the saved replies",
    )
    parser.add_argument("--gate-l1", type=float, default=None)
    parser.add_argument("--gate-l2", type=float, default=None)
    args = parser.parse_args(argv)

    if args.rescore or args.judge_only:
        return rescore(args.dataset, args.label, args.pace, with_judge=args.judge_only)
    folder = DATASETS[args.dataset]
    records, truth = load(folder)
    if not args.no_verify:
        ok, message = verify_ground_truth(folder)
        print(f"ground truth: {message}")
        if not ok:
            return 2
    only = {int(part) for part in args.only.split(",") if part}
    RESULTS.mkdir(parents=True, exist_ok=True)
    api = Api(args.base_url, args.timeout, args.pace)
    judge = None if args.no_judge else Judge(pace=args.pace)
    summaries: list[Json] = []
    for number in range(1, args.runs + 1):
        print(f"run {number}/{args.runs} ({args.dataset})", flush=True)
        run = replay(api, records, only)
        score(run, records, truth)
        if judge is not None:
            asyncio.run(judge_run(run, judge))
        run["summary"] = summarize(run)
        stem = f"{args.dataset}_{args.label}_{number}"
        (RESULTS / f"{stem}.json").write_text(json.dumps(run, indent=1), encoding="utf-8")
        (RESULTS / f"{stem}.md").write_text(transcript(run), encoding="utf-8")
        summaries.append(run["summary"])
        head = {
            k: run["summary"][k]
            for k in (
                "L1",
                "L2",
                "L3",
                "divergence",
                "profile_label_agreement",
                "profile_mae",
                "degraded_turns",
                "hard_gate_failures",
            )
        }
        print(json.dumps(head), flush=True)
    total = write_aggregate(args.dataset, args.label, summaries)
    failed = bool(total["hard_gate_failures"])
    for key, gate in (("L1", args.gate_l1), ("L2", args.gate_l2)):
        if gate is not None and (total[key]["mean"] or 0.0) < gate:
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
