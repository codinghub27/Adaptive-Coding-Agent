"""Pending-state probes (ADAPTIVE-tutoring Q1), on the LIVE API.

    .\\venv\\Scripts\\python.exe -m eval.behavior.pending_probe

1. **Replies route to `grade_answer`.** Read from a replay record
   (`--replay eval/results/<run>.json`): every learner turn that follows a
   turn ending with a pending QUESTION must have taken the `grade` route.
2. **A new problem clears the pending check.** Fresh account: ask the BFS/DFS
   concept question (leaves a pending question), then paste a brand-new
   problem statement. That turn must not be graded, and the pending check it
   ends with must not be the old question. Repeated for a confident practice
   request and for an explicit hint request.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from eval.transcript_probes import T1_TEXT, Api, Turn

__all__ = ["main"]

SETUP: Final = "I keep getting confused about when to use BFS versus DFS."
INTERRUPTS: Final[tuple[tuple[str, str], ...]] = (
    ("new_problem", T1_TEXT),
    ("practice_request", "Give me a two pointer problem to practise."),
    ("hint_request", "Can you give me a hint instead?"),
)


def _pending_id(turn: Turn) -> str | None:
    pending = (turn.body.get("tutoring") or {}).get("pending") or {}
    return pending.get("question_id")


def _pending_kind(record_turn: dict[str, Any]) -> str | None:
    pending = (record_turn.get("tutoring") or {}).get("pending") or {}
    return pending.get("kind")


def replies_routed(record: dict[str, Any]) -> tuple[int, int, list[str]]:
    """(graded replies, replies after a pending question, misses) from a replay record."""
    turns = record["turns"]
    total = graded = 0
    misses: list[str] = []
    for prev, turn in zip(turns, turns[1:], strict=False):
        if prev["probe"].split("#")[0] != turn["probe"].split("#")[0]:
            continue
        if _pending_kind(prev) != "question":
            continue
        if turn["route"] == "practice":
            # A new-problem request after a question is not an answer to it
            # (and must clear it -- probe 2); it is reported, not counted.
            misses.append(f"{turn['probe']} route=practice (new request, not a reply)")
            continue
        total += 1
        if turn["route"] == "grade":
            graded += 1
        else:
            misses.append(f"{turn['probe']} route={turn['route']}")
    return graded, total, misses


async def clears(api: Api) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    for name, text in INTERRUPTS:
        await api.fresh_account(f"pend_{name}")
        first = await api.turn("setup", "balanced", SETUP, None)
        conversation = str(first.body.get("conversation_id"))
        before = _pending_id(first)
        second = await api.turn(name, "balanced", text, conversation)
        after = _pending_id(second)
        ok = before is not None and second.route != "grade" and after != before
        results.append((name, ok, f"before={before} route={second.route} after={after}"))
    return results


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


async def run(args: argparse.Namespace) -> int:
    failed = False
    if args.replay:
        record = _load(Path(args.replay))
        graded, total, misses = replies_routed(record)
        print(f"replies routed to grade_answer: {graded}/{total}")
        for miss in misses:
            print("  MISS", miss)
        failed = failed or graded != total
    api = Api(args.base_url, args.timeout)
    try:
        outcomes = await clears(api)
    finally:
        await api.close()
    cleared = sum(ok for _n, ok, _d in outcomes)
    print(f"new requests clear the pending question: {cleared}/{len(outcomes)}")
    for name, ok, detail in outcomes:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}: {detail}")
    failed = failed or cleared != len(outcomes)
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--replay", default=None, help="a replay JSON record to read routes from")
    return asyncio.run(run(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
