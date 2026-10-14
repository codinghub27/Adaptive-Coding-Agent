"""Builds heldout_journey_10.jsonl: the HELD-OUT twin of the 10-session journey.

Same arc, same behaviour tags and the same eight patterns as
`../adaptive_journey_10.jsonl`, with DIFFERENT problems, paraphrased learner
turns and new buggy/fixed code. It exists to catch overfitting: nothing in
`app/` may be tuned on it, and a packet is judged by how close its held-out
scores stay to its dev scores.

Run from this directory, after `verify_examples.py` has written
`sandbox_ground_truth.json` (in a container):

    python build_dataset.py

- Every code block a learner submits is taken verbatim from verify_examples.py.
- Every sandbox claim is taken from sandbox_ground_truth.json, and the
  validator checks the text matches it.
- Skill values are computed with the same calibration as the dev set.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from verify_examples import SNIPPETS, TREE_HELPERS

HERE = Path(__file__).resolve().parent
GROUND = json.loads((HERE / "sandbox_ground_truth.json").read_text(encoding="utf-8"))

PRIOR, ALPHA, CONCEPT_ALPHA, WEAK, HARD = 0.5, 0.3, 0.1, 0.42, 0.68
GRADE_VALUE = {"correct": 1.0, "partial": 0.5, "incorrect": 0.0}

FAMILY = {
    "sliding_window": "two_pointer_window",
    "two_pointers": "two_pointer_window",
    "bfs": "graphs",
    "topological_sort": "graphs",
    "binary_search_on_answer": "searching",
    "trees": "trees",
    "dp_1d": "dynamic_programming",
    "monotonic_stack": "stack",
}


def code_of(ex: str, label: str) -> str:
    for e, lab, src, _fn, _cases in SNIPPETS:
        if e == ex and lab == label:
            src = src.replace(TREE_HELPERS, "")
            src = re.sub(r"\n\ndef solve\(values\):\n    return .*\n", "\n", src)
            return "```python\n" + src.strip() + "\n```"
    raise KeyError((ex, label))


def sbx(ex: str, label: str, provenance: str = "extracted") -> dict[str, Any]:
    g = GROUND[f"{ex}:{label}"]
    return {
        "ref": f"{ex}:{label}",
        "ran": True,
        "passed": g["passed"],
        "total": g["total"],
        "first_failing": g["first_failing"],
        "provenance": provenance,
    }


def U(text: str) -> dict[str, Any]:  # noqa: N802 - mirrors the dev builder
    return {"role": "user", "content": text}


def A(text: str, **meta: Any) -> dict[str, Any]:  # noqa: N802
    return {"role": "assistant", "content": text, "meta": meta}


def Q(topic: str) -> dict[str, Any]:  # noqa: N802
    return {"kind": "question", "topic": topic}


def SUBMIT(topic: str) -> dict[str, Any]:  # noqa: N802
    return {"kind": "code_submission", "topic": topic}


def concept(topic: str, grade: str) -> dict[str, Any]:
    return {"topic": topic, "provenance": "concept_check", "grade": grade}


def ran(topic: str, solved: bool) -> dict[str, Any]:
    return {"topic": topic, "provenance": "sandbox", "solved": solved}


EXAMPLES: list[dict[str, Any]] = []

# ---------------------------------------------------------------- HX01 diagnose
EXAMPLES.append(
    {
        "conversation_id": "heldout_01_diagnose",
        "session": 1,
        "day": 1,
        "title": "First session: find the real gap before teaching anything",
        "intent": "APPROACH_DISCUSSION",
        "teaching_mode": "Balanced",
        "problem": None,
        "agent_behavior": [
            "acknowledge_feeling_briefly_then_act",
            "diagnose_before_teaching",
            "one_question_at_a_time",
            "grade_each_answer",
            "name_the_signal_word",
            "summarize_gap_from_evidence",
            "propose_plan_and_ask_consent",
            "record_learning_preference",
        ],
        "conversation": [
            U(
                "hi, interviews are about two months away. I can do the easy ones but with "
                "medium problems I just stare at the screen, I never know what technique it "
                "wants. feeling pretty behind tbh"
            ),
            A(
                "Two months is plenty for this. And staring at a medium without knowing which "
                "technique it wants is usually not missing knowledge. It is missing the link "
                "from the wording to the technique.\n\n"
                "Let's measure that rather than assume it. I'll give you three one-line "
                "problems; tell me the approach you'd try. No code.\n\n"
                '**#1:** "Given an array of positive numbers and a target, find the length of '
                'the shortest contiguous subarray whose sum is at least the target." '
                "Which approach?",
                intent="APPROACH_DISCUSSION",
                route="clarify",
                plan={"difficulty": "medium", "assistance_level": "concept"},
                pending_check={
                    **Q("sliding_window"),
                    "expected_concepts": ["contiguous", "sliding window"],
                },
            ),
            U("prefix sums maybe? to get subarray sums fast"),
            A(
                "Partly. Prefix sums do give you subarray sums, but they are a tool here, "
                "not the plan.\n\n"
                "The clue is **shortest contiguous subarray** with a condition, and the "
                "numbers are positive, so growing the window only raises the sum and "
                "shrinking only lowers it. That is a **sliding window**.\n\n"
                '**#2:** "Given a string, decide whether it reads the same forwards and '
                'backwards, ignoring punctuation." Which approach?',
                grade="partial",
                evidence=[concept("sliding_window", "partial")],
                pending_check=Q("two_pointers"),
            ),
            U("two pointers from both ends moving toward the middle"),
            A(
                "Yes, and the reason matters: the check is **symmetric**, so comparing the two "
                "ends and stepping inward covers every pair once.\n\n"
                '**#3:** "On a chessboard, find the fewest moves a knight needs to get from one '
                'square to another." Which approach?',
                grade="correct",
                evidence=[concept("two_pointers", "correct")],
                pending_check=Q("bfs"),
            ),
            U("recursion / dfs trying all the moves and keeping the minimum?"),
            A(
                "That is the tempting one, and it is the one to drop. DFS dives down one "
                "sequence of moves before trying others, so the first time it reaches the "
                "target tells you nothing about the fewest moves. **BFS** visits squares in "
                "order of distance, so the first arrival is the shortest. Signal: "
                '**"fewest moves" + every move costs the same → BFS.**\n\n'
                "What those three answers show:\n"
                "- The techniques themselves are there: two pointers was immediate, and you "
                "know prefix sums.\n"
                '- The gap is **reading the wording**: "shortest contiguous" → window, '
                '"fewest moves, equal cost" → BFS.\n\n'
                "That can be trained directly. Plan: one pattern at a time, and before any "
                "code you name the pattern **and the words that pointed to it**. Start with "
                "sliding window now?",
                grade="incorrect",
                misconception="bfs.dfs_for_unweighted_shortest_path",
                evidence=[concept("bfs", "incorrect")],
            ),
            U("ok yes. but please don't hand me solutions, I want to work them out"),
            A(
                "Agreed. When you're stuck I'll ask a question first, and only get more direct "
                "if that doesn't move you. Sliding window, next message.",
                profile_update={
                    "learning_preferences": {
                        "prefers_hints": True,
                        "hint_style": "nudge_question_first",
                    }
                },
            ),
        ],
        "expected_outcome": (
            "The agent locates the real gap (recognition) with three graded one-line questions, "
            "explains each miss briefly, and gets consent for a plan. No code, no lecture."
        ),
    }
)

# ---------------------------------------------------------------- HX02 first window
EXAMPLES.append(
    {
        "conversation_id": "heldout_02_first_window",
        "session": 1,
        "day": 1,
        "title": "Sliding window with a hint ladder; the learner finds the bug from a sandbox trace",
        "intent": "DSA_SOLVE",
        "teaching_mode": "Balanced",
        "problem": "Minimum Size Subarray Sum",
        "agent_behavior": [
            "ask_pattern_and_signal_before_code",
            "hint_ladder_one_rung_at_a_time",
            "stop_climbing_when_learner_has_enough",
            "run_learner_code_in_sandbox",
            "show_failing_case_not_the_fix",
            "guided_trace_question",
            "learner_writes_the_fix",
            "record_misconception",
        ],
        "conversation": [
            U("alright, hit me with a sliding window one"),
            A(
                "**Minimum Size Subarray Sum.** Given positive integers `nums` and a `target`, "
                "return the length of the shortest contiguous subarray whose sum is at least "
                "`target`, or 0 if there is none. `target = 7, nums = [2,3,1,2,4,3]` → 2 "
                "(`[4,3]`).\n\n"
                "First: which pattern, and which words point to it?",
                intent="DSA_SOLVE",
                route="dsa",
                plan={"difficulty": "medium", "assistance_level": "hint", "hint_rung": 0},
                pending_check=Q("sliding_window"),
            ),
            U("sliding window. 'contiguous subarray' and they want the shortest one"),
            A(
                "Both clues, as we practised. Next: when is the current window good enough to "
                "count as a candidate answer?",
                grade="correct",
                plan={"assistance_level": "hint", "hint_rung": 1},
                evidence=[concept("sliding_window", "correct")],
            ),
            U("when its sum is >= target"),
            A(
                "Right. So once the window reaches the target, what should happen to the left "
                "edge, and for how long?",
                grade="correct",
                plan={"assistance_level": "hint", "hint_rung": 2},
            ),
            U("move it right to make the window smaller, as long as the sum still reaches target?"),
            A(
                "That is the idea in full. You have what you need. Write it and I'll run it.",
                grade="correct",
                plan={"assistance_level": "hint", "hint_rung": 2},
                pending_check=SUBMIT("sliding_window"),
            ),
            U("here is what I have:\n" + code_of("hx02", "learner_v1_buggy")),
            A(
                "Ran it on 5 cases: **1/5 pass**. The first one that fails:\n\n"
                "`target = 7, nums = [2,3,1,2,4,3]` → expected `2`, your code returns `4`.\n\n"
                "Trace it. After reading the `4` the window is `[3,1,2,4]`, sum 10. Your code "
                "drops one element from the left. What is the sum then, and does your code "
                "look at the window again before reading the next number?",
                intent="CODE_DEBUG",
                route="dsa",
                sandbox=sbx("hx02", "learner_v1_buggy"),
                evidence=[ran("sliding_window", False)],
            ),
            U(
                "sum is 7, still >= target... and no, it goes straight to the next number. "
                "so I shrink only once even when I could keep shrinking"
            ),
            A(
                "Exactly. One new number can make room for **several** steps of the left edge, "
                "and an `if` takes only one. You know what to change, so change it.",
                grade="correct",
                misconception="sliding_window.shrink_once_instead_of_while",
            ),
            U(code_of("hx02", "learner_v2_fixed")),
            A(
                "**6/6 pass**, including the case with no valid window (returns 0) and "
                "`[1,1,1,1,1,3]` with target 3. Time O(n): each element enters and leaves "
                "once. Space O(1).\n\n"
                "Keep this: shrinking a window is a loop, not a single step. I've noted that "
                "one and will ask you about it again.",
                sandbox=sbx("hx02", "learner_v2_fixed"),
                evidence=[ran("sliding_window", True)],
                profile_update={
                    "common_errors": {"sliding_window.shrink_once_instead_of_while": 1}
                },
            ),
        ],
        "expected_outcome": (
            "The learner names the pattern, reaches the invariant through three hint rungs, and "
            "fixes the bug after one trace question. The agent never writes the solution."
        ),
    }
)

# ---------------------------------------------------------------- HX03 less help
EXAMPLES.append(
    {
        "conversation_id": "heldout_03_less_help",
        "session": 2,
        "day": 2,
        "title": "Returning learner: recall yesterday's mistake, then visibly less scaffolding",
        "intent": "DSA_SOLVE",
        "teaching_mode": "Balanced",
        "problem": "Fruit Into Baskets",
        "agent_behavior": [
            "spaced_recall_of_recorded_misconception",
            "reduce_scaffolding_after_success",
            "let_learner_state_the_invariant",
            "run_learner_code_in_sandbox",
            "ask_complexity",
            "update_topic_status_from_evidence",
        ],
        "conversation": [
            U("I'm back. what are we doing today"),
            A(
                "Good to see you. Quick warm-up from yesterday: in the subarray-sum problem, "
                "why did shrinking with an `if` give the wrong answer?",
                intent="CONCEPT_EXPLANATION",
                route="clarify",
                pending_check=Q("sliding_window"),
                memory_used=["common_errors.sliding_window.shrink_once_instead_of_while"],
            ),
            U(
                "because after one new element the window might be shrinkable several times. needs a while"
            ),
            A(
                "That's it. Today is another variable window, and I'm going to help less, "
                "because yesterday says you can take it.\n\n"
                "**Fruit Into Baskets.** `fruits[i]` is the type of tree `i` in a row. You have "
                "two baskets, each holds one type. Starting anywhere, pick one fruit from every "
                "tree moving right until a tree doesn't fit. Return the most fruit you can "
                "collect.\n\n"
                "Pattern, and the words that give it away?",
                grade="correct",
                route="dsa",
                plan={"difficulty": "medium", "assistance_level": "hint", "hint_rung": 0},
                evidence=[concept("sliding_window", "correct")],
                pending_check=Q("sliding_window"),
            ),
            U(
                "a row of trees picked one after another = contiguous, so window. two baskets = "
                "window is valid while it has at most 2 distinct types, shrink from the left "
                "when there are 3"
            ),
            A(
                "You just stated the invariant and the shrink rule on your own. Write it.",
                grade="correct",
                pending_check=SUBMIT("sliding_window"),
            ),
            U(code_of("hx03", "learner_v1")),
            A(
                "**5/5 pass** on the first run, including the empty row and the long mixed one. "
                "What's the complexity?",
                sandbox=sbx("hx03", "learner_v1"),
                evidence=[ran("sliding_window", True)],
                pending_check=Q("sliding_window"),
            ),
            U(
                "O(n) time, left and right each only move forward. the dict never has more than 3 keys so O(1) space"
            ),
            A(
                "Correct on both, with the right reason. Two window problems in two days, and "
                "today you used **no hints**. I'm moving sliding window to occasional review. "
                "Next time we open a new pattern.",
                grade="correct",
            ),
        ],
        "expected_outcome": (
            "The agent uses the stored mistake for a one-question recall, then gives clearly less "
            "help than the day before because the evidence says the learner is ready."
        ),
    }
)

# ---------------------------------------------------------------- HX04 bad day
EXAMPLES.append(
    {
        "conversation_id": "heldout_04_bad_day",
        "session": 3,
        "day": 6,
        "title": "A bad contest: answer like a friend with real evidence, then teach the missed pattern",
        "intent": "DSA_SOLVE",
        "teaching_mode": "Balanced",
        "problem": "Capacity To Ship Packages Within D Days",
        "agent_behavior": [
            "acknowledge_emotion_genuinely",
            "counter_self_doubt_with_profile_evidence",
            "offer_choice_to_rest",
            "concept_first_reframe",
            "guiding_questions_to_key_insight",
            "run_learner_code_in_sandbox",
            "point_at_line_not_rewrite",
            "close_with_rest_suggestion",
        ],
        "conversation": [
            U(
                "rough evening. did a practice contest and got zero on a question about shipping "
                "packages in D days, I had no idea how to even begin. my friends all got it. "
                "starting to think I'm not cut out for this"
            ),
            A(
                "That's a miserable feeling, more so when the people around you got it.\n\n"
                "I don't agree with \"not cut out for this\", though, and here is what I'm going "
                "on. Five days ago you answered a shortest-subarray question with the wrong "
                "tool. Four days ago you named the pattern and its invariant yourself and "
                "passed every test without a hint. That is fast progress on a pattern you had "
                "just met. Tonight's question used a pattern you have not met.\n\n"
                "It rests on one idea that people rarely find alone. Shall we take it apart "
                "now? If you'd rather stop for tonight, that is a fine choice too.",
                intent="APPROACH_DISCUSSION",
                route="clarify",
                memory_used=[
                    "skills.sliding_window trend (fail → pass → pass)",
                    "heldout_03: zero-hint solve",
                ],
            ),
            U("let's do it now, it'll bug me otherwise"),
            A(
                'Good. Change the question you ask. Not "what is the smallest capacity?" but: '
                '**"if someone handed me a capacity, could I tell whether it is enough?"** '
                "Could you?",
                intent="DSA_SOLVE",
                route="dsa",
                plan={"difficulty": "medium", "assistance_level": "concept", "hint_rung": 0},
                pending_check=Q("binary_search_on_answer"),
            ),
            U(
                "yeah, load packages in order, start a new day when the next one doesn't fit, count the days and compare with D"
            ),
            A(
                "Right. Now: if capacity 10 is enough, is capacity 11 enough?",
                grade="correct",
                plan={"assistance_level": "concept", "hint_rung": 1},
            ),
            U("sure, a bigger ship never needs more days"),
            A(
                'That is the whole trick. "Enough" is **monotonic** in the capacity: the '
                "capacities read `no no no yes yes yes`, and you want the first `yes`. What "
                "finds the first `yes` in a line like that?",
                grade="correct",
                plan={"assistance_level": "concept", "hint_rung": 2},
            ),
            U("oh, binary search. over the capacity itself"),
            A(
                'Yes: **binary search on the answer**. The signal: *"smallest value such that '
                'something is possible"*, and checking one value is easy. Write it.',
                grade="correct",
                evidence=[concept("binary_search_on_answer", "correct")],
                pending_check=SUBMIT("binary_search_on_answer"),
            ),
            U(code_of("hx04", "learner_v1_buggy")),
            A(
                "The search itself is right. **3/4 pass**, and the one that fails: "
                "`weights = [1,2,3,1,1], days = 4` → expected `3`, you return `2`.\n\n"
                "Take capacity 2 and the package of weight 3. What does your loop do with it?",
                intent="CODE_DEBUG",
                sandbox=sbx("hx04", "learner_v1_buggy"),
                evidence=[ran("binary_search_on_answer", False)],
            ),
            U(
                "it starts a new day and loads the 3 anyway... onto a ship that holds 2. so "
                "capacities smaller than the heaviest package look fine. lo should start at "
                "max(weights)"
            ),
            A(
                "Yes. No capacity below the heaviest package can ever work, so the search must "
                "not consider one. Starting `lo` there is the standard way to say that.",
                grade="correct",
                misconception="binary_search_on_answer.lower_bound_below_feasible",
            ),
            U(code_of("hx04", "learner_v2_fixed")),
            A(
                "**5/5 pass**, including four equal packages in four days.\n\n"
                "That is the question you scored zero on this evening, solved by you in one "
                'sitting. When you next read *"smallest/largest value such that…"*, ask '
                "whether you can check a single guess. Now stop for the night. You did the "
                "hard part.",
                sandbox=sbx("hx04", "learner_v2_fixed"),
                evidence=[ran("binary_search_on_answer", True)],
                profile_update={
                    "common_errors": {"binary_search_on_answer.lower_bound_below_feasible": 1}
                },
            ),
        ],
        "expected_outcome": (
            "The agent answers like a friend using real evidence (no empty praise), offers rest, "
            "then teaches binary search on the answer through three questions. It points at the "
            "faulty behaviour with a question instead of rewriting the code."
        ),
    }
)

# ---------------------------------------------------------------- HX05 two jobs
EXAMPLES.append(
    {
        "conversation_id": "heldout_05_two_jobs",
        "session": 4,
        "day": 9,
        "title": "Tree misconception: one return value asked to do two jobs",
        "intent": "CODE_DEBUG",
        "teaching_mode": "Balanced",
        "problem": "Binary Tree Tilt",
        "agent_behavior": [
            "validate_correct_part_of_reasoning",
            "use_sandbox_output_as_clue",
            "detect_conceptual_misconception",
            "ask_what_the_number_counts",
            "separate_return_value_from_global_answer",
            "learner_writes_the_fix",
            "name_and_record_misconception",
        ],
        "conversation": [
            U(
                "binary tree tilt: a node's tilt is |sum of left subtree - sum of right "
                "subtree|, return the sum of all tilts. for [1,2,3] I get 0 and it should be 1. "
                "I add up the tilt of the left, the right and this node, isn't that the answer?\n"
                + code_of("hx05", "learner_v1_buggy")
            ),
            A(
                "Ran it on 4 trees: **1/4 pass** (only the empty tree). Adding every node's "
                "tilt is the right total. What goes wrong is what the function hands back.\n\n"
                "Clue: for `[1,2,3]` it returns `0`. The two leaves each return 0, so the root "
                "computes `abs(0 - 0)`. What did the root need from its children to compute its "
                "own tilt?",
                intent="CODE_DEBUG",
                route="debug",
                plan={"difficulty": "easy", "assistance_level": "concept"},
                sandbox=sbx("hx05", "learner_v1_buggy"),
                evidence=[ran("trees", False)],
                pending_check=Q("trees"),
            ),
            U(
                "...their sums. 2 and 3. but my function gives back tilts, so the root never sees the values"
            ),
            A(
                "Exactly. One return value is being asked to do **two jobs**:\n"
                "1. What the parent needs: the **sum** of my subtree.\n"
                "2. The answer being collected: my **tilt**, added to a running total.\n\n"
                "Those are different numbers. Which one should be returned to the parent, and "
                "where does the other one live?",
                grade="correct",
                misconception="trees.returnable_vs_global_path",
                pending_check=Q("trees"),
            ),
            U("return the subtree sum. keep the tilt total in a variable outside the helper"),
            A(
                "That's it. Write it.",
                grade="correct",
                evidence=[concept("trees", "correct")],
                pending_check=SUBMIT("trees"),
            ),
            U(code_of("hx05", "learner_v2_fixed")),
            A(
                "**4/4 pass**, including the three-level tree where the tilts add up to 15.\n\n"
                "Name for this trap: **returnable value vs. global answer**. Many tree problems "
                "have it. I've recorded it and will point it out when it comes back.",
                sandbox=sbx("hx05", "learner_v2_fixed"),
                evidence=[ran("trees", True)],
                profile_update={"common_errors": {"trees.returnable_vs_global_path": 1}},
            ),
        ],
        "expected_outcome": (
            "The agent uses the sandbox output as a clue, repairs the mental model with two "
            "questions, and records a named misconception instead of pasting fixed code."
        ),
    }
)

# ---------------------------------------------------------------- HX06 spots the trap
EXAMPLES.append(
    {
        "conversation_id": "heldout_06_spots_the_trap",
        "session": 5,
        "day": 16,
        "title": "Harder sibling problem: the learner spots the recorded trap unprompted",
        "intent": "DSA_SOLVE",
        "teaching_mode": "Balanced",
        "problem": "Longest Univalue Path",
        "agent_behavior": [
            "raise_difficulty_after_success",
            "connect_to_recorded_misconception_without_solving_it",
            "let_learner_identify_trap",
            "sandbox_beats_example_tests",
            "minimal_hint_on_edge_case",
            "praise_specific_correct_decision",
            "mark_misconception_resolved",
        ],
        "conversation": [
            U("another tree question please, a tougher one this time"),
            A(
                "You got through tilt, so here is a harder relative: **Longest Univalue Path**. "
                "Return the length (in edges) of the longest path on which every node has the "
                "same value. The path may or may not pass through the root. "
                "`[5,4,5,1,1,null,5]` → 2.\n\n"
                "It contains the trap you met last week. Before any code: what does your "
                "recursion give back to the parent, and what do you keep track of globally?",
                intent="DSA_SOLVE",
                route="practice",
                plan={"difficulty": "hard", "assistance_level": "hint", "hint_rung": 0},
                pending_check=Q("trees"),
                memory_used=["common_errors.trees.returnable_vs_global_path", "skills.trees"],
            ),
            U(
                "give back the longer of the two same-value arms going down from this node. "
                "globally track left arm + right arm, since the best path can bend at a node"
            ),
            A(
                "You separated the two jobs yourself this time, without a hint. Go and write it.",
                grade="correct",
                evidence=[concept("trees", "correct")],
                pending_check=SUBMIT("trees"),
            ),
            U("works on both examples from the page:\n" + code_of("hx06", "learner_v1_buggy")),
            A(
                "**3/4 pass.** Both page examples pass and a small one fails: `[3,2,3,2]` → "
                "expected `1`, you return `2`.\n\n"
                "At the root (value 3), its left child is 2. What does `la` hold after your "
                "`if` is skipped?",
                intent="CODE_DEBUG",
                sandbox=sbx("hx06", "learner_v1_buggy"),
                evidence=[ran("trees", False)],
            ),
            U(
                "it still holds the child's arm, 1. but that arm isn't my value, it should count as 0 for me"
            ),
            A(
                "Right. An arm only continues through a child with the same value. Otherwise it "
                "is zero for this node.",
                grade="correct",
            ),
            U(code_of("hx06", "learner_v2_fixed")),
            A(
                "**5/5 pass**, including the tree where both subtrees are univalue but differ "
                "from the root.\n\n"
                "Notice that you return `max(la, ra)` and never the sum, and you did that "
                "from the first draft. The returnable-vs-global trap is one you now catch "
                "alone, so I'm marking it resolved.",
                sandbox=sbx("hx06", "learner_v2_fixed"),
                evidence=[ran("trees", True)],
                profile_update={"common_errors": {"trees.returnable_vs_global_path": "resolved"}},
            ),
        ],
        "expected_outcome": (
            "The agent links the new problem to the recorded misconception without solving it. "
            "The learner names the trap unprompted, and the sandbox catches a case the page "
            "examples miss."
        ),
    }
)

# ---------------------------------------------------------------- HX07 why it works
EXAMPLES.append(
    {
        "conversation_id": "heldout_07_why_it_works",
        "session": 6,
        "day": 20,
        "title": "Working code, missing 'why': a review that builds interview-ready understanding",
        "intent": "CODE_REVIEW",
        "teaching_mode": "Balanced",
        "problem": "Parallel Courses",
        "agent_behavior": [
            "verify_before_reviewing",
            "confirm_correctness_first",
            "ask_for_signal_words",
            "explain_why_algorithm_detects_cycle_via_question",
            "one_concrete_performance_note",
            "state_complexity",
            "answer_original_question_directly",
        ],
        "conversation": [
            U(
                "parallel courses: n courses numbered 1..n, relations [a, b] mean a must be "
                "taken before b, any number of courses per semester. return the minimum number "
                "of semesters, or -1 if it's impossible. this got accepted but I pieced it "
                "together from memory and can't say if it's BFS or DP, or why the -1 part "
                "works\n" + code_of("hx07", "learner_v1")
            ),
            A(
                "First: it is correct. I ran it on 5 cases, a 3-course cycle among them: "
                "**5/5 pass**.\n\n"
                "Now the *why*, since that is what you'd be asked. Which words in the "
                "statement say this is a graph problem?",
                intent="CODE_REVIEW",
                route="explain",
                plan={"difficulty": "medium", "assistance_level": "concept"},
                sandbox=sbx("hx07", "learner_v1"),
                evidence=[ran("topological_sort", True)],
                pending_check=Q("topological_sort"),
            ),
            U("'must be taken before'? it's a dependency between two courses"),
            A(
                'Yes. *Before / depends on / order* → a **directed graph**, and "minimum '
                'semesters" → the **longest chain** of dependencies.\n\n'
                "Your loop is **Kahn's algorithm** taken one layer at a time: each semester it "
                "takes every course with nothing left to wait for. If the relations contain a "
                "cycle, what happens to the courses on it?",
                grade="correct",
                pending_check=Q("topological_sort"),
            ),
            U(
                "they each wait on another one in the loop, so indegree never gets to 0 and they're never studied. studied < n"
            ),
            A(
                "Exactly, and that is why `studied == n` is the test for -1. To your question: "
                "it is BFS by layers (a topological sort), and the number of layers is the "
                "answer. A DP over the longest path gives the same number, but needs the order "
                "first.\n\n"
                "One review note: `upcoming = upcoming + [nxt]` builds a new list each time, "
                "which is quadratic when one semester unlocks many courses. "
                "`upcoming.append(nxt)` is O(1). With that, time and space are O(V + E).",
                grade="correct",
                evidence=[concept("topological_sort", "correct")],
            ),
        ],
        "expected_outcome": (
            "The agent verifies before reviewing, turns 'accepted but I can't explain it' into a "
            "two-question explanation, gives one concrete performance note, and answers the "
            "BFS-or-DP question directly."
        ),
    }
)

# ---------------------------------------------------------------- HX08 night before
EXAMPLES.append(
    {
        "conversation_id": "heldout_08_night_before",
        "session": 7,
        "day": 24,
        "title": "Night before an interview: respect the explicit ask, verified code, no fake progress",
        "intent": "DSA_SOLVE",
        "teaching_mode": "Guidance",
        "problem": "House Robber",
        "agent_behavior": [
            "respect_explicit_request_and_time_pressure",
            "reveal_only_sandbox_verified_code",
            "give_one_core_idea_not_a_lecture",
            "teach_recognition_signal",
            "honest_no_skill_credit",
            "suggest_spaced_reattempt",
            "prioritize_sleep",
        ],
        "conversation": [
            U(
                "interview is at 9 tomorrow and I never got to dynamic programming. please just "
                "show me the house robber solution, I don't have time to be guided through it"
            ),
            A(
                "Understood. Tonight is for being prepared, not for practice. Here is the "
                "verified solution and the single idea under it, so you can rebuild it if they "
                "change the question.\n\n"
                "**Idea:** at each house you either rob it (then the best total *without* the "
                "previous house, plus this one) or skip it (then the best total so far). Carry "
                "those two numbers along.\n\n"
                + code_of("hx08", "agent_reference")
                + "\n\nRan it: **5/5 pass**, including no houses → 0 and `[2,1,1,2]` → 4. "
                "Time O(n), space O(1).\n\n"
                "**Recognition signal:** *best total* + a choice at each item that restricts "
                "the next one + the answer for the first `i` items builds on shorter prefixes "
                "→ **1-D DP**.",
                intent="DSA_SOLVE",
                route="dsa",
                plan={"difficulty": "medium", "assistance_level": "full"},
                escalation={
                    "reason": "explicit_ask",
                    "teaching_mode": "Guidance",
                    "verified_reference": True,
                },
                sandbox=sbx("hx08", "agent_reference", "synthesised"),
                evidence=[
                    {
                        "topic": "dp_1d",
                        "provenance": "exposure",
                        "needed_full_solution": True,
                        "solved": None,
                    }
                ],
            ),
            U("ok that's clearer than I thought it would be, thank you"),
            A(
                "Glad it is. One honest note: I am not recording this as you having solved a "
                "DP problem. You haven't yet, and that is fine. After the interview, write it "
                "again from an empty file. If it comes out, that is the real gain.\n\n"
                "Sleep is worth more than another problem now. Good luck at 9, and tell me how "
                "it goes.",
                memory_used=["skills.dp_1d = PRIOR (no prior evidence)"],
            ),
        ],
        "expected_outcome": (
            "Under time pressure and an explicit ask (Guidance mode), the agent gives a "
            "sandbox-verified solution with one idea and a recognition signal, records exposure "
            "only (no skill credit), and puts the learner's rest first."
        ),
    }
)

# ---------------------------------------------------------------- HX09 recognition drill
EXAMPLES.append(
    {
        "conversation_id": "heldout_09_recognition_drill",
        "session": 8,
        "day": 26,
        "title": "Mixed recognition drill: follow up, grade each answer, target the real gap",
        "intent": "APPROACH_DISCUSSION",
        "teaching_mode": "Balanced",
        "problem": None,
        "agent_behavior": [
            "follow_up_on_previous_session",
            "respect_dont_tell_me_the_pattern",
            "include_the_missed_problem_type",
            "grade_correct_partial_incorrect",
            "teach_only_the_missing_pattern",
            "distinguish_unlearned_from_forgotten",
            "plan_next_session_from_evidence",
        ],
        "conversation": [
            U(
                "interview went fine, I got 2 of the 3. the one I couldn't do was about the "
                "biggest rectangle under some bars. can we do pattern spotting practice? don't "
                "give away the pattern"
            ),
            A(
                "Two of three the morning after your first night of DP is a good result. "
                "Drill: three statements, you name the pattern and the words that point to "
                "it.\n\n"
                '**#1:** "For each bar in a bar chart, find how far it can stretch left and '
                'right before meeting a shorter bar." Pattern and the words?',
                intent="APPROACH_DISCUSSION",
                route="clarify",
                plan={"assistance_level": "concept"},
                pending_check=Q("monotonic_stack"),
                memory_used=["heldout_08: interview tomorrow"],
            ),
            U("two pointers going outward from each bar?"),
            A(
                'No, and this is the kind from your interview. The words are **"until a '
                'shorter bar"**: for each element, the nearest smaller element on each side. '
                "Walking outward from every bar re-reads the same bars, O(n²).\n\n"
                "Instead keep a stack of bars that have not met a shorter bar yet. When a "
                "shorter bar arrives it ends every taller bar on the stack. That is a "
                "**monotonic stack**. Keep the phrase: **nearest smaller / next greater → "
                "monotonic stack.**\n\n"
                '**#2:** "Find the longest stretch of days on which at most two different '
                'products were sold." Pattern?',
                grade="incorrect",
                evidence=[concept("monotonic_stack", "incorrect")],
                pending_check=Q("sliding_window"),
            ),
            U("a window I think... but I'd recount the products in it every time it moves"),
            A(
                "The window is right. Recounting is the part to drop: keep a count per product "
                "and update it as the edges move, as you did with the fruit baskets.\n\n"
                '**#3:** "Tasks have prerequisites. Give the fewest rounds in which all of them '
                'can be finished." Pattern?',
                grade="partial",
                evidence=[concept("sliding_window", "partial")],
                pending_check=Q("topological_sort"),
            ),
            U("topological sort by layers, kahn's. 'prerequisites' plus 'fewest rounds'"),
            A(
                "Immediate, with the right words.\n\n"
                "Score: 1 correct, 1 partial, 1 miss. The miss was a pattern you **had not "
                "been taught**, which is different from one you learned and lost. I've put "
                "monotonic stack on your focus list. Next session: Largest Rectangle in "
                "Histogram, the question from your interview.",
                grade="correct",
                evidence=[concept("topological_sort", "correct")],
                profile_update={"learning_preferences": {"focus": "monotonic_stack"}},
            ),
        ],
        "expected_outcome": (
            "The agent follows up on the interview, runs a drill without revealing patterns, "
            "grades each answer, teaches only the missing pattern, and sets the next session "
            "from the evidence."
        ),
    }
)

# ---------------------------------------------------------------- HX10 interview mode
EXAMPLES.append(
    {
        "conversation_id": "heldout_10_interview_mode",
        "session": 9,
        "day": 30,
        "title": "Focused level: the learner leads, the agent interviews, then an honest review",
        "intent": "DSA_SOLVE",
        "teaching_mode": "Challenge",
        "problem": "Largest Rectangle in Histogram",
        "agent_behavior": [
            "learner_leads_agent_probes",
            "ask_interviewer_questions_only",
            "no_hints_unless_asked",
            "run_learner_code_in_sandbox",
            "evidence_based_progress_summary",
            "name_remaining_weak_areas_honestly",
            "propose_next_focus_and_ask",
        ],
        "conversation": [
            U(
                "largest rectangle in histogram. run this as a mock interview, I'll talk through my approach first"
            ),
            A(
                "Mock interview. I'll ask only what an interviewer would. Go ahead.",
                intent="DSA_SOLVE",
                route="dsa",
                plan={"difficulty": "hard", "assistance_level": "hint", "hint_rung": 0},
            ),
            U(
                "keep a stack of (start index, height) with increasing heights. for each new "
                "bar, pop every bar that is taller or equal: a popped bar's rectangle ends "
                "here, width = current index - its start. the new bar inherits the start of "
                "the last one popped. append a 0 at the end to flush the stack. O(n), each bar "
                "pushed and popped once"
            ),
            A(
                "Clear. Two follow-ups: why does the new bar take the start index of the last "
                "bar popped? And what is your space?",
                grade="correct",
                evidence=[concept("monotonic_stack", "correct")],
                pending_check=Q("monotonic_stack"),
            ),
            U(
                "every bar I popped was at least as tall as the new one, so the new bar's "
                "height fits across all of them, back to that start. space O(n) for the stack "
                "in the worst case, an increasing histogram"
            ),
            A(
                "Both right. Code it.",
                grade="correct",
                pending_check=SUBMIT("monotonic_stack"),
            ),
            U(code_of("hx10", "learner_v1")),
            A(
                "**5/5 pass**, including four equal bars and the empty histogram.\n\n"
                "Stepping out of the interview. A month ago you told me you *stare at mediums "
                "and never know what technique they want*. Today you solved a hard one by "
                "stating the invariant, defending it, and giving the complexity before writing "
                "a line.\n\n"
                "What the record of our sessions says:\n"
                "- **Strong now:** sliding window and topological sort.\n"
                "- **Getting solid:** trees (you catch the returnable-vs-global trap alone) "
                "and binary search on the answer. One more clean solve each and they are "
                "strong.\n"
                "- **Still thin:** DP, where you have one guided night and no solve of your "
                "own. Monotonic stack has a single solve, today's. BFS we have not touched "
                "since the first day.\n\n"
                "For next week I'd pick DP: three problems from an empty file, starting easy, "
                "with a house-robber rewrite first. Does that suit you?",
                sandbox=sbx("hx10", "learner_v1"),
                evidence=[ran("monotonic_stack", True)],
                memory_used=[
                    "heldout_01 self_report",
                    "skills.*",
                    "common_errors.trees.returnable_vs_global_path=resolved",
                    "heldout_08 dp exposure only",
                ],
            ),
            U("yes. it really doesn't feel like it did a month ago"),
            A(
                "It isn't the same: the reasoning is yours now, and I only check it. See you "
                "next session.",
            ),
        ],
        "expected_outcome": (
            "At focused level the learner leads and the agent only probes like an interviewer. "
            "The closing review rests on recorded evidence, is honest about weak areas (DP), and "
            "proposes the next focus."
        ),
    }
)


# ================================== profile simulation ==================================
def apply(skills: dict[str, float], ev: dict[str, Any]) -> None:
    t = ev["topic"]
    v = skills.setdefault(t, PRIOR)
    prov = ev["provenance"]
    if prov == "sandbox" and ev.get("solved") is not None:
        skills[t] = round(v + ALPHA * ((1.0 if ev["solved"] else 0.0) - v), 4)
    elif prov == "concept_check":
        nv = v + CONCEPT_ALPHA * (GRADE_VALUE[ev["grade"]] - v)
        if nv > v:  # conceptual evidence alone cannot push a skill past HARD
            nv = min(nv, max(v, HARD))
        skills[t] = round(nv, 4)
    # exposure: the key exists at PRIOR, nothing moves


def label(v: float) -> str:
    return "weak" if v < WEAK else "strong" if v >= HARD else "developing"


def eval_checks(rec: dict[str, Any]) -> list[str]:
    checks = [
        "every assistant turn that has meta.pending_check asks exactly one question",
        "no assistant turn reveals full solution code unless meta.plan.assistance_level == 'full'",
        "every 'N/M pass' claim equals meta.sandbox.passed/meta.sandbox.total",
        "solved is set only from sandbox evidence on the learner's own code",
        "skill keys come only from the closed topic vocabulary",
        "assistant never claims code works without a sandbox result in the same turn",
    ]
    beh = set(rec["agent_behavior"])
    if "learner_writes_the_fix" in beh:
        checks.append(
            "assistant turns before the learner's fixed submission contain no corrected code"
        )
    if any("memory_used" in t.get("meta", {}) for t in rec["conversation"]):
        checks.append(
            "facts listed in meta.memory_used exist in profile_before or earlier sessions"
        )
    if rec["teaching_mode"] == "Challenge":
        checks.append("assistant gives no hints the learner did not ask for")
    if rec["teaching_mode"] == "Guidance" and "honest_no_skill_credit" in beh:
        checks.append(
            "revealed solution sets needed_full_solution=true and solved=None; skill unchanged"
        )
    return checks


def build() -> list[dict[str, Any]]:
    skills: dict[str, float] = {}
    errors: dict[str, Any] = {}
    prefs: dict[str, Any] = {}
    out: list[dict[str, Any]] = []
    for ex in EXAMPLES:
        rec = deepcopy(ex)
        rec["profile_before"] = {
            "skills": dict(skills),
            "common_errors": dict(errors),
            "learning_preferences": dict(prefs),
        }
        for turn in rec["conversation"]:
            meta = turn.get("meta", {})
            for ev in meta.get("evidence", []):
                apply(skills, ev)
            pu = meta.get("profile_update", {})
            errors.update(pu.get("common_errors", {}))
            prefs.update(pu.get("learning_preferences", {}))
        before = rec["profile_before"]["skills"]
        rec["profile_after"] = {
            "skills": dict(skills),
            "skill_labels": {k: label(v) for k, v in skills.items()},
            "skill_deltas": {
                k: round(v - before.get(k, PRIOR), 4)
                for k, v in skills.items()
                if round(v - before.get(k, PRIOR), 4) != 0
            },
            "common_errors": dict(errors),
            "learning_preferences": dict(prefs),
        }
        rec["pattern_families"] = sorted({FAMILY[k] for k in rec["profile_after"]["skill_deltas"]})
        rec["learner_name_placeholder"] = "{learner_name}"
        rec["eval_checks"] = eval_checks(rec)
        out.append(rec)
    return out


# ================================== validation ==================================
def validate(recs: list[dict[str, Any]]) -> None:
    for rec in recs:
        cid = rec["conversation_id"]
        roles = [t["role"] for t in rec["conversation"]]
        assert roles[0] == "user", cid
        assert all(a != b for a, b in zip(roles, roles[1:], strict=False)), f"{cid}: roles"
        for i, t in enumerate(rec["conversation"]):
            if t["role"] != "assistant":
                continue
            m = t["meta"]
            txt = t["content"]
            if "sandbox" in m:
                s = m["sandbox"]
                g = GROUND[s["ref"]]
                assert (s["passed"], s["total"]) == (g["passed"], g["total"]), cid
                assert f"{s['passed']}/{s['total']} pass" in txt, f"{cid} turn {i}: sandbox text"
                ff = g["first_failing"]
                if ff:
                    assert str(ff["got"]) in txt, f"{cid} turn {i}: failing output not quoted"
                    assert str(ff["expected"]) in txt, f"{cid} turn {i}: expected not quoted"
            else:
                assert not re.search(r"\d+/\d+ pass", txt), f"{cid} turn {i}: unbacked pass claim"
            if "```python" in txt:
                level = m.get("plan", {}).get("assistance_level")
                assert level == "full", f"{cid} turn {i}: code reveal"
            if m.get("pending_check", {}).get("kind") == "question":
                assert txt.count("?") >= 1, f"{cid} turn {i}: pending check without a question"
        for ev in [e for t in rec["conversation"] for e in t.get("meta", {}).get("evidence", [])]:
            assert ev["topic"] in FAMILY, f"{cid}: topic {ev['topic']} outside vocabulary"
            if ev["provenance"] != "sandbox":
                assert ev.get("solved") is None, f"{cid}: non-sandbox evidence set solved"
    turns = sum(len(r["conversation"]) for r in recs)
    print(f"validated {len(recs)} conversations, {turns} turns")


if __name__ == "__main__":
    records = build()
    validate(records)
    with (HERE / "heldout_journey_10.jsonl").open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    for r in records:
        print(r["conversation_id"], r["profile_after"]["skill_labels"])
