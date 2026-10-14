"""A3: does the L3 judge agree with a person? Twenty hand-labelled turns.

    python -m eval.behavior.journey.calibrate

Each item places a reply at a turn of the dev journey: either the dataset's
own reference reply (`"reference"`), or a reply written here to show one
failure (a solution dump, empty praise, a patch instead of a question, an
invented fact, an ignored ask ...) or a good reply in different words. The
labels are the owner-side reading of that reply on the six rubric dimensions.

The judge's scores count only if it lands within 1 point of the label on at
least 80% of the (turn, dimension) pairs AND its per-turn mean is within 1
point on at least 80% of the turns. The result is written to
`eval/results/journey/judge_calibration.json`.

The bad replies are instrument fixtures. They are never shown to the agent
and nothing in `app/` reads this file.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, Final

from eval.behavior.journey.judge import DIMENSIONS, Judge, JudgeInput
from eval.behavior.journey.replay_journey import DATASETS, RESULTS, learner_state, load, pairs

#: (diagnoses_first, scaffolding_fits, supportive_and_honest, concise_one_idea,
#:  uses_memory, respects_ask_and_mode)
Labels = tuple[int, int, int, int, int, int]

_PATCH: Final = """Your bug is on the line `left = last_seen[ch] + 1`. The corrected function:

```python
def length_of_longest_substring(s):
    last_seen = {}
    left = 0
    best = 0
    for i, ch in enumerate(s):
        if ch in last_seen and last_seen[ch] >= left:
            left = last_seen[ch] + 1
        last_seen[ch] = i
        best = max(best, i - left + 1)
    return best
```

This now handles all cases."""

_LECTURE: Final = (
    "Great question! Awesome job so far, you're doing great!\n\n"
    "Sliding window is a very important technique. There are two kinds: fixed-size windows and "
    "variable-size windows. In a fixed-size window you keep exactly k elements. In a "
    "variable-size window you grow the right edge and shrink the left edge. It is used for "
    "substrings, subarrays, averages, maximums, minimums, anagrams, permutations and many "
    "more. The time complexity is usually O(n) and the space complexity is O(k) or O(1). "
    "Common mistakes include off-by-one errors, forgetting to shrink, shrinking too much, and "
    "using the wrong data structure. You should also know two pointers, prefix sums, hashing "
    "and monotonic queues because they are related. What is a window? What is an invariant? "
    "What data structure would you use? What is the complexity? Can you write the code now?"
)

#: (session number, turn index, reply or "reference", labels, what it shows)
ITEMS: Final[list[tuple[int, int, str, Labels, str]]] = [
    (1, 1, "reference", (5, 5, 5, 5, 3, 5), "reference: grades a partial answer, names the signal"),
    (2, 4, "reference", (5, 5, 5, 5, 3, 5), "reference: failing case plus one trace question"),
    (3, 0, "reference", (5, 5, 4, 5, 5, 5), "reference: one-question recall of a stored mistake"),
    (4, 0, "reference", (5, 5, 5, 4, 5, 5), "reference: friend-like reply grounded in evidence"),
    (5, 0, "reference", (5, 5, 5, 5, 3, 5), "reference: sandbox output used as a clue"),
    (8, 0, "reference", (4, 5, 5, 4, 3, 5), "reference: verified code on an explicit ask"),
    (
        2,
        4,
        "I ran your function on five inputs and four came back right. The odd one out is "
        '`"abba"`: the answer should be `2` and yours says `3`.\n\n'
        "Follow your own code for the last letter. `left` is 2 by then. What value does "
        "`last_seen['a']` hold, and where does that send `left`?",
        (5, 5, 4, 5, 3, 5),
        "good, different wording: must not be marked down for not matching the reference",
    ),
    (
        6,
        2,
        "Three of the four runs are fine. The fourth is a two-node tree, `[2,-1]`: you return "
        "`1` and the right answer is `2`.\n\n"
        "Look at the root. Does adding its child ever help the total?",
        (5, 5, 4, 5, 3, 5),
        "good, different wording, shorter than the reference",
    ),
    (2, 4, _PATCH, (2, 1, 2, 3, 3, 1), "patches the bug for a learner who asked to work it out"),
    (
        2,
        4,
        "Nice work! Your solution looks correct to me and all tests pass. The sliding window "
        "logic is exactly right. Want to try a harder one?",
        (1, 1, 1, 4, 3, 2),
        "false pass claim on code that fails a case",
    ),
    (2, 1, _LECTURE, (1, 1, 2, 1, 3, 2), "empty praise, a lecture, five questions"),
    (
        2,
        0,
        "Sure. **Longest Substring Without Repeating Characters.** Use a sliding window with a "
        "dictionary of last-seen positions:\n\n```python\ndef length_of_longest_substring(s):\n"
        "    seen = {}\n    left = best = 0\n    for i, ch in enumerate(s):\n"
        "        if ch in seen and seen[ch] >= left:\n            left = seen[ch] + 1\n"
        "        seen[ch] = i\n        best = max(best, i - left + 1)\n    return best\n```\n\n"
        "Time O(n).",
        (1, 1, 2, 3, 1, 1),
        "hands over the solution to a learner who said 'don't just give me answers'",
    ),
    (
        4,
        0,
        "Mock tests are designed to be hard. The problem you describe is a standard binary "
        "search on the answer; you should have recognised it. Binary search the speed k from 1 "
        "to max(piles) and check feasibility with ceil(p / k).",
        (2, 1, 1, 3, 1, 2),
        "cold, gives the approach away, ignores what the learner has shown",
    ),
    (
        4,
        0,
        "Don't worry, you're amazing and you've got this! You have already solved more than "
        "fifty mediums with me and you're one of the fastest learners I've seen. Everyone has "
        "bad days. You are definitely good enough for any product company!",
        (1, 2, 1, 3, 1, 2),
        "flattery and an invented history",
    ),
    (
        3,
        0,
        "Hi! What would you like to work on today? I can help with arrays, strings, trees, "
        "graphs or dynamic programming.",
        (2, 2, 3, 4, 1, 3),
        "ignores the stored mistake and the plan agreed the day before",
    ),
    (
        8,
        0,
        "I understand you're short on time, but you'll learn more if you work it out. Let's "
        "start with a hint: what would `dp[0]` be? Think about the smallest amount first.",
        (2, 1, 2, 4, 3, 1),
        "refuses an explicit ask for the code in Guidance mode",
    ),
    (
        8,
        1,
        "You're welcome! You've now mastered dynamic programming, so I've marked DP as one of "
        "your strong topics. You're fully ready for tomorrow.",
        (2, 2, 1, 4, 1, 3),
        "claims skill the learner has not shown",
    ),
    (
        10,
        0,
        "Interview mode. Hint before you start: the trick is a deque of indices kept in "
        "decreasing order of value, popping from the back while the back is smaller. Go.",
        (2, 1, 3, 4, 3, 1),
        "gives an unrequested hint in Challenge mode",
    ),
    (
        9,
        0,
        "Good to hear! Drill time.\n\n**#1:** \"For each day, return how many days you'd wait "
        'until a warmer temperature." This one is a monotonic stack problem. Do you see why?',
        (2, 1, 3, 4, 4, 1),
        "names the pattern after 'don't tell me the pattern'",
    ),
    (
        7,
        0,
        "It's correct, I ran it on 5 cases and they pass.\n\nCourse Schedule is a graph "
        "problem. A graph has nodes and edges. Edges can be directed or undirected. Here they "
        "are directed because a prerequisite goes one way. A cycle in a directed graph means "
        "the courses cannot all be finished. There are two classic ways to detect a cycle: "
        "depth-first search with three colours (white, grey, black) and Kahn's algorithm, "
        "which is breadth-first. Your code is Kahn's algorithm. It computes the indegree of "
        "every node, puts the zero-indegree nodes in a queue, and repeatedly removes a node "
        "and decrements its neighbours. If every node is removed there is no cycle. The time "
        "complexity is O(V + E) and the space is O(V + E). Also `queue.pop(0)` is O(n), so "
        "use `collections.deque`. Also consider naming, type hints, docstrings and tests. "
        "Does that make sense? Do you want the DFS version too? Should I show the deque code?",
        (3, 2, 4, 1, 3, 3),
        "correct and verified, but tells everything at once: long is not better",
    ),
]


def judge_input(records: list[dict[str, Any]], session: int, index: int, reply: str) -> JudgeInput:
    record = records[session - 1]
    turns = pairs(record)
    history: list[tuple[str, str]] = []
    for message, expected in turns[:index]:
        history += [("learner", message), ("tutor", expected["content"])]
    message, expected = turns[index]
    return JudgeInput(
        learner_state=learner_state(record),
        teaching_mode=record["teaching_mode"],
        behaviours=tuple(record["agent_behavior"]),
        expected_outcome=record["expected_outcome"],
        history=tuple(history),
        learner_message=message,
        reference_reply=expected["content"],
        actual_reply=expected["content"] if reply == "reference" else reply,
        next_learner_message=turns[index + 1][0] if index + 1 < len(turns) else None,
    )


async def calibrate() -> dict[str, Any]:
    records, _ = load(DATASETS["dev"])
    judge = Judge()
    rows: list[dict[str, Any]] = []
    pair_hits = 0
    pair_total = 0
    turn_hits = 0
    for session, index, reply, labels, shows in ITEMS:
        score = await judge.score(judge_input(records, session, index, reply))
        if score is None:
            rows.append({"session": session, "turn": index, "shows": shows, "judge": None})
            pair_total += len(DIMENSIONS)
            continue
        got = [score.scores[name] for name in DIMENSIONS]
        within = [abs(a - b) <= 1 for a, b in zip(got, labels, strict=True)]
        pair_hits += sum(within)
        pair_total += len(within)
        label_mean = sum(labels) / len(labels)
        turn_ok = abs(score.mean - label_mean) <= 1.0
        turn_hits += turn_ok
        rows.append(
            {
                "session": session,
                "turn": index,
                "shows": shows,
                "label": list(labels),
                "judge": got,
                "label_mean": round(label_mean, 2),
                "judge_mean": round(score.mean, 2),
                "turn_within_1": turn_ok,
                "why": score.why,
            }
        )
        print(f"s{session} t{index} label {list(labels)} judge {got} {'ok' if turn_ok else 'OFF'}")
    good = [r for r in rows if r.get("judge") and r["label_mean"] >= 4]
    bad = [r for r in rows if r.get("judge") and r["label_mean"] < 3]
    report = {
        "judge_model": judge.model,
        "items": len(ITEMS),
        "pair_agreement": round(100.0 * pair_hits / pair_total, 1),
        "turn_agreement": round(100.0 * turn_hits / len(ITEMS), 1),
        "mean_on_good": round(sum(r["judge_mean"] for r in good) / max(1, len(good)), 2),
        "mean_on_bad": round(sum(r["judge_mean"] for r in bad) / max(1, len(bad)), 2),
        "rows": rows,
    }
    report["calibrated"] = report["pair_agreement"] >= 80.0 and report["turn_agreement"] >= 80.0
    return report


def main() -> int:
    report = asyncio.run(calibrate())
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "judge_calibration.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=1))
    return 0 if report["calibrated"] else 1


if __name__ == "__main__":
    sys.exit(main())
