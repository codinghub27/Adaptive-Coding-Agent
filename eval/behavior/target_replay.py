"""Replay the target-behaviour conversations through the LIVE API and write them out.

    .\\venv\\Scripts\\python.exe -m eval.behavior.target_replay \\
        --base-url http://127.0.0.1:8000 --out transcripts.md

The three sources are the ones the owner wrote as the target:
`adaptive_examples.jsonl` (same conversations as `docs/target_behavior.md`),
`docs/target_behavior2.md` (a JSON array) and the learner messages of
`docs/current-behavior.md` (the turns that were answered badly). Only the
LEARNER turns are sent, each conversation on a fresh account. The output puts
the reply the agent gave next to the reply the target shows, so a person can
read them side by side; `--only` narrows the run to some conversations.

`CHECKS` are the properties of the target reply that can be read off the API
response without judgement (code shown or not, one question, no reference
list, the technique not named). They are a floor, not the score: the score is
the reading.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import httpx

ROOT: Final = Path(__file__).resolve().parents[2]
EXAMPLES: Final = Path(__file__).with_name("adaptive_examples.jsonl")
EXAMPLES_2: Final = ROOT / "docs" / "target_behavior2.md"

Turn = dict[str, Any]
Check = Callable[[Turn], bool]


@dataclass(frozen=True)
class Conversation:
    key: str
    targets: tuple[str, ...]
    #: (learner message, the reply the target shows or "")
    turns: tuple[tuple[str, str], ...]
    mode: str = "balanced"


#: The learner turns of docs/current-behavior.md, in order, as one conversation.
CURRENT: Final = Conversation(
    key="current",
    targets=(
        "A request for code with a bug to fix is given buggy code, not a new problem "
        "and not the correct solution.",
        "A challenge with 'don't tell me the algorithm' names no technique.",
        "No reference list and no internal note is printed under a reply.",
    ),
    turns=(
        ("ask me 10 dsa basic question to test how much knowledge i have", ""),
        (
            "check my answers and score out of out by corret answers:\n1:O(1)\n2:ello\n3:O(1)\n"
            "4:stack\n5:O(log n)\n6:O(n)\n7:O(n)\n8:6\n9:heap for minimun elements\n"
            "10: i don't know 10th answer",
            "",
        ),
        ("give 2 problems with bugs and to fix it myself,start with easy", ""),
        ("give a code with bug and i will fix it", ""),
        (
            "i asked you to give code with bugs and i will fix it to test myself."
            "give bug code of twosum",
            "",
        ),
        ("Give me 5 topics to learn before starting DSA", ""),
        (
            "Give me a coding challenge. I want to write the solution myself. "
            "Don't tell me the algorithm unless I ask for a hint.",
            "",
        ),
    ),
)


def conversations() -> list[Conversation]:
    out: list[Conversation] = []
    for line in EXAMPLES.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        example = json.loads(line)
        turns: list[tuple[str, str]] = []
        pending: str | None = None
        for turn in example["conversation"]:
            if turn["role"] == "user":
                pending = turn["content"]
            elif pending is not None:
                turns.append((pending, turn["content"]))
                pending = None
        out.append(
            Conversation(
                key="a" + example["example_id"].rsplit("_", 1)[-1],
                targets=tuple(example["agent_behavior_targets"]),
                turns=tuple(turns),
            )
        )
    for example in json.loads(EXAMPLES_2.read_text(encoding="utf-8")):
        out.append(
            Conversation(
                key="b" + example["example_id"].rsplit("_", 1)[-1],
                targets=(example["learning_update"]["agent_behavior_rule"],),
                turns=tuple(
                    (turn["user"], turn["ideal_agent_response"]) for turn in example["conversation"]
                ),
            )
        )
    out.append(CURRENT)
    return out


# --- what can be read off a reply without judgement --------------------------------

_FENCE: Final = re.compile(r"```")
_DEF: Final = re.compile(r"^\s*(def |class )\w+", re.MULTILINE)


def text(turn: Turn) -> str:
    return str(turn.get("response") or "")


def shows_solution_code(turn: Turn) -> bool:
    """A fenced block that defines a function or class."""
    parts = _FENCE.split(text(turn))
    return any(_DEF.search(block) for block in parts[1::2])


def questions(turn: Turn) -> int:
    prose = "".join(_FENCE.split(text(turn))[0::2])
    return prose.count("?")


def no_references(turn: Turn) -> bool:
    body = text(turn).lower()
    return (
        not turn.get("citations")
        and "\nreferences" not in body
        and "reference:" not in body
        and "adaptation note" not in body
        and "dsa corpus" not in body
    )


def names(*words: str) -> Check:
    return lambda turn: any(word in text(turn).lower() for word in words)


def never(*words: str) -> Check:
    return lambda turn: not any(word in text(turn).lower() for word in words)


def no_code(turn: Turn) -> bool:
    return not shows_solution_code(turn)


def at_most_two_questions(turn: Turn) -> bool:
    return questions(turn) <= 2


#: conversation key -> turn index -> named checks. Every turn also gets `no_references`.
CHECKS: Final[dict[str, dict[int, dict[str, Check]]]] = {
    "a001": {
        0: {"no code": no_code, "technique not named first": never("expand around", "two pointer")},
        1: {"no code": no_code},
        2: {"refines two pointers": names("center", "centre")},
        4: {"states the cost reason": names("center", "centre", "n²", "n^2", "n squared")},
    },
    "a002": {
        0: {"no code": no_code},
        1: {"no code": no_code, "corrects reset": names("abba", "reset", "start over", "throw")},
        2: {"explains while": names("while")},
        3: {"no code before the attempt": no_code},
        4: {"confirms correct": names("correct", "passes", "right", "works")},
    },
    "a003": {
        0: {"names the lookup": names("complement")},
        1: {"traces a small input": names("[2, 7]", "2, 7", "2 and 7", "seen")},
        2: {"gives the code": shows_solution_code},
    },
    "a004": {
        0: {"no full code": no_code},
        1: {"no full code": no_code},
        2: {"no full code": no_code},
        3: {"gives the code": shows_solution_code, "LeetCode class": names("class solution")},
    },
    "a005": {
        0: {"BFS for shortest": names("bfs", "breadth"), "no code": no_code},
        1: {"no code": no_code},
        2: {"visited at enqueue": names("enqueue", "when you add", "when it is added", "pushed")},
        3: {"no code": no_code},
    },
    "a006": {
        0: {"no code": no_code, "asks about the last move": names("last", "final")},
        1: {"states the recurrence": names("n-1", "n - 1")},
        3: {"no code": no_code},
    },
    "a007": {
        0: {
            "no code": no_code,
            "technique withheld": never("hash", " set", "sort", "union"),
        },
        1: {"sorting accepted": names("valid", "works", "correct", "fine"), "no code": no_code},
        2: {"no code": no_code},
        3: {"no code": no_code},
        4: {"no code": no_code},
    },
    "a008": {
        0: {
            "reads the type error": names("string", "str"),
            "asks for the caller": lambda t: questions(t) >= 1,
        },
        1: {"input returns a string": names("input")},
        2: {"no rewrite": no_code},
    },
    "a009": {
        0: {"names the contract": names("-1")},
        1: {"traces": names("left")},
        2: {"insert position": names("insert")},
    },
    "a010": {
        0: {
            "isolates retrieval": names("retriev"),
            "asks for evidence": lambda t: questions(t) >= 1,
        },
        2: {"dimension is not compatibility": names("same", "compatib", "space")},
    },
    "b001": {
        0: {"no code": no_code, "algorithm withheld": never("sliding window")},
        1: {"no code": no_code},
    },
    "b002": {0: {"no corrected code yet": no_code}, 1: {"no corrected code yet": no_code}},
    "b003": {
        0: {
            "validates brute force": names("correct", "works", "valid", "not bad"),
            "no code": no_code,
        },
        1: {"no code": no_code},
    },
    "b004": {0: {"defines distinct": names("unique", "different"), "no code": no_code}},
    "b005": {
        0: {"argues from the shorter line": names("shorter", "smaller", "min"), "no code": no_code}
    },
    "b006": {0: {"last move": names("last", "final"), "no code": no_code}},
    "b007": {0: {"no fix yet": no_code}, 1: {"no fix yet": no_code}},
    "b008": {0: {"no code": no_code}, 1: {"no code": no_code}},
    "b009": {0: {"gives signals": names("sliding window"), "not a problem": never("leetcode.com")}},
    "b010": {
        0: {"a plan, not a problem": never("leetcode.com")},
        2: {"not a problem dump": no_code},
    },
    "current": {
        2: {"buggy code given": shows_solution_code, "says it has a bug": names("bug")},
        3: {"buggy code given": shows_solution_code},
        4: {
            "buggy code given": shows_solution_code,
            "not called the solution": never("full solution"),
        },
        5: {"five topics": names("5.", "5)")},
        6: {
            "technique withheld": never("hash", "general signal", "complement"),
            "no code": no_code,
        },
    },
}


class Api:
    def __init__(self, base_url: str, timeout: float, pace: float) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=timeout)
        self._pace = pace
        self.headers: dict[str, str] = {}

    def new_account(self) -> None:
        account = {
            "username": "tgt_" + secrets.token_hex(5),
            "password": secrets.token_urlsafe(18) + "aA1!",
        }
        self._client.post("/auth/register", json=account).raise_for_status()
        token = self._client.post("/auth/login", json=account).json()["access_token"]
        self.headers = {"Authorization": f"Bearer {token}"}

    def run(self, conversation: Conversation) -> list[Turn]:
        turns: list[Turn] = []
        conversation_id: str | None = None
        for message, _ in conversation.turns:
            data = {"text": message, "teaching_mode": conversation.mode}
            if conversation_id:
                data["conversation_id"] = conversation_id
            started = time.perf_counter()
            response = self._client.post("/chat", data=data, headers=self.headers)
            response.raise_for_status()
            body: Turn = response.json()
            body["_seconds"] = round(time.perf_counter() - started, 2)
            conversation_id = body["conversation_id"]
            turns.append(body)
            if self._pace > 0:
                time.sleep(self._pace)
        return turns


@dataclass
class Tally:
    passed: int = 0
    total: int = 0
    failures: list[str] = field(default_factory=list[str])


def _summary(turn: Turn) -> str:
    intent: Any = turn.get("intent") or {}
    plan: Any = turn.get("plan") or {}
    parts = [f"route={turn.get('route')}", f"intent={intent.get('intent')}"]
    parts += [f"{key}={plan[key]}" for key in ("help_level", "strategy") if plan.get(key)]
    parts.append(f"calls={turn.get('llm_calls')}")
    parts.append(f"{turn.get('_seconds')}s")
    return " ".join(parts)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay the target conversations live.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--only", default="", help="comma-separated keys, e.g. a001,b003,current")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--pace", type=float, default=0.0)
    args = parser.parse_args(argv)

    wanted = {key for key in args.only.split(",") if key}
    api = Api(args.base_url, args.timeout, args.pace)
    tally = Tally()
    lines: list[str] = []
    for conversation in conversations():
        if wanted and conversation.key not in wanted:
            continue
        api.new_account()
        try:
            turns = api.run(conversation)
        except httpx.HTTPError as exc:
            lines.append(f"\n# {conversation.key}\n\nFAILED TO RUN: {exc!r}\n")
            tally.failures.append(f"{conversation.key}: did not run ({exc!r})")
            tally.total += 1
            continue
        lines.append(f"\n# {conversation.key}\n")
        lines += [f"- target: {target}" for target in conversation.targets]
        for index, (turn, (message, ideal)) in enumerate(
            zip(turns, conversation.turns, strict=True)
        ):
            checks: dict[str, Check] = {"no references": no_references}
            checks.update(CHECKS.get(conversation.key, {}).get(index, {}))
            results = {name: bool(check(turn)) for name, check in checks.items()}
            for name, ok in results.items():
                tally.total += 1
                tally.passed += ok
                if not ok:
                    tally.failures.append(f"{conversation.key} turn {index}: {name}")
            failed = [name for name, ok in results.items() if not ok]
            lines.append(f"\n## {conversation.key} turn {index}  [{_summary(turn)}]")
            lines.append(
                f"\nCHECKS: {'all pass' if not failed else 'FAILED: ' + '; '.join(failed)}"
            )
            lines.append(f"\n**USER:** {message}")
            lines.append(f"\n**AGENT:**\n{text(turn)}")
            if ideal:
                lines.append(f"\n**TARGET:**\n{ideal}")
        args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"{conversation.key}: done ({tally.passed}/{tally.total} checks so far)", flush=True)

    rate = 100.0 * tally.passed / tally.total if tally.total else 0.0
    report = [f"\n# CHECKS {tally.passed}/{tally.total} ({rate:.1f}%)"]
    report += [f"- {failure}" for failure in tally.failures]
    with args.out.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(report) + "\n")
    print("\n".join(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
