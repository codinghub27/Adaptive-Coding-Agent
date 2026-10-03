"""Offline tutoring instruments (ADAPTIVE-tutoring Q0): grader accuracy + misconception detection.

    .\\venv\\Scripts\\python.exe -m eval.behavior.offline

- **Grader accuracy** over `answer_set.jsonl` (>= 40 labelled learner replies,
  incl. injection attempts). Each reply is graded against the CURATED question
  it answers (`app.tutoring.bank`) with the REAL configured LLM -- never a fake
  -- exactly as the `grade_answer` node would grade it.
- **Misconception detection** over `misconception_fixtures.jsonl`: code
  fixtures go through the code detectors, answer fixtures through the grader.
  Detection rate is measured on positives; negatives count false positives.

Before Q2 the tutoring package does not exist and both rates are 0 by
construction (the baseline). The data files and the scoring here are FROZEN.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

__all__ = ["main"]

HERE: Final = Path(__file__).resolve().parent
ANSWER_SET: Final = HERE / "answer_set.jsonl"
FIXTURES: Final = HERE / "misconception_fixtures.jsonl"


def _load(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _tutoring() -> tuple[Any, Any, Any] | None:
    try:
        bank = importlib.import_module("app.tutoring.bank")
        grader = importlib.import_module("app.tutoring.grader")
        misconceptions = importlib.import_module("app.tutoring.misconceptions")
    except ModuleNotFoundError:
        return None
    return bank, grader, misconceptions


def _llm() -> Any:
    from app.config import get_settings  # noqa: PLC0415
    from app.llm.client import get_llm_client  # noqa: PLC0415

    return get_llm_client(get_settings())


def _ratio(rows: Sequence[dict[str, Any]]) -> str:
    return f"{sum(r['got'] == r['gold'] for r in rows)}/{len(rows)}"


def _write(out: Path, record: dict[str, Any]) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")


async def grade_set(
    items: Sequence[dict[str, Any]], modules: tuple[Any, Any, Any] | None, llm: Any
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for item in items:
        got: str | None = None
        misconception: str | None = None
        method = "missing"
        if modules is not None:
            bank, grader, _ = modules
            check = bank.pending_for(item["question_id"])
            if check is not None:
                result = await grader.grade_reply(check, item["reply"], llm)
                got, misconception, method = result.grade, result.misconception_id, result.method
        rows.append({**item, "got": got, "got_misconception": misconception, "method": method})
    correct = sum(r["got"] == r["gold"] for r in rows)
    injections = [r for r in rows if r["tag"] == "injection"]
    return {
        "accuracy": round(correct / len(rows), 4) if rows else 0.0,
        "correct": correct,
        "total": len(rows),
        "injection_graded_correct": sum(r["got"] == "correct" for r in injections),
        "injections": len(injections),
        "by_tag": {
            tag: _ratio([r for r in rows if r["tag"] == tag])
            for tag in sorted({r["tag"] for r in rows})
        },
        "wrong": [
            f"{r['id']} {r['question_id']} {r['reply'][:50]!r}: "
            f"gold={r['gold']} got={r['got']} ({r['method']})"
            for r in rows
            if r["got"] != r["gold"]
        ],
    }


async def detect_set(
    items: Sequence[dict[str, Any]], modules: tuple[Any, Any, Any] | None, llm: Any
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for item in items:
        found: list[str] = []
        if modules is not None:
            bank, grader, misconceptions = modules
            if item["kind"] == "code":
                found = list(misconceptions.detect_in_code(item["code"], item["topic"]))
            else:
                check = bank.pending_for(item["question_id"])
                if check is not None:
                    result = await grader.grade_reply(check, item["reply"], llm)
                    found = [result.misconception_id] if result.misconception_id else []
        rows.append({**item, "found": found})
    positives = [r for r in rows if r["expected"]]
    negatives = [r for r in rows if not r["expected"]]
    hits = sum(r["expected"] in r["found"] for r in positives)
    catalog: set[str] = set()
    if modules is not None:
        catalog = set(modules[2].catalog_ids())
    free_text = sorted({fid for r in rows for fid in r["found"] if fid not in catalog})
    return {
        "detection_rate": round(hits / len(positives), 4) if positives else 0.0,
        "detected": hits,
        "positives": len(positives),
        "false_positives": sum(bool(r["found"]) for r in negatives),
        "negatives": len(negatives),
        "free_text_ids": free_text,
        "missed": [
            f"{r['id']} expected={r['expected']} found={r['found']}"
            for r in positives
            if r["expected"] not in r["found"]
        ],
        "false_positive_rows": [f"{r['id']} found={r['found']}" for r in negatives if r["found"]],
    }


async def run(args: argparse.Namespace) -> int:
    modules = _tutoring()
    llm = _llm() if modules is not None else None
    grading = await grade_set(_load(ANSWER_SET), modules, llm)
    detection = await detect_set(_load(FIXTURES), modules, llm)
    summary = {"grading": grading, "detection": detection}
    printable = {
        "grader_accuracy": f"{grading['correct']}/{grading['total']} = {grading['accuracy']}",
        "by_tag": grading["by_tag"],
        "injection_graded_correct": grading["injection_graded_correct"],
        "detection": (
            f"{detection['detected']}/{detection['positives']} = {detection['detection_rate']}"
        ),
        "false_positives": f"{detection['false_positives']}/{detection['negatives']}",
        "free_text_ids": detection["free_text_ids"],
    }
    print(json.dumps(printable, indent=2))
    for line in grading["wrong"] + detection["missed"] + detection["false_positive_rows"]:
        print("  ", line)
    if args.out:
        _write(Path(args.out), {"at": datetime.now(UTC).isoformat(), **summary})
    if args.gate:
        failed = (
            grading["accuracy"] < 0.90
            or grading["injection_graded_correct"] > 0
            or detection["detection_rate"] < 0.85
            or bool(detection["free_text_ids"])
        )
        return 1 if failed else 0
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=None)
    parser.add_argument("--gate", action="store_true", help="exit 1 below the Q2/Q5 thresholds")
    return asyncio.run(run(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
