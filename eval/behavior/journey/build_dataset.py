"""Builds adaptive_journey_10.jsonl: one learner's 10-session journey from
'confused intermediate' to 'focused problem-solver'.

- Every code block a learner submits is taken verbatim from verify_examples.py.
- Every sandbox claim in an agent turn is taken from sandbox_ground_truth.json
  (real execution), and the validator checks the text matches it.
- Skill values are computed with the project's calibration, not hand-written.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

from verify_examples import SNIPPETS, TREE_HELPERS

GROUND = json.load(open("sandbox_ground_truth.json"))

# ---------------- calibration (matches docs/features/ADAPTIVE-loop.md) -------------
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


def sbx(ex: str, label: str) -> dict[str, Any]:
    g = GROUND[f"{ex}:{label}"]
    return {
        "ref": f"{ex}:{label}",
        "ran": True,
        "passed": g["passed"],
        "total": g["total"],
        "first_failing": g["first_failing"],
        "provenance": "extracted",
    }


def U(text: str) -> dict[str, Any]:
    return {"role": "user", "content": text}


def A(text: str, **meta: Any) -> dict[str, Any]:
    return {"role": "assistant", "content": text, "meta": meta}


# ======================================================================================
# The journey. Learner: final-year student, Python, ~80 easy problems, placements in
# ~10 weeks, freezes on mediums. {learner_name} is a placeholder, never a real name.
# ======================================================================================
EXAMPLES: list[dict[str, Any]] = []

# ---------------------------------------------------------------- EX01 onboarding
EXAMPLES.append(
    {
        "conversation_id": "journey_01_diagnose",
        "session": 1,
        "day": 1,
        "title": "First session: diagnose the real gap (recognition, not knowledge)",
        "intent": "APPROACH_DISCUSSION",
        "teaching_mode": "Balanced",
        "learner_state": {
            "stage": "confused_intermediate",
            "confidence": "low",
            "self_report": "solved ~80 easy problems, freezes on mediums, can't pick an approach",
        },
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
                "hey. placements start in like 10 weeks. I've done around 80 easy leetcode problems but I freeze on mediums. I don't even know which approach to pick. kinda stressed honestly"
            ),
            A(
                "Ten weeks is enough to fix exactly this. And \"I freeze because I don't know which approach to pick\" usually isn't a knowledge problem, it's a recognition problem: you know the tools, but the problem statement doesn't tell you which one to grab.\n\nLet's check that instead of guessing. Three one-line problems, you just tell me which approach you'd reach for. No code.\n\n**#1:** \"Given a string, find the length of the longest substring with no repeating characters.\" Which approach?",
                intent="APPROACH_DISCUSSION",
                route="clarify",
                plan={"difficulty": "medium", "assistance_level": "concept"},
                pending_check={
                    "kind": "question",
                    "topic": "sliding_window",
                    "expected_concepts": ["contiguous", "sliding window"],
                },
            ),
            U("hashmap? to store the characters"),
            A(
                "Half right. A hash map is part of it, but it's the helper, not the strategy.\n\nThe giveaway is the word **substring**: it means *contiguous*. When a problem asks for the longest or shortest contiguous piece that satisfies a condition, the strategy is usually a **sliding window**, and the hash map just tracks what's inside the window.\n\n**#2:** \"Given a sorted array, find two numbers that add up to a target.\" Which approach?",
                grade="partial",
                evidence=[
                    {"topic": "sliding_window", "provenance": "concept_check", "grade": "partial"}
                ],
                pending_check={"kind": "question", "topic": "two_pointers"},
            ),
            U("two pointers, one at the start one at the end"),
            A(
                'Exactly, and for the right reason: because it\'s **sorted**, moving one end inward tells you which way the sum changes.\n\n**#3:** "Find the shortest path from the top-left to the bottom-right of a grid. You can move up, down, left, right." Which approach?',
                grade="correct",
                evidence=[
                    {"topic": "two_pointers", "provenance": "concept_check", "grade": "correct"}
                ],
                pending_check={"kind": "question", "topic": "bfs"},
            ),
            U("dfs?"),
            A(
                'That\'s the most common pick, and the one to unlearn. DFS finds *a* path, not necessarily the shortest. In an unweighted grid, **BFS** explores cells in order of distance, so the first time it reaches the goal, that path is the shortest. Signal: **"shortest" + unweighted → BFS.**\n\nHere\'s what those three answers tell me:\n- Your tools are fine. Two pointers is solid; you know hash maps.\n- The gap is **mapping problem wording to a pattern**: "contiguous" → window, "shortest in unweighted" → BFS.\n\nThat\'s a very fixable gap. My plan: one pattern at a time, and before any code you\'ll name the pattern **and the word that gave it away**. Want to start with sliding window right now?',
                grade="incorrect",
                misconception="bfs.dfs_for_unweighted_shortest_path",
                evidence=[{"topic": "bfs", "provenance": "concept_check", "grade": "incorrect"}],
            ),
            U("yes lets do it. and pls don't just give me answers, I want to actually get it"),
            A(
                "Deal. When you're stuck I'll ask you a nudge question first and only get more direct if you're still stuck. Sliding window, next message.",
                profile_update={
                    "learning_preferences": {
                        "prefers_hints": True,
                        "hint_style": "nudge_question_first",
                    }
                },
            ),
        ],
        "expected_outcome": "The agent finds the real gap (recognition) with three graded micro-questions, explains each miss in one short paragraph, and gets consent for a plan. No code, no lecture.",
    }
)

# ---------------------------------------------------------------- EX02 first window
EXAMPLES.append(
    {
        "conversation_id": "journey_02_first_window",
        "session": 1,
        "day": 1,
        "title": "Sliding window with a hint ladder; learner finds his own bug from a sandbox trace",
        "intent": "DSA_SOLVE",
        "teaching_mode": "Balanced",
        "problem": "Longest Substring Without Repeating Characters",
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
            U("ok give me the sliding window problem"),
            A(
                '**Longest Substring Without Repeating Characters.** Given a string `s`, return the length of the longest substring without repeating characters. `"abcabcbb"` → 3 (`"abc"`).\n\nBefore anything else: which pattern, and which word gave it away?',
                intent="DSA_SOLVE",
                route="dsa",
                plan={"difficulty": "medium", "assistance_level": "hint", "hint_rung": 0},
                pending_check={"kind": "question", "topic": "sliding_window"},
            ),
            U("sliding window, because substring means contiguous. and longest"),
            A(
                "Both signals, exactly what we practised. Next question: what must be true about the window at every moment?",
                grade="correct",
                plan={"assistance_level": "hint", "hint_rung": 1},
                evidence=[
                    {"topic": "sliding_window", "provenance": "concept_check", "grade": "correct"}
                ],
            ),
            U("no repeating characters inside it"),
            A(
                "Right, that's your invariant. So when the new character on the right is already inside the window, what do you do with the left edge?",
                grade="correct",
                plan={"assistance_level": "hint", "hint_rung": 2},
            ),
            U("move left to just after the old position of that character?"),
            A(
                "Yes, that's the whole idea. You have enough to write it. Try it and I'll run it.",
                grade="correct",
                plan={"assistance_level": "hint", "hint_rung": 2},
                pending_check={"kind": "code_submission", "topic": "sliding_window"},
            ),
            U("here, it passed abcabcbb:\n" + code_of("ex02", "learner_v1_buggy")),
            A(
                "Ran it on 5 cases: **4/5 pass**. The one that fails:\n\n`\"abba\"` → expected `2`, your code returns `3`.\n\nWalk through it with me. At i=2 (`'b'`), left jumps to 2. At i=3 (`'a'`), `last_seen['a']` is 0. What does your code set `left` to?",
                intent="CODE_DEBUG",
                route="dsa",
                sandbox=sbx("ex02", "learner_v1_buggy"),
                evidence=[{"topic": "sliding_window", "provenance": "sandbox", "solved": False}],
            ),
            U(
                "left = 0 + 1 = 1... oh. it moved backwards. that old 'a' isn't even in the window anymore"
            ),
            A(
                "Exactly. `last_seen` still remembers characters that already left the window, and your code trusts it. **The left edge must never move backwards.** One extra condition fixes it, and you're the one writing it.",
                grade="correct",
                misconception="sliding_window.left_pointer_moves_backward",
            ),
            U(code_of("ex02", "learner_v2_fixed")),
            A(
                '**6/6 pass**, including `"abba"` and `"tmmzuxt"`, which needs the same check. Time O(n), space O(alphabet).\n\nKeep this rule: a "last seen" map needs a *"is it still inside the window?"* check. I\'ll remember this one and check it on you later.',
                sandbox=sbx("ex02", "learner_v2_fixed"),
                evidence=[{"topic": "sliding_window", "provenance": "sandbox", "solved": True}],
                profile_update={"common_errors": {"sliding_window.left_pointer_moves_backward": 1}},
            ),
        ],
        "expected_outcome": "The learner names the pattern, derives the invariant through 3 hint rungs, and fixes his own bug after one trace question. The agent never writes the solution.",
    }
)

# ---------------------------------------------------------------- EX03 spaced recall
EXAMPLES.append(
    {
        "conversation_id": "journey_03_less_help",
        "session": 2,
        "day": 2,
        "title": "Returning learner: spaced recall of yesterday's mistake, then visibly less scaffolding",
        "intent": "DSA_SOLVE",
        "teaching_mode": "Balanced",
        "problem": "Max Consecutive Ones III",
        "agent_behavior": [
            "spaced_recall_of_recorded_misconception",
            "reduce_scaffolding_after_success",
            "let_learner_state_the_invariant",
            "run_learner_code_in_sandbox",
            "ask_complexity",
            "update_topic_status_from_evidence",
        ],
        "conversation": [
            U("hi, back. what's next"),
            A(
                'Welcome back. 10-second warm-up from yesterday: in `"abba"`, why did your left pointer go wrong?',
                intent="CONCEPT_EXPLANATION",
                route="clarify",
                memory_used=["common_errors.sliding_window.left_pointer_moves_backward"],
                pending_check={"kind": "question", "topic": "sliding_window"},
            ),
            U(
                "it jumped back to an old position of a char that wasn't in the window anymore. need last_seen >= left"
            ),
            A(
                "Locked in. Today is a variable window with a twist, and I'll give you less help, because you're ready for it.\n\n**Max Consecutive Ones III.** Binary array `nums` and integer `k`: return the max number of consecutive 1s if you may flip at most `k` zeros.\n\nWhat's the pattern, and which words gave it away?",
                grade="correct",
                route="dsa",
                plan={"difficulty": "medium", "assistance_level": "hint", "hint_rung": 0},
                evidence=[
                    {"topic": "sliding_window", "provenance": "concept_check", "grade": "correct"}
                ],
                pending_check={"kind": "question", "topic": "sliding_window"},
            ),
            U(
                "'consecutive' means window. 'at most k' zeros means the window is valid while zeros <= k, shrink from the left when zeros > k"
            ),
            A(
                "That's the whole solution in one sentence, and you named the invariant yourself. Write it.",
                grade="correct",
                pending_check={"kind": "code_submission", "topic": "sliding_window"},
            ),
            U(code_of("ex03", "learner_v1")),
            A(
                "**5/5 pass**, first try, including `[0,0,0]` with `k=0` → 0 and the all-ones case. Complexity?",
                sandbox=sbx("ex03", "learner_v1"),
                evidence=[{"topic": "sliding_window", "provenance": "sandbox", "solved": True}],
                pending_check={"kind": "question", "topic": "sliding_window"},
            ),
            U("O(n), every element enters and leaves the window at most once"),
            A(
                'Exactly the right reason. Two window problems in two days, and today you needed **zero hints**. I\'m moving sliding window from "learning" to "review now and then". Next session we start a new pattern.',
                grade="correct",
            ),
        ],
        "expected_outcome": "The agent uses the stored misconception for a 1-question recall, then gives noticeably less help than yesterday because the evidence says the learner is ready.",
    }
)

# ---------------------------------------------------------------- EX04 frustration
EXAMPLES.append(
    {
        "conversation_id": "journey_04_bad_day",
        "session": 3,
        "day": 6,
        "title": "Bad mock test: respond like a friend with real evidence, then turn the failure into a win",
        "intent": "DSA_SOLVE",
        "teaching_mode": "Balanced",
        "problem": "Koko Eating Bananas",
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
                "bad day. had a mock test in college and couldn't even start one question, something about eating bananas at speed k. everyone else solved it. maybe I'm just not good enough for product companies"
            ),
            A(
                "That stings, especially when it feels like everyone else got it.\n\nBut let me push back on \"not good enough\" with what actually happened in our sessions. Five days ago you couldn't name the pattern for a longest-substring problem. Four days ago you named the pattern *and* its invariant in one sentence and passed every test with zero hints. That's not someone who can't do this. That's someone who hasn't met this pattern yet.\n\nThe banana problem is a classic with one specific trick most people only see once. Want to make it the one we crack today? If you'd rather rest tonight, that's fine too. It'll keep.",
                intent="APPROACH_DISCUSSION",
                route="clarify",
                memory_used=[
                    "skills.sliding_window trend (fail → pass → pass)",
                    "journey_03: zero-hint solve",
                ],
            ),
            U("no let's do it. I want to understand it"),
            A(
                'Good. Here\'s the reframe that unlocks it: don\'t ask "what is the answer?" Ask: **"If I guessed a speed k, could I check whether it works?"** Could you?',
                intent="DSA_SOLVE",
                route="dsa",
                plan={"difficulty": "medium", "assistance_level": "concept", "hint_rung": 0},
                pending_check={"kind": "question", "topic": "binary_search_on_answer"},
            ),
            U("yes. hours = sum of each pile divided by k, check if <= h"),
            A(
                "Right. Now: if speed 4 works, does speed 5 also work?",
                grade="correct",
                plan={"assistance_level": "concept", "hint_rung": 1},
            ),
            U("yes, faster is always fine"),
            A(
                "That's the key insight. Feasibility is **monotonic**: once a speed works, every faster speed works. So the speeds look like `✗ ✗ ✗ ✓ ✓ ✓` and you want the first `✓`. What finds the first true in a sorted yes/no sequence?",
                grade="correct",
                plan={"assistance_level": "concept", "hint_rung": 2},
            ),
            U("binary search!! on the speed, not on the array"),
            A(
                'Exactly: **binary search on the answer**. Signal to remember: *"minimum value such that something is possible"* + checking a guess is easy. Write it.',
                grade="correct",
                evidence=[
                    {
                        "topic": "binary_search_on_answer",
                        "provenance": "concept_check",
                        "grade": "correct",
                    }
                ],
                pending_check={"kind": "code_submission", "topic": "binary_search_on_answer"},
            ),
            U(code_of("ex04", "learner_v1_buggy")),
            A(
                "The binary search is right. **1/4 pass** though: `piles=[3,6,7,11], h=8` → expected `4`, you return `3`.\n\nAt speed 3, how many hours does the pile of 7 take? Your code computes `p // mid`.",
                intent="CODE_DEBUG",
                sandbox=sbx("ex04", "learner_v1_buggy"),
                evidence=[
                    {"topic": "binary_search_on_answer", "provenance": "sandbox", "solved": False}
                ],
            ),
            U(
                "7 // 3 = 2 but it really takes 3 hours, she can't finish part of a pile in an hour and move on... need ceil"
            ),
            A(
                "Yep. Floor undercounts every pile that doesn't divide evenly, so slow speeds look fast enough. Ceil without floats: `(p + k - 1) // k`.",
                grade="correct",
                misconception="binary_search_on_answer.floor_instead_of_ceil",
            ),
            U(code_of("ex04", "learner_v2_fixed")),
            A(
                "**5/5 pass**, including a single pile of 1,000,000,000.\n\nThat's the problem you couldn't start this morning, solved by you, in one sitting. Next time you see *\"minimum/maximum value such that…\"*, ask the guess-and-check question first. Now go rest, you've earned it.",
                sandbox=sbx("ex04", "learner_v2_fixed"),
                evidence=[
                    {"topic": "binary_search_on_answer", "provenance": "sandbox", "solved": True}
                ],
                profile_update={
                    "common_errors": {"binary_search_on_answer.floor_instead_of_ceil": 1}
                },
            ),
        ],
        "expected_outcome": "The agent responds like a friend using real evidence (not empty praise), offers rest, then teaches binary-search-on-answer through 3 guiding questions. It points at the buggy line with a question instead of rewriting it.",
    }
)

# ---------------------------------------------------------------- EX05 misconception
EXAMPLES.append(
    {
        "conversation_id": "journey_05_two_jobs",
        "session": 4,
        "day": 9,
        "title": "Tree misconception: one return value doing two jobs",
        "intent": "CODE_DEBUG",
        "teaching_mode": "Balanced",
        "problem": "Diameter of Binary Tree",
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
                "diameter of binary tree. getting 5 for [1,2,3,4,5] but the answer is 3. I return left+right+1 which should be the longest path through each node right?\n"
                + code_of("ex05", "learner_v1_buggy")
            ),
            A(
                "Ran it on 4 trees: **0/4 pass**. Your instinct that the longest path through a node = left + right is correct. The problem is what the function *returns*.\n\nClue: it returns `5` for `[1,2,3,4,5]`, and that tree has exactly 5 nodes. What is your function actually counting?",
                intent="CODE_DEBUG",
                route="debug",
                plan={"difficulty": "easy", "assistance_level": "concept"},
                sandbox=sbx("ex05", "learner_v1_buggy"),
                evidence=[{"topic": "trees", "provenance": "sandbox", "solved": False}],
                pending_check={"kind": "question", "topic": "trees"},
            ),
            U("...all the nodes? because left and right are also returning totals, not one side"),
            A(
                "Exactly. Your function is doing **two jobs with one return value**:\n1. What the parent needs from me: my **height**, one side only, because a path can't branch on the way up.\n2. The best path *through* me: left height + right height.\n\nThose are different numbers. Which one goes back to the parent, and which one gets stored in a variable outside?",
                grade="correct",
                misconception="trees.returnable_vs_global_path",
                pending_check={"kind": "question", "topic": "trees"},
            ),
            U("return the height, keep left+right in a max variable outside"),
            A(
                "That's it. Write it.",
                grade="correct",
                evidence=[{"topic": "trees", "provenance": "concept_check", "grade": "correct"}],
                pending_check={"kind": "code_submission", "topic": "trees"},
            ),
            U(code_of("ex05", "learner_v2_fixed")),
            A(
                "**4/4 pass**, including an unbalanced tree whose diameter (5) doesn't go through the root.\n\nName for this trap: **returnable value vs. global answer**. It shows up in a lot of tree problems. I've noted it, and I'll flag it when it comes back.",
                sandbox=sbx("ex05", "learner_v2_fixed"),
                evidence=[{"topic": "trees", "provenance": "sandbox", "solved": True}],
                profile_update={"common_errors": {"trees.returnable_vs_global_path": 1}},
            ),
        ],
        "expected_outcome": "The agent uses the sandbox output (5 = node count) as a clue, repairs the mental model with two questions, and records a named misconception instead of just pasting fixed code.",
    }
)

# ---------------------------------------------------------------- EX06 transfer
EXAMPLES.append(
    {
        "conversation_id": "journey_06_spots_the_trap",
        "session": 5,
        "day": 16,
        "title": "Harder sibling problem: the learner spots the recorded trap himself",
        "intent": "DSA_SOLVE",
        "teaching_mode": "Balanced",
        "problem": "Binary Tree Maximum Path Sum",
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
            U("give me another tree problem, something harder"),
            A(
                "You just beat diameter, so here's its harder sibling: **Binary Tree Maximum Path Sum**. A path is any chain of connected nodes (it doesn't have to pass through the root), and node values **can be negative**. Return the max path sum. `[-10,9,20,null,null,15,7]` → 42.\n\nIt has the same trap you met last week. Before coding: what does your recursion return to the parent, and what do you track globally?",
                intent="DSA_SOLVE",
                route="practice",
                plan={"difficulty": "hard", "assistance_level": "hint", "hint_rung": 0},
                memory_used=["common_errors.trees.returnable_vs_global_path", "skills.trees"],
                pending_check={"kind": "question", "topic": "trees"},
            ),
            U("return node + the better of the two sides. global = node + left + right"),
            A(
                "You spotted the two jobs yourself this time, with no hint. Go write it.",
                grade="correct",
                evidence=[{"topic": "trees", "provenance": "concept_check", "grade": "correct"}],
                pending_check={"kind": "code_submission", "topic": "trees"},
            ),
            U("passes both examples on the problem page:\n" + code_of("ex06", "learner_v1_buggy")),
            A(
                "**3/4 pass.** It passes both page examples and fails a tiny one: `[2,-1]` → expected `2`, you return `1`.\n\nIn `[2,-1]`, is it ever worth taking the `-1` child?",
                intent="CODE_DEBUG",
                sandbox=sbx("ex06", "learner_v1_buggy"),
                evidence=[{"topic": "trees", "provenance": "sandbox", "solved": False}],
            ),
            U("no. if a side is negative just don't take it. max(gain, 0)"),
            A("Exactly. A path is allowed to stop. Clamp each child's gain at 0.", grade="correct"),
            U(code_of("ex06", "learner_v2_fixed")),
            A(
                "**5/5 pass**, including all-negative `[-2,-1]` → `-1`.\n\nThat case is why `best` must start at `-inf`, not `0`, and you got that right without being told. The returnable-vs-global trap is now one you catch on your own, so I'm marking it resolved.",
                sandbox=sbx("ex06", "learner_v2_fixed"),
                evidence=[{"topic": "trees", "provenance": "sandbox", "solved": True}],
                profile_update={"common_errors": {"trees.returnable_vs_global_path": "resolved"}},
            ),
        ],
        "expected_outcome": "The agent connects the new problem to the recorded misconception without solving it. The learner names the trap unprompted, and the sandbox catches an edge case the page examples miss.",
    }
)

# ---------------------------------------------------------------- EX07 review
EXAMPLES.append(
    {
        "conversation_id": "journey_07_why_it_works",
        "session": 6,
        "day": 20,
        "title": "Working code, missing 'why': review that builds interview-ready understanding",
        "intent": "CODE_REVIEW",
        "teaching_mode": "Balanced",
        "problem": "Course Schedule",
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
                "course schedule: numCourses and prerequisites pairs [a, b] meaning you must take b before a, return true if you can finish all courses. I wrote this, it passes on leetcode but idk if this is a BFS or DFS problem or WHY it works\n"
                + code_of("ex07", "learner_v1")
            ),
            A(
                "First: it's correct. I ran it on 5 cases, including a 3-course cycle: **5/5 pass**.\n\nNow the *why*, because that's what an interviewer will ask. Which words in the problem told you it's a graph problem?",
                intent="CODE_REVIEW",
                route="explain",
                plan={"difficulty": "medium", "assistance_level": "concept"},
                sandbox=sbx("ex07", "learner_v1"),
                evidence=[{"topic": "topological_sort", "provenance": "sandbox", "solved": True}],
                pending_check={"kind": "question", "topic": "topological_sort"},
            ),
            U("prerequisites? one course depends on another"),
            A(
                "Yes. *Prerequisites / depends on / order* → **directed graph**. *Can you finish all of them?* → **does the graph have a cycle?**\n\nYour code is **Kahn's algorithm** (topological sort): it keeps taking courses that have no remaining prerequisites. If there's a cycle, what happens to the courses inside it?",
                grade="correct",
                pending_check={"kind": "question", "topic": "topological_sort"},
            ),
            U(
                "their indegree never hits 0 so they never get taken. so taken ends up < num_courses"
            ),
            A(
                "Exactly, that's why `taken == num_courses` is your cycle check. So, to your question: it's BFS-style. DFS with three colours also works, but Kahn's is easier to get right under pressure.\n\nOne review note: `queue.pop(0)` is O(n) on a Python list, so long chains can make the loop O(V²). `collections.deque` with `popleft()` is O(1). Same logic, one import. With that, time and space are O(V + E).",
                grade="correct",
                evidence=[
                    {"topic": "topological_sort", "provenance": "concept_check", "grade": "correct"}
                ],
            ),
        ],
        "expected_outcome": "The agent verifies before reviewing, turns 'it works but I don't know why' into a 2-question explanation, gives one concrete performance note, and answers the BFS-vs-DFS question directly.",
    }
)

# ---------------------------------------------------------------- EX08 full solution
EXAMPLES.append(
    {
        "conversation_id": "journey_08_night_before",
        "session": 7,
        "day": 24,
        "title": "Night before a test: respect the explicit ask, give a verified solution, don't fake progress",
        "intent": "DSA_SOLVE",
        "teaching_mode": "Guidance",
        "problem": "Coin Change",
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
                "test is tomorrow morning and I haven't touched DP. just give me the coin change code pls, no time for hints tonight"
            ),
            A(
                "Fair. Tonight is about being ready, not about an exercise. Here's the verified solution plus the one idea behind it, so you can rebuild it if they change the problem.\n\n**Idea:** `dp[a]` = fewest coins to make amount `a`. Any amount is one coin plus a smaller amount you've already solved: `dp[a] = min(dp[a - c] + 1)` over coins `c`.\n\n"
                + code_of("ex08", "agent_reference")
                + "\n\nRan it: **5/5 pass**, including impossible (`[2]`, 3 → -1) and amount 0 → 0. Time O(amount × coins), space O(amount).\n\n**Recognition signal:** *fewest / number of ways* + items can be reused + an answer for a big amount builds on smaller amounts → **1-D DP**.",
                intent="DSA_SOLVE",
                route="dsa",
                plan={"difficulty": "medium", "assistance_level": "full"},
                escalation={
                    "reason": "explicit_ask",
                    "teaching_mode": "Guidance",
                    "verified_reference": True,
                },
                sandbox=dict(sbx("ex08", "agent_reference"), provenance="synthesised"),
                evidence=[
                    {
                        "topic": "dp_1d",
                        "provenance": "exposure",
                        "needed_full_solution": True,
                        "solved": None,
                    }
                ],
            ),
            U("thanks, this makes more sense than I expected"),
            A(
                "Good. To be straight with you: I'm not counting this as \"you solved DP\". You haven't yet, and that's fine. After your test, try rewriting it from a blank page. If you can, that's the real progress.\n\nSleep matters more than one more problem tonight. Good luck tomorrow, and tell me how it went.",
                memory_used=["skills.dp_1d = PRIOR (no prior evidence)"],
            ),
        ],
        "expected_outcome": "Under time pressure and an explicit ask (Guidance mode), the agent gives a sandbox-verified solution with one idea and a recognition signal, records exposure only (no skill credit), and puts the learner's rest first.",
    }
)

# ---------------------------------------------------------------- EX09 drill
EXAMPLES.append(
    {
        "conversation_id": "journey_09_recognition_drill",
        "session": 8,
        "day": 26,
        "title": "Mixed recognition drill: follow up on the test, grade each answer, target the real gap",
        "intent": "APPROACH_DISCUSSION",
        "teaching_mode": "Balanced",
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
                "test went ok! 2 out of 3. the one I missed was some 'next warmer day' thing. can you drill me on recognizing patterns? don't tell me the pattern"
            ),
            A(
                '2/3 the morning after your first DP night is solid. Drill time: three statements, you give the pattern and the giveaway words.\n\n**#1:** "For each day, return how many days you\'d wait until a warmer temperature." Pattern and giveaway words?',
                intent="APPROACH_DISCUSSION",
                route="clarify",
                plan={"assistance_level": "concept"},
                memory_used=["journey_08: test tomorrow"],
                pending_check={"kind": "question", "topic": "monotonic_stack"},
            ),
            U("two pointers? compare each day with the later days"),
            A(
                'Not quite, and this is the one from your test. The giveaway is **"next warmer / next greater"**: for each element, find the next element to the right that beats it. Comparing with later days re-scans, which is O(n²).\n\nThe trick: keep a stack of days still *waiting* for a warmer day. When a warmer day arrives, it answers everyone colder on the stack. That\'s a **monotonic stack**. Phrase to keep: **next greater → monotonic stack.**\n\n**#2:** "Given an array and k, return the maximum of every window of size k." Pattern?',
                grade="incorrect",
                evidence=[
                    {
                        "topic": "monotonic_stack",
                        "provenance": "concept_check",
                        "grade": "incorrect",
                    }
                ],
                pending_check={"kind": "question", "topic": "sliding_window"},
            ),
            U("sliding window... but I need the max quickly. a heap?"),
            A(
                "Sliding window is right, and a heap works in O(n log k). There's an O(n) version that uses the idea from #1: a deque of indices with decreasing values. We'll do that one properly next session. You're ready for it.\n\n**#3:** \"Given course prerequisites, return a valid order to take all the courses.\" Pattern?",
                grade="partial",
                evidence=[
                    {"topic": "sliding_window", "provenance": "concept_check", "grade": "partial"}
                ],
                pending_check={"kind": "question", "topic": "topological_sort"},
            ),
            U("topological sort, kahn's. 'prerequisites' and 'order'"),
            A(
                "Instant, with the right giveaway words.\n\nScore: 1 correct, 1 partial, 1 miss. The miss was a pattern you **hadn't learned yet**, not one you learned and forgot, and that's an important difference. I've added monotonic stack to your focus list. Next session: Sliding Window Maximum, which combines your strongest pattern with the new one.",
                grade="correct",
                evidence=[
                    {"topic": "topological_sort", "provenance": "concept_check", "grade": "correct"}
                ],
                profile_update={"focus": ["monotonic_stack"]},
            ),
        ],
        "expected_outcome": "The agent follows up on the test, runs a drill without revealing patterns, grades each answer, teaches only the missing pattern, and sets the next session from the evidence.",
    }
)

# ---------------------------------------------------------------- EX10 interview
EXAMPLES.append(
    {
        "conversation_id": "journey_10_interview_mode",
        "session": 9,
        "day": 30,
        "title": "Focused level: the learner leads, the agent acts as interviewer, then an honest progress review",
        "intent": "DSA_SOLVE",
        "teaching_mode": "Challenge",
        "problem": "Sliding Window Maximum",
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
            U("ok sliding window maximum. treat this like an interview. I'll explain first"),
            A(
                "Interview mode. I'll only ask what an interviewer would. Go.",
                intent="DSA_SOLVE",
                route="dsa",
                plan={"difficulty": "hard", "assistance_level": "hint", "hint_rung": 0},
            ),
            U(
                "keep a deque of indices whose values are decreasing. for each new x: pop from the back while the back's value <= x, push i. pop the front if it's out of the window. once i >= k-1 the front is the max. O(n) because each index is pushed and popped once"
            ),
            A(
                "Clear. Two follow-ups: why pop when `nums[back] <= x` and not only when `<`? And what's your space?",
                grade="correct",
                evidence=[
                    {"topic": "monotonic_stack", "provenance": "concept_check", "grade": "correct"}
                ],
                pending_check={"kind": "question", "topic": "monotonic_stack"},
            ),
            U(
                "if a newer element is >= the older one, the older can never be the max while the newer one is in the window, and it leaves first. so equal can go too. space O(k)"
            ),
            A(
                "Both correct. Code it.",
                grade="correct",
                pending_check={"kind": "code_submission", "topic": "monotonic_stack"},
            ),
            U(code_of("ex10", "learner_v1")),
            A(
                "**5/5 pass**, including all-duplicates `[4,4,4,4]` and `k=1`.\n\nStep out of interview mode for a second. A month ago you said *\"I freeze on mediums and don't know which approach to pick.\"* Today you solved a hard one by naming the pattern, defending the invariant, and giving the complexity before writing a line.\n\nWhat the evidence from our sessions says:\n- **Strong now:** sliding window and topological sort.\n- **Getting solid:** trees (you now catch the returnable-vs-global trap yourself) and binary search on answer. One more clean solve each and they're strong.\n- **Still thin:** DP, with one guided night and no solo solve yet. Monotonic stack has exactly one solve, today's. And BFS we haven't touched since day one.\n\nNext week I'd focus on DP: three problems from a blank page, starting easy, and a coin-change rewrite first. Sound good?",
                sandbox=sbx("ex10", "learner_v1"),
                evidence=[
                    {"topic": "monotonic_stack", "provenance": "sandbox", "solved": True},
                    {"topic": "sliding_window", "provenance": "sandbox", "solved": True},
                ],
                memory_used=[
                    "journey_01 self_report",
                    "skills.*",
                    "common_errors.trees.returnable_vs_global_path=resolved",
                    "journey_08 dp exposure only",
                ],
            ),
            U("yes. honestly this feels different from before"),
            A(
                "It is different: you're doing the thinking now, and I'm just checking it. See you next session."
            ),
        ],
        "expected_outcome": "At focused level the learner leads and the agent only probes like an interviewer. The closing review is grounded in recorded evidence, honest about weak areas (DP), and proposes the next focus.",
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
        o = GRADE_VALUE[ev["grade"]]
        nv = v + CONCEPT_ALPHA * (o - v)
        if nv > v:  # conceptual evidence alone cannot push a skill past HARD
            nv = min(nv, max(v, HARD))
        skills[t] = round(nv, 4)
    # exposure: key exists at PRIOR, nothing moves


def label(v: float) -> str:
    return "weak" if v < WEAK else "strong" if v >= HARD else "developing"


def build() -> list[dict[str, Any]]:
    skills: dict[str, float] = {}
    errors: dict[str, Any] = {}
    prefs: dict[str, Any] = {}
    out = []
    for ex in EXAMPLES:
        rec = deepcopy(ex)
        rec["profile_before"] = {
            "skills": dict(skills),
            "common_errors": dict(errors),
            "learning_preferences": dict(prefs),
        }
        n_events = 0
        for turn in rec["conversation"]:
            meta = turn.get("meta", {})
            for ev in meta.get("evidence", []):
                apply(skills, ev)
                n_events += 1
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


# ================================== validation ==================================
def validate(recs: list[dict[str, Any]]) -> None:
    code_re = re.compile(r"```python")
    for rec in recs:
        cid = rec["conversation_id"]
        roles = [t["role"] for t in rec["conversation"]]
        assert roles[0] == "user", cid
        assert all(a != b for a, b in zip(roles, roles[1:])), f"{cid}: roles must alternate"
        for i, t in enumerate(rec["conversation"]):
            if t["role"] != "assistant":
                continue
            m = t["meta"]
            txt = t["content"]
            if "sandbox" in m:
                s = m["sandbox"]
                g = GROUND[s["ref"]]
                assert (s["passed"], s["total"]) == (g["passed"], g["total"]), cid
                assert f"{s['passed']}/{s['total']} pass" in txt, (
                    f"{cid} turn {i}: sandbox text mismatch"
                )
                ff = g["first_failing"]
                if ff:
                    assert json.dumps(ff["got"]) in txt.replace(" ", "") or str(ff["got"]) in txt, (
                        f"{cid} turn {i}: failing output not quoted"
                    )
            else:
                assert not re.search(r"\d+/\d+ pass", txt), (
                    f"{cid} turn {i}: pass claim without sandbox"
                )
            if code_re.search(txt):
                assert m.get("plan", {}).get("assistance_level") == "full", (
                    f"{cid} turn {i}: code reveal"
                )
            if m.get("pending_check", {}).get("kind") == "question":
                assert txt.count("?") >= 1, f"{cid} turn {i}: pending check without a question"
        for ev in [e for t in rec["conversation"] for e in t.get("meta", {}).get("evidence", [])]:
            assert ev["topic"] in FAMILY, f"{cid}: topic {ev['topic']} outside vocabulary"
            if ev["provenance"] != "sandbox":
                assert ev.get("solved") is None, f"{cid}: non-sandbox evidence set solved"
    print(f"validated {len(recs)} conversations, {sum(len(r['conversation']) for r in recs)} turns")


if __name__ == "__main__":
    recs = build()
    validate(recs)
    with open("adaptive_journey_10.jsonl", "w", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    for r in recs:
        print(
            r["conversation_id"],
            {
                k: (v, r["profile_after"]["skill_labels"][k])
                for k, v in r["profile_after"]["skills"].items()
            },
        )
