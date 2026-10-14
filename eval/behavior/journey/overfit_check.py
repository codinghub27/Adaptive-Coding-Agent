"""The NO-OVERFITTING gate: no dataset text in the agent's code or prompts.

Behaviour must come from general rules, never from the journey's own
problems, learner lines or replies (`docs/features/ADAPTIVE-journey-eval.md`).
This scans `app/` for:

- the dev and held-out PROBLEM names (`*.py` only: the knowledge corpus and
  the curated bank name classic problems on their own, and predate the set);
- any learner line or sentence of three words or more (code only: shorter
  ones collide with the problem titles the corpus lists), and any sentence
  of a reference reply of eight words or more (every file under `app/`),
  matched after normalising case, quotes and spacing.

Text already present in the same file on `main` (written before the
journey dataset existed) is reported as a baseline, not as a hit: it cannot
have been tuned on the set. Exit status 1 on any NEW hit.

    python -m eval.behavior.journey.overfit_check
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DATASETS = (
    ROOT / "eval/behavior/journey/adaptive_journey_10.jsonl",
    ROOT / "eval/behavior/journey/heldout/heldout_journey_10.jsonl",
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_CODE = re.compile(r"```.*?```", re.S)


def _norm(text: str) -> str:
    text = text.lower().replace("’", "'").replace("“", '"').replace("”", '"')
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", text).split())


def needles() -> tuple[set[str], set[str]]:
    """(problem names, phrases)."""
    names: set[str] = set()
    phrases: set[str] = set()
    for path in DATASETS:
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            for key in ("problem", "title"):
                if record.get(key) and key == "problem":
                    names.add(_norm(str(record[key])))
            for message in record["conversation"]:
                prose = _CODE.sub(" ", message["content"])
                if message["role"] == "user":
                    for part in [prose, *_SENTENCE.split(prose)]:
                        words = _norm(part).split()
                        if len(words) >= 3:
                            phrases.add(" ".join(words))
                else:
                    for part in _SENTENCE.split(prose):
                        words = _norm(part).split()
                        if len(words) >= 8:
                            phrases.add(" ".join(words))
    return names, phrases


def _on_main(rel: str) -> str:
    """The file as it is on `main`, normalised; empty when it is new."""
    shown = subprocess.run(
        ["git", "show", f"main:{rel}"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return _norm(shown.stdout) if shown.returncode == 0 else ""


def scan() -> list[str]:
    names, phrases = needles()
    long_phrases = {p for p in phrases if len(p.split()) >= 8}
    hits: list[str] = []
    for path in sorted((ROOT / "app").rglob("*")):
        if not path.is_file() or path.suffix not in {".py", ".json", ".md", ".txt"}:
            continue
        text = _norm(path.read_text(encoding="utf-8", errors="ignore"))
        rel = path.relative_to(ROOT).as_posix()
        found: list[tuple[str, str]] = []
        if path.suffix == ".py":
            # Short learner lines collide with problem titles the corpus and
            # bank list on their own; they are checked in code only.
            found += [("problem name", n) for n in names if n and f" {n} " in f" {text} "]
            found += [("dataset text", p) for p in phrases if f" {p} " in f" {text} "]
        else:
            found += [("dataset text", p) for p in long_phrases if f" {p} " in f" {text} "]
        if found:
            before = f" {_on_main(rel)} "
            hits += [f"{rel}: {kind} {n!r}" for kind, n in found if f" {n} " not in before]
    return hits


def main() -> int:
    hits = scan()
    for hit in hits:
        print(hit)
    print(f"overfitting check: {len(hits)} hit(s)")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
