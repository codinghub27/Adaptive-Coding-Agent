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
import re
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


OUTPUT_ASK: Final = "what is the output of this code.\na=b=[]\na.append(1)\nprint(b)"
QUIZ_ASK: Final = "ask me 10 dsa basic question to test how much knowledge i have"
QUIZ_SKIPS: Final = "\n".join(f"{n}. skip, I don't know" for n in range(1, 11))
#: The practice agent's old reply to a request it had no topic for.
PICK: Final = "which pattern you want to practise"


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


def numbered(turn: Turn) -> int:
    """How many numbered lines ("1. ...") the reply has."""
    return sum(1 for line in text(turn).splitlines() if _NUMBERED_RE.match(line))


_NUMBERED_RE: Final = re.compile(r"\d{1,2}\. \S")


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


def states_the_fix(turn: Turn, *spellings: str) -> bool:
    """One of `spellings` is in the reply, however it is spaced."""
    flat = "".join(text(turn).lower().split())
    return any("".join(spelling.lower().split()) in flat for spelling in spellings)


def has_code_block(turn: Turn) -> bool:
    return "```python" in text(turn) or code_shown(turn)


def _bank_sentences() -> frozenset[str]:
    """Every fixed reply sentence in the curated bank (item F)."""
    from app.tutoring.bank import BANK_PATH

    bank = json.loads(BANK_PATH.read_text(encoding="utf-8"))
    out: set[str] = set()
    for spec in bank["questions"].values():
        for field_name in ("on_correct", "on_incorrect", "scaffold"):
            sentence = str(spec.get(field_name) or "").strip()
            if len(sentence) >= 30:
                out.add(sentence)
    return frozenset(out)


def written_not_looked_up(turn: Turn) -> bool:
    """A graded reply is not one of the bank's fixed sentences."""
    return not any(sentence in text(turn) for sentence in _bank_sentences())


def no_dump(turn: Turn) -> bool:
    """No retrieved material nobody asked for (target behaviour section 25)."""
    return not has(
        turn,
        "how to recognize this pattern",
        "the spreadsheet's cue",
        "common mistakes to avoid",
        "a brute-force approach",
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
LPS: Final = (
    "help me to solve this problem.\n\nLongest Palindromic Substring\n\nGiven a string s, "
    "return the longest palindromic substring in s.\n\n"
    'Example 1:\nInput: s = "cbbd"\nOutput: "bb"\n\n'
    'Example 2:\nInput: s = "a"\nOutput: "a"\n\n'
    "Constraints:\n1 <= s.length <= 1000\ns consist of only digits and English letters."
)
LPS_PASTED: Final = (
    "full code:\n```python\nclass Solution(object):\n    def longestPalindrome(self, s):\n"
    "        def expand(l: int, r: int) -> str:\n"
    "            while l >= 0 and r < len(s) and s[l] == s[r]:\n"
    "                l -= 1\n                r += 1\n            return s[l + 1:r]\n\n"
    '        best = ""\n        for i in range(len(s) - 1):\n'
    "            cand = expand(i, i + 1)\n            if len(cand) > len(best):\n"
    "                best = cand\n        return best\n```\n"
    "error: SyntaxError: invalid syntax\n    def expand(l: int, r: int) -> str:\n"
    "Line 3  (Solution.py)"
)


def no_type_hints(turn: Turn) -> bool:
    """No annotated parameter or return in any code the reply shows."""
    import re

    blocks = re.findall(r"```python\n(.*?)```", text(turn), re.DOTALL)
    return not any(re.search(r"def \w+\([^)]*:\s*\w|\)\s*->", block) for block in blocks)


FRAGMENT: Final = (
    "fix this code.\nheight = [0,1,0,2,1,0,1,3,2,1,2,1]\nif not height:\n"
    "            return 0\n\n        left, right = 0, len(height) - 1\n"
    "        left_max, right_max = 0, 0\n        trapped_water = 0\n\n"
    "        while left < right:\n            if height[left] < height[right]:\n"
    "                if height[left] >= left_max:\n                    left_max = height[left]\n"
    "                else:\n                    trapped_water += left_max - height[left]\n"
    "                left -= 1\n            else:\n"
    "                if height[right] >= right_max:\n"
    "                    right_max = height[right]\n                else:\n"
    "                    trapped_water += right_max - height[right]\n"
    "                right += 1\n\n        return trapped_water"
)
#: `docs/current-behavior.md`: the learner's own (correct) method body, pasted.
FRAGMENT_OK: Final = (
    "give correct code of this.\nheight = [0,1,0,2,1,0,1,3,2,1,2,1]\n\n```python\n"
    + FRAGMENT.split("\n", 2)[2]
    .replace("left -= 1", "left += 1")
    .replace("right += 1", "right -= 1")
    + "\n```"
)
NEXT_PERMUTATION: Final = (
    "31. Next Permutation\n\nThe next permutation of an array of integers is the next "
    "lexicographically greater permutation of its integers. If such an arrangement is not "
    "possible, the array must be rearranged as the lowest possible order (sorted in "
    "ascending order).\n\nGiven an array of integers nums, find the next permutation of "
    "nums. The replacement must be in place and use only constant extra memory.\n\n"
    "Example 1:\nInput: nums = [1,2,3]\nOutput: [1,3,2]\n\n"
    "Example 2:\nInput: nums = [3,2,1]\nOutput: [1,2,3]\n\n"
    "Example 3:\nInput: nums = [1,1,5]\nOutput: [1,5,1]\n\n"
    "Constraints:\n1 <= nums.length <= 100\n0 <= nums[i] <= 100\n\n"
    "Explain how to solve this problem and explain with code line by line of how it works."
)


def walks_the_code(turn: Turn) -> bool:
    """A numbered walk that quotes code: at least four `N. \`...\`` items."""
    import re

    return len(re.findall(r"(?m)^\s*\d+\.\s+`[^`\n]+`", text(turn))) >= 4


def not_the_fallback(turn: Turn) -> bool:
    return not has(turn, "wasn't able to put together", "can't tell what that refers to")


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
                (
                    "no badge for what the reply did not do",
                    lambda t: not any("worked example" in n for n in adaptations(t[3])),
                ),
                # ADDED 2026-10-11 (item F): the reply to a graded answer is the
                # tutor's own, not the bank's fixed sentence.
                (
                    "t2, t3 the reply is written, not looked up",
                    lambda t: written_not_looked_up(t[1]) and written_not_looked_up(t[2]),
                ),
                ("t2, t3 still one question", lambda t: one_question(t[1]) and one_question(t[2])),
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
                    "t3-t4 answered, not the plan again",
                    lambda t: (
                        all(len(text(x)) < 1300 for x in t[2:4])
                        and not any(has(x, "week 2", "| day", "segment tree") for x in t[2:4])
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
                # TIGHTENED 2026-10-11 (item D): any spacing, and the reply to
                # "Add 1." may not state the line either (reference 6 says
                # "Try changing only the return statement").
                (
                    "t1 the fix line is not stated either",
                    lambda t: not states_the_fix(t[0], "1 + max(", "max(left, right) + 1"),
                ),
                (
                    "t2 the fix line is still theirs to write",
                    lambda t: not states_the_fix(t[1], "1 + max(", "max(left, right) + 1"),
                ),
                (
                    "t1 the diagnosis is still said",
                    lambda t: has(t[0], "current node", "node itself", "root"),
                ),
                ("t1 asks them to make the fix", lambda t: "?" in text(t[0])),
                (
                    "t2 'Add 1.' is about their code",
                    lambda t: t[1]["route"] == "debug" and not code_shown(t[1]),
                ),
                (
                    "t2 the right answer is credited",
                    lambda t: (
                        text(t[1])
                        .lstrip("*_ ")
                        .lower()
                        .startswith(("yes", "correct", "exactly", "that's right", "right"))
                    ),
                ),
                (
                    "t2 the diagnosis is not repeated",
                    lambda t: not has(t[1], "it fails on this case"),
                ),
                ("t3 their fix line is read as their code", lambda t: t[2]["route"] == "debug"),
                ("t3 their fix is confirmed", lambda t: has(t[2], "that is the fix", "yes")),
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
                (
                    "the sandbox's missing keyboard is not a bug",
                    lambda t: not any(has(x, "EOFError", "no code was executed") for x in t),
                ),
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
            "19",
            "Owner: a pasted method body with the pointers moving the wrong way",
            "balanced",
            [FRAGMENT],
            [
                ("the code is read", lambda t: t[0]["route"] == "debug"),
                (
                    "the paste is not blamed",
                    lambda t: not has(t[0], "indentation", "unindent", "syntax error"),
                ),
                (
                    "the real bug is named",
                    lambda t: has(t[0], "left -= 1", "right += 1", "left += 1", "right -= 1"),
                ),
                ("a real verdict on their code", lambda t: learner_verdict(t[0]) == "fail"),
            ],
            shared_account=False,
        ),
        Scenario(
            "20",
            "Ref 8: the same tree mix-up again, in a new conversation",
            "balanced",
            [
                "Diameter of Binary Tree\n\nGiven the root of a binary tree, return the length "
                "of the diameter of the tree: the longest path between any two nodes, counted "
                "in edges.\n\nExample 1:\nInput: root = [1,2,3,4,5]\nOutput: 3\n\n"
                "I want to solve it myself. What should my recursive function return?",
                "The node should return the longest path using both children.",
            ],
            [
                ("t1 no code", lambda t: not code_shown(t[0])),
                ("t2 no code", lambda t: not code_shown(t[1])),
                (
                    "t2 linked to the earlier mistake",
                    lambda t: has(t[1], "as before", "earlier", "previous", "again", "last time"),
                ),
                ("t2 says so on the badge", lambda t: bool(adaptations(t[1]))),
            ],
        ),
        Scenario(
            "21",
            "Owner: Longest Palindromic Substring, from the pattern to a LeetCode submission",
            "balanced",
            [
                "explain how to find the pattern for the problem to solve. every time im "
                "getting stuck at new problems.",
                LPS,
                "could not understand",
                LPS_PASTED,
                "give correct code in given leetcode way",
                "give full python version don't use any keywords in code like nonlocal",
            ],
            [
                (
                    "t1 a decision process, not a catalogue",
                    lambda t: (
                        has(t[0], "input") and has(t[0], "constraint") and has(t[0], "output")
                    ),
                ),
                ("t2 a step, no code", lambda t: t[1]["route"] == "dsa" and not code_shown(t[1])),
                (
                    "t3 a different, concrete explanation",
                    lambda t: text(t[2]) != text(t[1]) and has(t[2], "index", "aba", "bb"),
                ),
                ("t3 the change is announced", lambda t: bool(adaptations(t[2]))),
                (
                    "t4 the SyntaxError is answered as a syntax problem",
                    lambda t: has(t[3], "Python 2", "Python3") and has(t[3], "type hint"),
                ),
                ("t4 syntax and logic are separate", lambda t: has(t[3], "Logic, separately")),
                (
                    "t5 a LeetCode submission",
                    lambda t: (
                        code_shown(t[4])
                        and has(t[4], "class Solution")
                        and has(t[4], "def longestPalindrome(self, s)")
                    ),
                ),
                (
                    "t5 both kinds of centre",
                    lambda t: has(t[4], "i + 1", "i+1") and has(t[4], "(i, i)", "odd"),
                ),
                ("t5 O(n^2), not O(n)", lambda t: has(t[4], "O(n^2)", "O(n²)")),
                (
                    "t6 still a LeetCode submission, no nonlocal",
                    lambda t: (
                        code_shown(t[5])
                        and has(t[5], "class Solution")
                        and "nonlocal" not in text(t[5]).split("```python")[-1]
                    ),
                ),
                ("no type hints in any code", lambda t: all(no_type_hints(x) for x in t)),
                (
                    "no 'take it slowly'",
                    lambda t: not any("take it slowly" in n for x in t for n in adaptations(x)),
                ),
            ],
            shared_account=False,
            strict=("no type hints in any code",),
        ),
        Scenario(
            "22",
            "Owner: docs/current-behavior.md, asking for the code four ways, then a buggy paste",
            "balanced",
            [
                FRAGMENT_OK,
                "give full code and tell me where is the bug",
                "give python code",
                "i asked for python code",
                FRAGMENT,
            ],
            [
                ("t1 code on the first ask", lambda t: has_code_block(t[0]) and no_refusal(t[0])),
                ("t2 code, not a refusal", lambda t: has_code_block(t[1]) and no_refusal(t[1])),
                ("t3 code, not another hint", lambda t: has_code_block(t[2]) and no_refusal(t[2])),
                (
                    "t4 code, not a greeting",
                    lambda t: has_code_block(t[3]) and not has(t[3], "share a problem statement"),
                ),
                ("no corpus dump on any turn", lambda t: all(no_dump(x) for x in t)),
                (
                    "no verdict is invented",
                    lambda t: all(
                        verdict(x).get("status") == "pass" or not has(x, "verified in the sandbox")
                        for x in t[:4]
                    ),
                ),
                (
                    "t5 the paste is not blamed",
                    lambda t: not has(t[4], "indentation", "unindent", "syntax error"),
                ),
                (
                    "t5 the real bug is named",
                    lambda t: has(t[4], "left -= 1", "right += 1", "left += 1", "right -= 1"),
                ),
                ("t5 a real verdict on their code", lambda t: learner_verdict(t[4]) == "fail"),
            ],
            shared_account=False,
            strict=("t2 code, not a refusal", "t3 code, not another hint"),
        ),
        # --- owner's conversations of 2026-10-11 -----------------------------------
        Scenario(
            "23",
            "Owner: a stated problem, 'explain with code line by line'",
            "guidance",
            [NEXT_PERMUTATION],
            [
                ("the code is shown, not a hint", lambda t: code_shown(t[0]) and no_refusal(t[0])),
                ("it is walked line by line", lambda t: walks_the_code(t[0])),
                (
                    "a real sandbox verdict",
                    lambda t: verdict(t[0]).get("status") in ("pass", "fail"),
                ),
                ("no generic hint card", lambda t: not has(t[0], "one step at a time")),
            ],
            shared_account=False,
            strict=("the code is shown, not a hint",),
        ),
        Scenario(
            "24",
            "Owner: 'explain the code of 3sum line by line', nothing pasted",
            "balanced",
            [
                "explain the code of 3sum line by line of how it works.",
                "explain binary search code line by line",
            ],
            [
                ("t1 answered, not the fallback", lambda t: not_the_fallback(t[0])),
                ("t1 the code is there", lambda t: "```python" in text(t[0])),
                ("t1 walked line by line", lambda t: walks_the_code(t[0])),
                ("t1 one closing question", lambda t: not has(t[0], "**Check:**")),
                ("t2 answered, not the fallback", lambda t: not_the_fallback(t[1])),
                ("t2 walked line by line", lambda t: walks_the_code(t[1])),
            ],
            shared_account=False,
            strict=("t1 answered, not the fallback",),
        ),
        Scenario(
            "25",
            "Owner: 'what is the output of this code', then 'why'",
            "balanced",
            [OUTPUT_ASK, "why b as contains the same value as a"],
            [
                ("t1 states the real output", lambda t: "[1]" in text(t[0])),
                ("t1 the code was run", lambda t: has(t[0], "in the sandbox")),
                (
                    "t1 an answer, not a report",
                    lambda t: not has(t[0], "structure:", "**cost", "not executed", "o(n)"),
                ),
                (
                    "t1 no DSA topic for a Python question",
                    lambda t: (t[0].get("plan") or {}).get("topic") is None,
                ),
                ("t2 says why", lambda t: has(t[1], "same list", "same object", "one list")),
                ("t2 an answer, not a report", lambda t: not has(t[1], "structure:", "**cost")),
                ("t2 answered, not the fallback", lambda t: not_the_fallback(t[1])),
            ],
            shared_account=False,
            strict=("t1 states the real output", "t2 says why"),
        ),
        Scenario(
            "26",
            "Owner: 'ask me 10 questions to test my knowledge', answered, profile moves",
            "balanced",
            [QUIZ_ASK, QUIZ_SKIPS, "ask me questions to check how much knowledge i have"],
            [
                ("t1 is a knowledge check", lambda t: t[0].get("route") == "assess"),
                ("t1 ten numbered questions", lambda t: numbered(t[0]) == 10),
                ("t1 not one practice problem", lambda t: not has(t[0], "leetcode.com", PICK)),
                ("t2 the answers are marked", lambda t: has(t[1], "you got **0 of")),
                ("t2 the answers are given", lambda t: len(text(t[1])) > 400),
                (
                    "t2 recorded as quiz evidence",
                    lambda t: (
                        {e.get("evidence_source") for e in t[1].get("events") or []} == {"quiz"}
                    ),
                ),
                ("t2 the profile moved", lambda t: bool(t[1].get("skill_deltas"))),
                ("t3 asks again, not a refusal", lambda t: numbered(t[2]) >= 3),
                ("t3 is not t1 word for word", lambda t: text(t[2]) != text(t[0])),
            ],
            shared_account=False,
            strict=("t1 ten numbered questions", "t2 the profile moved"),
        ),
        Scenario(
            "27",
            "Owner: a quiz, then 'start from basic not directly in DSA'",
            "balanced",
            [QUIZ_ASK, "start from basic not directly in DSA"],
            [
                ("t2 a new set of questions", lambda t: t[1].get("route") == "assess"),
                ("t2 numbered questions", lambda t: numbered(t[1]) >= 3),
                ("t2 not a study plan", lambda t: not has(t[1], "week 1", "1-week", "roadmap")),
                ("t2 is not t1", lambda t: text(t[1]) != text(t[0])),
                ("t2 not a refusal", lambda t: not has(t[1], PICK)),
            ],
            shared_account=False,
            strict=("t2 a new set of questions",),
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
