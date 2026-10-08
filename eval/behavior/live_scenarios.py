"""Run the live behaviour scenarios N times and report pass rates.

    .\\venv\\Scripts\\python.exe -m eval.behavior.live_scenarios --runs 5 \\
        --base-url http://127.0.0.1:8000

Each run uses fresh accounts and drives `POST /chat` exactly like a client:
scenarios 1-5 and 8 share one account (8 is the cross-session check on the
profile 1-5 built), 6 and 7 each get their own. Every check reads only what
the API returned. The scenarios and what they must show are the ones scored
by hand in `docs/LIVE_BEHAVIOR.md`; this file makes that scoring repeatable,
so a model's run-to-run variance shows up as a pass RATE rather than as one
lucky or unlucky transcript.

Scenarios 1-5 send the learner turns of `adaptive_examples.jsonl`.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import httpx

EXAMPLES: Final = Path(__file__).with_name("adaptive_examples.jsonl")

Turn = dict[str, Any]
Check = tuple[str, Callable[[list[Turn]], bool]]

P678: Final = (
    "678. Valid Parenthesis String\n\n"
    "Given a string s containing only three types of characters: '(', ')' and '*', return true "
    "if s is valid.\n\nThe following rules define a valid string:\n"
    "- Any left parenthesis '(' must have a corresponding right parenthesis ')'.\n"
    "- Any right parenthesis ')' must have a corresponding left parenthesis '('.\n"
    "- Left parenthesis '(' must go before the corresponding right parenthesis ')'.\n"
    "- '*' could be treated as a single right parenthesis ')' or a single left parenthesis '(' "
    'or an empty string "".\n\n'
    'Example 1:\nInput: s = "()"\nOutput: true\n\n'
    'Example 2:\nInput: s = "(*)"\nOutput: true\n\n'
    'Example 3:\nInput: s = "(*))"\nOutput: true\n\n'
    "Constraints:\n1 <= s.length <= 100\ns[i] is '(', ')' or '*'.\n\n"
    "how to solve this prob"
)
TWO_SUM: Final = (
    "Two Sum\n\nGiven an array of integers nums and an integer target, return indices of the "
    "two numbers such that they add up to target.\n\n"
    "Example 1:\nInput: nums = [2,7,11,15], target = 9\nOutput: [0,1]\n\n"
    "Example 2:\nInput: nums = [3,2,4], target = 6\nOutput: [1,2]\n\n"
)
BUGGY: Final = (
    "here is my attempt, it gives the wrong answer\n\n```python\n"
    "def two_sum(nums, target):\n    seen = {}\n    for i, num in enumerate(nums):\n"
    "        if num in seen:\n            return [seen[num], i]\n        seen[num] = i\n"
    "    return []\n```"
)
ISLANDS: Final = (
    "Number of Islands\n\nGiven an m x n 2D binary grid which represents a map of '1's (land) "
    "and '0's (water), return the number of islands.\n\n"
    'Example 1:\nInput: grid = [["1","1","0"],["0","1","0"],["0","0","1"]]\nOutput: 2\n\n'
    "how should I approach this?"
)


def _examples() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in EXAMPLES.read_text(encoding="utf-8").splitlines():
        if line.strip():
            example = json.loads(line)
            out[example["conversation_id"]] = [
                turn["content"] for turn in example["conversation"] if turn["role"] == "user"
            ]
    return out


# --- accessors over one turn's response body ------------------------------------


def text(turn: Turn) -> str:
    return str(turn.get("response") or "")


def code_shown(turn: Turn) -> bool:
    return bool((turn.get("generated") or {}).get("reveals_code"))


def grade(turn: Turn) -> str | None:
    return ((turn.get("tutoring") or {}).get("grade") or {}).get("grade")


def verdict(turn: Turn) -> dict[str, Any]:
    return turn.get("verification") or {}


def learner_verdict(turn: Turn) -> str | None:
    execution = (turn.get("tutoring") or {}).get("execution") or {}
    return (execution.get("learner") or {}).get("status")


def has(turn: Turn, *needles: str) -> bool:
    lowered = text(turn).lower()
    return any(needle.lower() in lowered for needle in needles)


def adaptations(turn: Turn) -> list[str]:
    return list(turn.get("adaptations") or [])


def evidence(turn: Turn) -> str | None:
    events = turn.get("events") or []
    return events[-1].get("evidence_source") if events else None


def stack_code(turn: Turn) -> bool:
    """The revealed code keeps a stack: it pushes and pops (or names one)."""
    lowered = text(turn).lower()
    return "stack" in lowered and ".append(" in lowered and ".pop(" in lowered


def no_guessing(turn: Turn) -> bool:
    return not has(turn, "it looks like you might want", "could you confirm")


def no_refusal(turn: Turn) -> bool:
    return not has(
        turn,
        "did not return usable code",
        "i won't show code",
        "the references do not cover",
        "the provided references",
        "i can't elaborate",
    )


def one_question(turn: Turn) -> bool:
    """A tutoring step asks at most two things (one question, maybe a lead-in)."""
    return text(turn).count("?") <= 2


TRAP: Final = (
    "```python\n"
    "def trap(height):\n    if not height:\n        return 0\n"
    "    left, right = 0, len(height) - 1\n    left_max, right_max = 0, 0\n    water = 0\n"
    "    while left < right:\n        if height[left] < height[right]:\n"
    "            if height[left] >= left_max:\n                left_max = height[left]\n"
    "            else:\n                water += left_max - height[left]\n            left += 1\n"
    "        else:\n            if height[right] >= right_max:\n"
    "                right_max = height[right]\n            else:\n"
    "                water += right_max - height[right]\n            right -= 1\n"
    "    return water\n```"
)
MAX_DEPTH: Final = (
    "I'm solving Maximum Depth of Binary Tree. My code gives the wrong answer. Explain it, "
    "but don't give me the final code yet.\n\n```python\n"
    "def maxDepth(root):\n    if root is None:\n        return 0\n"
    "    left = maxDepth(root.left)\n    right = maxDepth(root.right)\n"
    "    return max(left, right)\n```"
)
TRACEBACK: Final = (
    "Find the core error and explain it. Don't rewrite everything.\n\n"
    "Traceback (most recent call last):\n"
    '  File "app.py", line 18, in <module>\n    result = process_data(user)\n'
    '  File "app.py", line 11, in process_data\n    print(user["name"])\n'
    "TypeError: string indices must be integers"
)
MAX_PATH: Final = (
    "Binary Tree Maximum Path Sum\n\nGiven the root of a binary tree, return the maximum "
    "path sum of any non-empty path.\n\n"
    "Example 1:\nInput: root = [1,2,3]\nOutput: 6\n\n"
    "Example 2:\nInput: root = [-10,9,20,null,null,15,7]\nOutput: 42\n\n"
)


def _never_a_study_plan(turns: list[Turn]) -> bool:
    return all(t["route"] != "explain" and not has(t, "study plan", "week 1") for t in turns)


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    mode: str
    turns: Sequence[str]
    checks: Sequence[Check]
    shared_account: bool = True
    #: Checks that must pass on EVERY run (the merge bar's 5/5 items).
    strict: Sequence[str] = ()


def scenarios() -> list[Scenario]:
    ex = _examples()
    return [
        Scenario(
            "1",
            "Beginner, Two Sum",
            "guidance",
            ex["adaptive_001"],
            [
                ("t1 no full code", lambda t: not code_shown(t[0])),
                ("t1 ends on a question", lambda t: "?" in text(t[0])),
                ("t2 graded correct", lambda t: grade(t[1]) == "correct"),
                ("t3 graded correct", lambda t: grade(t[2]) == "correct"),
                ("t4 'I don't know' raises help", lambda t: grade(t[3]) == "dont_know"),
                (
                    "t4 code shown and verified",
                    lambda t: code_shown(t[3]) and verdict(t[3]).get("status") == "pass",
                ),
            ],
            strict=("t1 no full code",),
        ),
        Scenario(
            "2",
            "KeyError debug",
            "balanced",
            ex["adaptive_002"],
            [
                ("t1 debug route", lambda t: t[0]["route"] == "debug"),
                ("t1 names the line", lambda t: has(t[0], "seen[num]")),
                (
                    "t1 verified on >= 4 cases",
                    lambda t: (
                        verdict(t[0]).get("status") == "pass"
                        and (verdict(t[0]).get("cases_total") or 0) >= 4
                    ),
                ),
                ("t2 answer credited", lambda t: grade(t[1]) == "correct"),
            ],
        ),
        Scenario(
            "3",
            "Challenge mode, Critical Connections",
            "challenge",
            ex["adaptive_003"],
            [
                ("solution withheld on every turn", lambda t: not any(code_shown(x) for x in t)),
                ("t1 gives a problem", lambda t: t[0]["route"] == "practice"),
                ("t2-t4 answers credited", lambda t: all(grade(x) == "correct" for x in t[1:4])),
                (
                    "t5 submitted code is run",
                    lambda t: (
                        t[4]["route"] == "debug" and verdict(t[4]).get("status") in ("pass", "fail")
                    ),
                ),
            ],
            strict=("solution withheld on every turn",),
        ),
        Scenario(
            "4",
            "Max Path Sum misconception",
            "balanced",
            ex["adaptive_004"],
            [
                ("t1 debug route", lambda t: t[0]["route"] == "debug"),
                ("t1 returnable vs global path", lambda t: has(t[0], "one-sided", "one sided")),
                (
                    "t1 real verdict on their code",
                    lambda t: learner_verdict(t[0]) in ("pass", "fail"),
                ),
                ("t2 answer credited", lambda t: grade(t[1]) == "correct"),
            ],
        ),
        Scenario(
            "5",
            "BFS vs DFS",
            "balanced",
            ex["adaptive_005"],
            [
                ("t2 wrong answer corrected", lambda t: grade(t[1]) == "incorrect"),
                ("t3 graded correct", lambda t: grade(t[2]) == "correct"),
                ("t4 gives a problem", lambda t: t[3]["route"] == "practice"),
                (
                    "t6 submitted code is run",
                    lambda t: verdict(t[5]).get("status") in ("pass", "fail"),
                ),
                (
                    "t7 harder, citing the session",
                    lambda t: (
                        t[6]["route"] == "practice"
                        and has(t[6], "based on this session")
                        and has(t[6], "**hard**")
                    ),
                ),
                ("t8 'I don't know' raises help", lambda t: grade(t[7]) == "dont_know"),
            ],
        ),
        Scenario(
            "6",
            "Session M, LeetCode 678 as text",
            "balanced",
            [
                P678,
                "tell me name of that problem",
                "the problem i have shared, its name",
                "so you can't read previous conversations?",
                "give me step by step to solve the valid parenthesis string problem",
            ],
            [
                ("t1 a step, no code", lambda t: t[0]["route"] == "dsa" and not code_shown(t[0])),
                (
                    "t2-t4 name the problem",
                    lambda t: all(has(x, "Valid Parenthesis String") for x in t[1:4]),
                ),
                ("t4 confirms it sees the conversation", lambda t: has(t[3], "i can see")),
                ("no study plan on follow-ups", lambda t: _never_a_study_plan(t[1:])),
                (
                    "t5 problem-specific step, no code",
                    lambda t: (
                        t[4]["route"] == "dsa"
                        and not code_shown(t[4])
                        and has(t[4], "low", "min", "smallest", "range", "unmatched", "open")
                    ),
                ),
                ("'*' intact", lambda t: "`*`" in text(t[0]) and "''" not in text(t[0])),
            ],
            shared_account=False,
            strict=("no study plan on follow-ups",),
        ),
        Scenario(
            "7",
            "Vague phrasings",
            "balanced",
            [
                P678,
                TWO_SUM + "help me get started",
                "what was that problem called again?",
                "go back to the earlier one",
                "show me the solution",
                TWO_SUM + "back to this one",
                BUGGY,
                "where's the mistake?",
                "write it out",
            ],
            [
                (
                    "'what was that problem called again?'",
                    lambda t: t[2]["route"] == "meta" and has(t[2], "Two Sum"),
                ),
                (
                    "'go back to the earlier one'",
                    lambda t: (
                        t[3]["route"] == "dsa" and has(t[3], "(") and not has(t[3], "Two Sum")
                    ),
                ),
                (
                    "'show me the solution'",
                    lambda t: code_shown(t[4]) and verdict(t[4]).get("status") == "pass",
                ),
                (
                    "'where's the mistake?'",
                    lambda t: t[7]["route"] == "debug" and has(t[7], "num in seen"),
                ),
                ("'write it out'", lambda t: code_shown(t[8])),
            ],
            shared_account=False,
        ),
        # --- The owner's real conversations (docs/BEHAVIOR_GAP.md, section 2) ---
        Scenario(
            "9",
            "Owner: 678, a roadmap, its follow-ups, then the code",
            "balanced",
            [
                P678,
                "give roadmap to master stack,queue",
                "first where should i start",
                "im asking about the roadmap",
                "give code the valid paranthesis problem",
                "give code using stack",
            ],
            [
                ("t1 a step, no code", lambda t: t[0]["route"] == "dsa" and not code_shown(t[0])),
                ("t2 a plan for stacks and queues", lambda t: has(t[1], "stack", "queue")),
                (
                    "t3 follow-up is about the roadmap",
                    lambda t: t[2]["route"] == "explain" and not has(t[2], "unmatched", "`*`"),
                ),
                (
                    "t3-t4 answered, not a new plan",
                    lambda t: (
                        all(len(text(x)) < 2500 for x in t[2:4])
                        and not any(has(x, "segment tree", "bit manipulation") for x in t[2:4])
                    ),
                ),
                ("t5 code given, no refusal", lambda t: code_shown(t[4]) and no_refusal(t[4])),
                ("t6 no 'could you confirm'", lambda t: no_guessing(t[5])),
                ("t6 code given", lambda t: code_shown(t[5]) and no_refusal(t[5])),
                ("t6 the code uses a stack", lambda t: stack_code(t[5])),
                ("no study plan unless asked", lambda t: _never_a_study_plan([t[0], *t[4:]])),
            ],
            shared_account=False,
            strict=("t3 follow-up is about the roadmap", "no study plan unless asked"),
        ),
        Scenario(
            "10",
            "Owner: shared code, asks for the code",
            "balanced",
            [
                "give correct code of this.\nheight = [0,1,0,2,1,0,1,3,2,1,2,1]\n\n" + TRAP,
                "give python code",
                "is there another method for this program?",
            ],
            [
                ("t1 the code is read, not hinted at", lambda t: t[0]["route"] == "debug"),
                ("t1 code given", lambda t: code_shown(t[0]) and no_refusal(t[0])),
                ("t2 code given", lambda t: code_shown(t[1]) and no_guessing(t[1])),
                (
                    "t3 answered about their program",
                    lambda t: t[2]["route"] != "clarify" and no_guessing(t[2]),
                ),
                (
                    "t3 names another method",
                    lambda t: has(
                        t[2], "another", "alternative", "prefix", "stack", "dynamic", "instead"
                    ),
                ),
            ],
            shared_account=False,
            strict=("t3 answered about their program",),
        ),
        Scenario(
            "11",
            "Owner: concept explanations and a follow-up",
            "balanced",
            [
                "explain the concept of recursion with examples.",
                "explain sliding window with examples",
                "can you give one more example",
            ],
            [
                ("t1 explained, not refused", lambda t: no_refusal(t[0]) and len(text(t[0])) > 600),
                ("t1 has a run example", lambda t: has(t[0], "run in the sandbox", "```")),
                ("t2 explained", lambda t: has(t[1], "window") and no_refusal(t[1])),
                (
                    "t3 one more example, not the lecture again",
                    lambda t: t[2]["route"] == "explain" and len(text(t[2])) < len(text(t[1])),
                ),
            ],
            shared_account=False,
        ),
        Scenario(
            "12",
            "Owner: greetings and a question about the chat get no study plan",
            "balanced",
            ["hi agent", P678, "answer this only. what question i gave to u", "hii agent"],
            [
                ("no study plan unless asked", lambda t: _never_a_study_plan(t)),
                ("t1 greeted", lambda t: t[0]["route"] == "clarify"),
                ("t3 names the problem", lambda t: has(t[2], "Valid Parenthesis String")),
            ],
            shared_account=False,
            strict=("no study plan unless asked",),
        ),
        # --- Invariants 7, 8 and 12: the reply changes with the learner ---------
        Scenario(
            "13",
            "Stuck three times: the representation changes",
            "guidance",
            [TWO_SUM + "I'm new to DSA. I don't understand how to start.", *(["I don't know"] * 3)],
            [
                ("t1 no full code", lambda t: not code_shown(t[0])),
                ("t2-t3 never the same reply twice", lambda t: len({text(x) for x in t}) == len(t)),
                (
                    "a changed representation is announced",
                    lambda t: any(
                        "worked example" in " ".join(adaptations(x))
                        or "drew it out" in " ".join(adaptations(x))
                        for x in t[1:]
                    ),
                ),
                ("stuck is recorded as evidence", lambda t: any(evidence(x) for x in t[1:])),
            ],
            shared_account=False,
            strict=("t1 no full code",),
        ),
        Scenario(
            "14",
            "Asking for the code moves the profile, and code is never refused",
            "balanced",
            [P678.replace("how to solve this prob", "give me the full code")],
            [
                ("code given on the first ask", lambda t: code_shown(t[0]) and no_refusal(t[0])),
                ("recorded as help needed", lambda t: evidence(t[0]) == "help_needed"),
                (
                    "no badge without a reason",
                    lambda t: bool(t[0].get("adapted")) == bool(adaptations(t[0])),
                ),
            ],
            shared_account=False,
        ),
        # --- target_behavior.md section 29, conversations 6 to 10 ---------------
        Scenario(
            "15",
            "Ref 6: buggy code, explain but no final code yet",
            "balanced",
            [MAX_DEPTH, "Add 1.", "return 1 + max(left, right)"],
            [
                ("t1 their code is read", lambda t: t[0]["route"] == "debug"),
                ("t1 names the missing node", lambda t: has(t[0], "current node", "1 +", "+ 1")),
                ("t1 no final code, as asked", lambda t: not code_shown(t[0])),
                (
                    "t1 the fix line is not stated either",
                    lambda t: not has(t[0], "1 + max(", "max(left, right) + 1"),
                ),
                ("t1 asks them to make the fix", lambda t: "?" in text(t[0])),
                (
                    "t2 'Add 1.' is about their code",
                    lambda t: t[1]["route"] == "debug" and not code_shown(t[1]),
                ),
                (
                    "t2 the right answer is credited",
                    lambda t: has(t[1], "correct", "right", "exactly", "yes"),
                ),
                ("t3 their fix line is read as their code", lambda t: t[2]["route"] == "debug"),
            ],
            shared_account=False,
            strict=("t1 no final code, as asked",),
        ),
        Scenario(
            "16",
            "Ref 7: independent challenge, algorithm not revealed",
            "challenge",
            [
                "Give me a coding challenge. I want to write it myself. Don't tell me the "
                "algorithm unless I ask for a hint.",
                "I'll sort it and count.",
                "Sorting is O(n log n). Maybe there is an O(n) approach.",
            ],
            [
                ("t1 gives a problem", lambda t: t[0]["route"] == "practice"),
                ("solution withheld on every turn", lambda t: not any(code_shown(x) for x in t)),
                ("t2 not rejected", lambda t: not has(t[1], "not quite", "that's not it")),
            ],
            shared_account=False,
            strict=("solution withheld on every turn",),
        ),
        Scenario(
            "17",
            "Ref 9: a traceback, core error only",
            "balanced",
            [
                TRACEBACK,
                'I\'m doing:\n\n```python\nuser = input("Enter your name: ")\n'
                "process_data(user)\n```",
            ],
            [
                ("t1 debug route", lambda t: t[0]["route"] == "debug"),
                ("t1 names the type mismatch", lambda t: has(t[0], "string", "str")),
                ("t1 no rewrite", lambda t: not code_shown(t[0])),
                ("t2 ties it to input()", lambda t: has(t[1], "input")),
            ],
            shared_account=False,
        ),
        Scenario(
            "18",
            "Ref 10: asks for the code after understanding",
            "balanced",
            [
                MAX_PATH + "I tried it and now I understand that the parent can only extend "
                "one branch. Give me the code and test it.",
            ],
            [
                ("code given on the first ask", lambda t: code_shown(t[0]) and no_refusal(t[0])),
                (
                    "a real sandbox verdict",
                    lambda t: verdict(t[0]).get("status") in ("pass", "fail"),
                ),
                ("no forced hint first", lambda t: not has(t[0], "take this one step first")),
            ],
            shared_account=False,
        ),
        Scenario(
            "8",
            "Cross-session, same learner",
            "balanced",
            [ISLANDS, TWO_SUM + "how should I approach this?"],
            [
                (
                    "profile skill is not the prior",
                    lambda t: (t[0].get("plan") or {}).get("skill_level") != 0.5,
                ),
                (
                    "cites an earlier mistake",
                    lambda t: any(has(x, "you ran into this earlier") for x in t),
                ),
                ("no code for an approach question", lambda t: not any(code_shown(x) for x in t)),
            ],
        ),
    ]


# --- driving the API ----------------------------------------------------------------


class Api:
    def __init__(self, base_url: str, timeout: float, pace: float = 0.0) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=timeout)
        self._pace = pace
        self.headers: dict[str, str] = {}

    def new_account(self) -> None:
        account = {
            "username": "rel_" + secrets.token_hex(5),
            "password": secrets.token_urlsafe(18) + "aA1!",
        }
        self._client.post("/auth/register", json=account).raise_for_status()
        token = self._client.post("/auth/login", json=account).json()["access_token"]
        self.headers = {"Authorization": f"Bearer {token}"}

    def run(self, scenario: Scenario) -> list[Turn]:
        turns: list[Turn] = []
        conversation_id: str | None = None
        for message in scenario.turns:
            data = {"text": message, "teaching_mode": scenario.mode}
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
    runs: int = 0
    passed_runs: int = 0
    checks: dict[str, int] = field(default_factory=dict[str, int])
    failures: list[str] = field(default_factory=list[str])


def _score(scenario: Scenario, turns: list[Turn], tally: Tally, run: int) -> None:
    tally.runs += 1
    all_ok = True
    for name, check in scenario.checks:
        try:
            ok = bool(check(turns))
        except (IndexError, KeyError, TypeError):
            ok = False
        tally.checks[name] = tally.checks.get(name, 0) + int(ok)
        if not ok:
            all_ok = False
            detail = [
                f"{t['route']}/{(t.get('intent') or {}).get('intent')}"
                f"/code={code_shown(t)}/grade={grade(t)}"
                for t in turns
            ]
            tally.failures.append(f"run {run}: {name}: {detail}")
    tally.passed_runs += int(all_ok)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--only", nargs="*", default=None, help="scenario keys to run")
    parser.add_argument(
        "--pace", type=float, default=0.0, help="seconds between turns (LLM rate limits)"
    )
    parser.add_argument("--out", default=None, help="write the JSON record here")
    args = parser.parse_args(argv)

    chosen = [s for s in scenarios() if args.only is None or s.key in args.only]
    tallies = {s.key: Tally() for s in chosen}
    seconds: list[float] = []
    calls: list[int] = []
    record: list[dict[str, Any]] = []
    api = Api(args.base_url, args.timeout, args.pace)
    for run in range(1, args.runs + 1):
        shared_headers: dict[str, str] | None = None
        for scenario in chosen:
            # Scenarios 1-5 and 8 are ONE learner; 6 and 7 are strangers. The
            # shared learner's login is kept aside while a stranger runs.
            if scenario.shared_account:
                if shared_headers is None:
                    api.new_account()
                    shared_headers = api.headers
                api.headers = shared_headers
            else:
                api.new_account()
            try:
                turns = api.run(scenario)
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                tallies[scenario.key].runs += 1
                tallies[scenario.key].failures.append(f"run {run}: {type(exc).__name__}")
                print(f"run {run} scenario {scenario.key}: {type(exc).__name__}", flush=True)
                continue
            _score(scenario, turns, tallies[scenario.key], run)
            seconds += [t["_seconds"] for t in turns]
            calls += [int(t.get("llm_calls") or 0) for t in turns]
            record.append(
                {
                    "run": run,
                    "scenario": scenario.key,
                    "turns": [
                        {
                            "route": t["route"],
                            "intent": t.get("intent"),
                            "seconds": t["_seconds"],
                            "llm_calls": t.get("llm_calls"),
                            "reveals_code": code_shown(t),
                            "grade": grade(t),
                            "adaptations": adaptations(t),
                            "evidence": evidence(t),
                            "verification": verdict(t).get("status"),
                            "response": text(t),
                        }
                        for t in turns
                    ],
                }
            )
            done = tallies[scenario.key]
            print(f"run {run} scenario {scenario.key}: {done.passed_runs}/{done.runs}", flush=True)

    print("\n| # | Scenario | Runs passed | Check | Passed |")
    print("|---|---|---|---|---|")
    below_bar = False
    for scenario in chosen:
        tally = tallies[scenario.key]
        for index, (name, _) in enumerate(scenario.checks):
            passed = tally.checks.get(name, 0)
            bar = tally.runs if name in scenario.strict else max(tally.runs - 1, 0)
            flag = "" if passed >= bar else " BELOW BAR"
            below_bar = below_bar or passed < bar
            lead = (
                f"| {scenario.key} | {scenario.title} | {tally.passed_runs}/{tally.runs} |"
                if index == 0
                else "| | | |"
            )
            strict = " (must be all)" if name in scenario.strict else ""
            print(f"{lead} {name}{strict} | {passed}/{tally.runs}{flag} |")
    for scenario in chosen:
        for failure in tallies[scenario.key].failures:
            print(f"FAIL {scenario.key}: {failure[:400]}")
    if seconds:
        ordered = sorted(seconds)
        print(
            f"\nturns={len(seconds)} median={ordered[len(ordered) // 2]:.1f}s "
            f"p90={ordered[int(len(ordered) * 0.9)]:.1f}s max={ordered[-1]:.1f}s "
            f"mean_llm_calls={sum(calls) / len(calls):.2f}"
        )
    if args.out:
        Path(args.out).write_text(json.dumps(record, indent=1), encoding="utf-8")
    return 1 if below_bar else 0


if __name__ == "__main__":
    sys.exit(main())
