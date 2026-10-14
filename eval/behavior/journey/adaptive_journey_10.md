# Adaptive Coding Agent: 10-session learner journey

One learner (`{learner_name}`), from *confused intermediate* to *focused problem-solver* over 30 days.
Every sandbox result was produced by actually running the code (`verify_examples.py`).
Skill values are computed with ALPHA 0.3 (sandbox), CONCEPT_ALPHA 0.1 (graded answers, capped at HARD 0.68), PRIOR 0.5.

---

## 1. First session: diagnose the real gap (recognition, not knowledge) (session 1)
`journey_01_diagnose` · day 1 · intent `APPROACH_DISCUSSION` · mode **Balanced**

**Behaviors:** `acknowledge_feeling_briefly_then_act`, `diagnose_before_teaching`, `one_question_at_a_time`, `grade_each_answer`, `name_the_signal_word`, `summarize_gap_from_evidence`, `propose_plan_and_ask_consent`, `record_learning_preference`

🧑 **Learner**

hey. placements start in like 10 weeks. I've done around 80 easy leetcode problems but I freeze on mediums. I don't even know which approach to pick. kinda stressed honestly

🤖 **Agent**

Ten weeks is enough to fix exactly this. And "I freeze because I don't know which approach to pick" usually isn't a knowledge problem, it's a recognition problem: you know the tools, but the problem statement doesn't tell you which one to grab.

Let's check that instead of guessing. Three one-line problems, you just tell me which approach you'd reach for. No code.

**#1:** "Given a string, find the length of the longest substring with no repeating characters." Which approach?

> plan=difficulty:medium, assistance_level:concept

🧑 **Learner**

hashmap? to store the characters

🤖 **Agent**

Half right. A hash map is part of it, but it's the helper, not the strategy.

The giveaway is the word **substring**: it means *contiguous*. When a problem asks for the longest or shortest contiguous piece that satisfies a condition, the strategy is usually a **sliding window**, and the hash map just tracks what's inside the window.

**#2:** "Given a sorted array, find two numbers that add up to a target." Which approach?

> grade=`partial`

🧑 **Learner**

two pointers, one at the start one at the end

🤖 **Agent**

Exactly, and for the right reason: because it's **sorted**, moving one end inward tells you which way the sum changes.

**#3:** "Find the shortest path from the top-left to the bottom-right of a grid. You can move up, down, left, right." Which approach?

> grade=`correct`

🧑 **Learner**

dfs?

🤖 **Agent**

That's the most common pick, and the one to unlearn. DFS finds *a* path, not necessarily the shortest. In an unweighted grid, **BFS** explores cells in order of distance, so the first time it reaches the goal, that path is the shortest. Signal: **"shortest" + unweighted → BFS.**

Here's what those three answers tell me:
- Your tools are fine. Two pointers is solid; you know hash maps.
- The gap is **mapping problem wording to a pattern**: "contiguous" → window, "shortest in unweighted" → BFS.

That's a very fixable gap. My plan: one pattern at a time, and before any code you'll name the pattern **and the word that gave it away**. Want to start with sliding window right now?

> grade=`incorrect` · misconception=`bfs.dfs_for_unweighted_shortest_path`

🧑 **Learner**

yes lets do it. and pls don't just give me answers, I want to actually get it

🤖 **Agent**

Deal. When you're stuck I'll ask you a nudge question first and only get more direct if you're still stuck. Sliding window, next message.

**Profile change:** `two_pointers` +0.050 → 0.55 (developing), `bfs` -0.050 → 0.45 (developing)

**Expected outcome:** The agent finds the real gap (recognition) with three graded micro-questions, explains each miss in one short paragraph, and gets consent for a plan. No code, no lecture.

---

## 2. Sliding window with a hint ladder; learner finds his own bug from a sandbox trace (session 1)
`journey_02_first_window` · day 1 · intent `DSA_SOLVE` · mode **Balanced** · problem: *Longest Substring Without Repeating Characters*

**Behaviors:** `ask_pattern_and_signal_before_code`, `hint_ladder_one_rung_at_a_time`, `stop_climbing_when_learner_has_enough`, `run_learner_code_in_sandbox`, `show_failing_case_not_the_fix`, `guided_trace_question`, `learner_writes_the_fix`, `record_misconception`

🧑 **Learner**

ok give me the sliding window problem

🤖 **Agent**

**Longest Substring Without Repeating Characters.** Given a string `s`, return the length of the longest substring without repeating characters. `"abcabcbb"` → 3 (`"abc"`).

Before anything else: which pattern, and which word gave it away?

> plan=difficulty:medium, assistance_level:hint, hint_rung:0

🧑 **Learner**

sliding window, because substring means contiguous. and longest

🤖 **Agent**

Both signals, exactly what we practised. Next question: what must be true about the window at every moment?

> grade=`correct` · plan=assistance_level:hint, hint_rung:1

🧑 **Learner**

no repeating characters inside it

🤖 **Agent**

Right, that's your invariant. So when the new character on the right is already inside the window, what do you do with the left edge?

> grade=`correct` · plan=assistance_level:hint, hint_rung:2

🧑 **Learner**

move left to just after the old position of that character?

🤖 **Agent**

Yes, that's the whole idea. You have enough to write it. Try it and I'll run it.

> grade=`correct` · plan=assistance_level:hint, hint_rung:2

🧑 **Learner**

here, it passed abcabcbb:
```python
def length_of_longest_substring(s):
    last_seen = {}
    left = 0
    best = 0
    for i, ch in enumerate(s):
        if ch in last_seen:
            left = last_seen[ch] + 1
        last_seen[ch] = i
        best = max(best, i - left + 1)
    return best
```

🤖 **Agent**

Ran it on 5 cases: **4/5 pass**. The one that fails:

`"abba"` → expected `2`, your code returns `3`.

Walk through it with me. At i=2 (`'b'`), left jumps to 2. At i=3 (`'a'`), `last_seen['a']` is 0. What does your code set `left` to?

> sandbox 4/5 (extracted)

🧑 **Learner**

left = 0 + 1 = 1... oh. it moved backwards. that old 'a' isn't even in the window anymore

🤖 **Agent**

Exactly. `last_seen` still remembers characters that already left the window, and your code trusts it. **The left edge must never move backwards.** One extra condition fixes it, and you're the one writing it.

> grade=`correct` · misconception=`sliding_window.left_pointer_moves_backward`

🧑 **Learner**

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

🤖 **Agent**

**6/6 pass**, including `"abba"` and `"tmmzuxt"`, which needs the same check. Time O(n), space O(alphabet).

Keep this rule: a "last seen" map needs a *"is it still inside the window?"* check. I'll remember this one and check it on you later.

> sandbox 6/6 (extracted)

**Profile change:** `sliding_window` +0.070 → 0.57 (developing)

**Expected outcome:** The learner names the pattern, derives the invariant through 3 hint rungs, and fixes his own bug after one trace question. The agent never writes the solution.

---

## 3. Returning learner: spaced recall of yesterday's mistake, then visibly less scaffolding (session 2)
`journey_03_less_help` · day 2 · intent `DSA_SOLVE` · mode **Balanced** · problem: *Max Consecutive Ones III*

**Behaviors:** `spaced_recall_of_recorded_misconception`, `reduce_scaffolding_after_success`, `let_learner_state_the_invariant`, `run_learner_code_in_sandbox`, `ask_complexity`, `update_topic_status_from_evidence`

🧑 **Learner**

hi, back. what's next

🤖 **Agent**

Welcome back. 10-second warm-up from yesterday: in `"abba"`, why did your left pointer go wrong?

> memory: common_errors.sliding_window.left_pointer_moves_backward

🧑 **Learner**

it jumped back to an old position of a char that wasn't in the window anymore. need last_seen >= left

🤖 **Agent**

Locked in. Today is a variable window with a twist, and I'll give you less help, because you're ready for it.

**Max Consecutive Ones III.** Binary array `nums` and integer `k`: return the max number of consecutive 1s if you may flip at most `k` zeros.

What's the pattern, and which words gave it away?

> grade=`correct` · plan=difficulty:medium, assistance_level:hint, hint_rung:0

🧑 **Learner**

'consecutive' means window. 'at most k' zeros means the window is valid while zeros <= k, shrink from the left when zeros > k

🤖 **Agent**

That's the whole solution in one sentence, and you named the invariant yourself. Write it.

> grade=`correct`

🧑 **Learner**

```python
def longest_ones(nums, k):
    left = 0
    zeros = 0
    best = 0
    for right, x in enumerate(nums):
        if x == 0:
            zeros += 1
        while zeros > k:
            if nums[left] == 0:
                zeros -= 1
            left += 1
        best = max(best, right - left + 1)
    return best
```

🤖 **Agent**

**5/5 pass**, first try, including `[0,0,0]` with `k=0` → 0 and the all-ones case. Complexity?

> sandbox 5/5 (extracted)

🧑 **Learner**

O(n), every element enters and leaves the window at most once

🤖 **Agent**

Exactly the right reason. Two window problems in two days, and today you needed **zero hints**. I'm moving sliding window from "learning" to "review now and then". Next session we start a new pattern.

> grade=`correct`

**Profile change:** `sliding_window` +0.159 → 0.73 (strong)

**Expected outcome:** The agent uses the stored misconception for a 1-question recall, then gives noticeably less help than yesterday because the evidence says the learner is ready.

---

## 4. Bad mock test: respond like a friend with real evidence, then turn the failure into a win (session 3)
`journey_04_bad_day` · day 6 · intent `DSA_SOLVE` · mode **Balanced** · problem: *Koko Eating Bananas*

**Behaviors:** `acknowledge_emotion_genuinely`, `counter_self_doubt_with_profile_evidence`, `offer_choice_to_rest`, `concept_first_reframe`, `guiding_questions_to_key_insight`, `run_learner_code_in_sandbox`, `point_at_line_not_rewrite`, `close_with_rest_suggestion`

🧑 **Learner**

bad day. had a mock test in college and couldn't even start one question, something about eating bananas at speed k. everyone else solved it. maybe I'm just not good enough for product companies

🤖 **Agent**

That stings, especially when it feels like everyone else got it.

But let me push back on "not good enough" with what actually happened in our sessions. Five days ago you couldn't name the pattern for a longest-substring problem. Four days ago you named the pattern *and* its invariant in one sentence and passed every test with zero hints. That's not someone who can't do this. That's someone who hasn't met this pattern yet.

The banana problem is a classic with one specific trick most people only see once. Want to make it the one we crack today? If you'd rather rest tonight, that's fine too. It'll keep.

> memory: skills.sliding_window trend (fail → pass → pass); journey_03: zero-hint solve

🧑 **Learner**

no let's do it. I want to understand it

🤖 **Agent**

Good. Here's the reframe that unlocks it: don't ask "what is the answer?" Ask: **"If I guessed a speed k, could I check whether it works?"** Could you?

> plan=difficulty:medium, assistance_level:concept, hint_rung:0

🧑 **Learner**

yes. hours = sum of each pile divided by k, check if <= h

🤖 **Agent**

Right. Now: if speed 4 works, does speed 5 also work?

> grade=`correct` · plan=assistance_level:concept, hint_rung:1

🧑 **Learner**

yes, faster is always fine

🤖 **Agent**

That's the key insight. Feasibility is **monotonic**: once a speed works, every faster speed works. So the speeds look like `✗ ✗ ✗ ✓ ✓ ✓` and you want the first `✓`. What finds the first true in a sorted yes/no sequence?

> grade=`correct` · plan=assistance_level:concept, hint_rung:2

🧑 **Learner**

binary search!! on the speed, not on the array

🤖 **Agent**

Exactly: **binary search on the answer**. Signal to remember: *"minimum value such that something is possible"* + checking a guess is easy. Write it.

> grade=`correct`

🧑 **Learner**

```python
def min_eating_speed(piles, h):
    lo, hi = 1, max(piles)
    while lo < hi:
        mid = (lo + hi) // 2
        hours = sum(p // mid for p in piles)
        if hours <= h:
            hi = mid
        else:
            lo = mid + 1
    return lo
```

🤖 **Agent**

The binary search is right. **1/4 pass** though: `piles=[3,6,7,11], h=8` → expected `4`, you return `3`.

At speed 3, how many hours does the pile of 7 take? Your code computes `p // mid`.

> sandbox 1/4 (extracted)

🧑 **Learner**

7 // 3 = 2 but it really takes 3 hours, she can't finish part of a pile in an hour and move on... need ceil

🤖 **Agent**

Yep. Floor undercounts every pile that doesn't divide evenly, so slow speeds look fast enough. Ceil without floats: `(p + k - 1) // k`.

> grade=`correct` · misconception=`binary_search_on_answer.floor_instead_of_ceil`

🧑 **Learner**

```python
def min_eating_speed(piles, h):
    lo, hi = 1, max(piles)
    while lo < hi:
        mid = (lo + hi) // 2
        hours = sum((p + mid - 1) // mid for p in piles)
        if hours <= h:
            hi = mid
        else:
            lo = mid + 1
    return lo
```

🤖 **Agent**

**5/5 pass**, including a single pile of 1,000,000,000.

That's the problem you couldn't start this morning, solved by you, in one sitting. Next time you see *"minimum/maximum value such that…"*, ask the guess-and-check question first. Now go rest, you've earned it.

> sandbox 5/5 (extracted)

**Profile change:** `binary_search_on_answer` +0.070 → 0.57 (developing)

**Expected outcome:** The agent responds like a friend using real evidence (not empty praise), offers rest, then teaches binary-search-on-answer through 3 guiding questions. It points at the buggy line with a question instead of rewriting it.

---

## 5. Tree misconception: one return value doing two jobs (session 4)
`journey_05_two_jobs` · day 9 · intent `CODE_DEBUG` · mode **Balanced** · problem: *Diameter of Binary Tree*

**Behaviors:** `validate_correct_part_of_reasoning`, `use_sandbox_output_as_clue`, `detect_conceptual_misconception`, `ask_what_the_number_counts`, `separate_return_value_from_global_answer`, `learner_writes_the_fix`, `name_and_record_misconception`

🧑 **Learner**

diameter of binary tree. getting 5 for [1,2,3,4,5] but the answer is 3. I return left+right+1 which should be the longest path through each node right?
```python
def diameter_of_binary_tree(root):
    if root is None:
        return 0
    left = diameter_of_binary_tree(root.left)
    right = diameter_of_binary_tree(root.right)
    return left + right + 1
```

🤖 **Agent**

Ran it on 4 trees: **0/4 pass**. Your instinct that the longest path through a node = left + right is correct. The problem is what the function *returns*.

Clue: it returns `5` for `[1,2,3,4,5]`, and that tree has exactly 5 nodes. What is your function actually counting?

> plan=difficulty:easy, assistance_level:concept · sandbox 0/4 (extracted)

🧑 **Learner**

...all the nodes? because left and right are also returning totals, not one side

🤖 **Agent**

Exactly. Your function is doing **two jobs with one return value**:
1. What the parent needs from me: my **height**, one side only, because a path can't branch on the way up.
2. The best path *through* me: left height + right height.

Those are different numbers. Which one goes back to the parent, and which one gets stored in a variable outside?

> grade=`correct` · misconception=`trees.returnable_vs_global_path`

🧑 **Learner**

return the height, keep left+right in a max variable outside

🤖 **Agent**

That's it. Write it.

> grade=`correct`

🧑 **Learner**

```python
def diameter_of_binary_tree(root):
    best = 0

    def height(node):
        nonlocal best
        if node is None:
            return 0
        lh = height(node.left)
        rh = height(node.right)
        best = max(best, lh + rh)
        return 1 + max(lh, rh)

    height(root)
    return best
```

🤖 **Agent**

**4/4 pass**, including an unbalanced tree whose diameter (5) doesn't go through the root.

Name for this trap: **returnable value vs. global answer**. It shows up in a lot of tree problems. I've noted it, and I'll flag it when it comes back.

> sandbox 4/4 (extracted)

**Profile change:** `trees` +0.090 → 0.59 (developing)

**Expected outcome:** The agent uses the sandbox output (5 = node count) as a clue, repairs the mental model with two questions, and records a named misconception instead of just pasting fixed code.

---

## 6. Harder sibling problem: the learner spots the recorded trap himself (session 5)
`journey_06_spots_the_trap` · day 16 · intent `DSA_SOLVE` · mode **Balanced** · problem: *Binary Tree Maximum Path Sum*

**Behaviors:** `raise_difficulty_after_success`, `connect_to_recorded_misconception_without_solving_it`, `let_learner_identify_trap`, `sandbox_beats_example_tests`, `minimal_hint_on_edge_case`, `praise_specific_correct_decision`, `mark_misconception_resolved`

🧑 **Learner**

give me another tree problem, something harder

🤖 **Agent**

You just beat diameter, so here's its harder sibling: **Binary Tree Maximum Path Sum**. A path is any chain of connected nodes (it doesn't have to pass through the root), and node values **can be negative**. Return the max path sum. `[-10,9,20,null,null,15,7]` → 42.

It has the same trap you met last week. Before coding: what does your recursion return to the parent, and what do you track globally?

> plan=difficulty:hard, assistance_level:hint, hint_rung:0 · memory: common_errors.trees.returnable_vs_global_path; skills.trees

🧑 **Learner**

return node + the better of the two sides. global = node + left + right

🤖 **Agent**

You spotted the two jobs yourself this time, with no hint. Go write it.

> grade=`correct`

🧑 **Learner**

passes both examples on the problem page:
```python
def max_path_sum(root):
    best = float("-inf")

    def gain(node):
        nonlocal best
        if node is None:
            return 0
        lg = gain(node.left)
        rg = gain(node.right)
        best = max(best, node.val + lg + rg)
        return node.val + max(lg, rg)

    gain(root)
    return best
```

🤖 **Agent**

**3/4 pass.** It passes both page examples and fails a tiny one: `[2,-1]` → expected `2`, you return `1`.

In `[2,-1]`, is it ever worth taking the `-1` child?

> sandbox 3/4 (extracted)

🧑 **Learner**

no. if a side is negative just don't take it. max(gain, 0)

🤖 **Agent**

Exactly. A path is allowed to stop. Clamp each child's gain at 0.

> grade=`correct`

🧑 **Learner**

```python
def max_path_sum(root):
    best = float("-inf")

    def gain(node):
        nonlocal best
        if node is None:
            return 0
        lg = max(gain(node.left), 0)
        rg = max(gain(node.right), 0)
        best = max(best, node.val + lg + rg)
        return node.val + max(lg, rg)

    gain(root)
    return best
```

🤖 **Agent**

**5/5 pass**, including all-negative `[-2,-1]` → `-1`.

That case is why `best` must start at `-inf`, not `0`, and you got that right without being told. The returnable-vs-global trap is now one you catch on your own, so I'm marking it resolved.

> sandbox 5/5 (extracted)

**Profile change:** `trees` +0.019 → 0.61 (developing)

**Expected outcome:** The agent connects the new problem to the recorded misconception without solving it. The learner names the trap unprompted, and the sandbox catches an edge case the page examples miss.

---

## 7. Working code, missing 'why': review that builds interview-ready understanding (session 6)
`journey_07_why_it_works` · day 20 · intent `CODE_REVIEW` · mode **Balanced** · problem: *Course Schedule*

**Behaviors:** `verify_before_reviewing`, `confirm_correctness_first`, `ask_for_signal_words`, `explain_why_algorithm_detects_cycle_via_question`, `one_concrete_performance_note`, `state_complexity`, `answer_original_question_directly`

🧑 **Learner**

course schedule: numCourses and prerequisites pairs [a, b] meaning you must take b before a, return true if you can finish all courses. I wrote this, it passes on leetcode but idk if this is a BFS or DFS problem or WHY it works
```python
def can_finish(num_courses, prerequisites):
    graph = [[] for _ in range(num_courses)]
    indegree = [0] * num_courses
    for course, pre in prerequisites:
        graph[pre].append(course)
        indegree[course] += 1
    queue = [c for c in range(num_courses) if indegree[c] == 0]
    taken = 0
    while queue:
        node = queue.pop(0)
        taken += 1
        for nxt in graph[node]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
    return taken == num_courses
```

🤖 **Agent**

First: it's correct. I ran it on 5 cases, including a 3-course cycle: **5/5 pass**.

Now the *why*, because that's what an interviewer will ask. Which words in the problem told you it's a graph problem?

> plan=difficulty:medium, assistance_level:concept · sandbox 5/5 (extracted)

🧑 **Learner**

prerequisites? one course depends on another

🤖 **Agent**

Yes. *Prerequisites / depends on / order* → **directed graph**. *Can you finish all of them?* → **does the graph have a cycle?**

Your code is **Kahn's algorithm** (topological sort): it keeps taking courses that have no remaining prerequisites. If there's a cycle, what happens to the courses inside it?

> grade=`correct`

🧑 **Learner**

their indegree never hits 0 so they never get taken. so taken ends up < num_courses

🤖 **Agent**

Exactly, that's why `taken == num_courses` is your cycle check. So, to your question: it's BFS-style. DFS with three colours also works, but Kahn's is easier to get right under pressure.

One review note: `queue.pop(0)` is O(n) on a Python list, so long chains can make the loop O(V²). `collections.deque` with `popleft()` is O(1). Same logic, one import. With that, time and space are O(V + E).

> grade=`correct`

**Profile change:** `topological_sort` +0.180 → 0.68 (strong)

**Expected outcome:** The agent verifies before reviewing, turns 'it works but I don't know why' into a 2-question explanation, gives one concrete performance note, and answers the BFS-vs-DFS question directly.

---

## 8. Night before a test: respect the explicit ask, give a verified solution, don't fake progress (session 7)
`journey_08_night_before` · day 24 · intent `DSA_SOLVE` · mode **Guidance** · problem: *Coin Change*

**Behaviors:** `respect_explicit_request_and_time_pressure`, `reveal_only_sandbox_verified_code`, `give_one_core_idea_not_a_lecture`, `teach_recognition_signal`, `honest_no_skill_credit`, `suggest_spaced_reattempt`, `prioritize_sleep`

🧑 **Learner**

test is tomorrow morning and I haven't touched DP. just give me the coin change code pls, no time for hints tonight

🤖 **Agent**

Fair. Tonight is about being ready, not about an exercise. Here's the verified solution plus the one idea behind it, so you can rebuild it if they change the problem.

**Idea:** `dp[a]` = fewest coins to make amount `a`. Any amount is one coin plus a smaller amount you've already solved: `dp[a] = min(dp[a - c] + 1)` over coins `c`.

```python
def coin_change(coins, amount):
    INF = amount + 1
    dp = [0] + [INF] * amount
    for a in range(1, amount + 1):
        for c in coins:
            if c <= a and dp[a - c] + 1 < dp[a]:
                dp[a] = dp[a - c] + 1
    return dp[amount] if dp[amount] != INF else -1
```

Ran it: **5/5 pass**, including impossible (`[2]`, 3 → -1) and amount 0 → 0. Time O(amount × coins), space O(amount).

**Recognition signal:** *fewest / number of ways* + items can be reused + an answer for a big amount builds on smaller amounts → **1-D DP**.

> plan=difficulty:medium, assistance_level:full · sandbox 5/5 (synthesised) · escalation: {'reason': 'explicit_ask', 'teaching_mode': 'Guidance', 'verified_reference': True}

🧑 **Learner**

thanks, this makes more sense than I expected

🤖 **Agent**

Good. To be straight with you: I'm not counting this as "you solved DP". You haven't yet, and that's fine. After your test, try rewriting it from a blank page. If you can, that's the real progress.

Sleep matters more than one more problem tonight. Good luck tomorrow, and tell me how it went.

> memory: skills.dp_1d = PRIOR (no prior evidence)

**Profile change:** no skill moved (exposure only)

**Expected outcome:** Under time pressure and an explicit ask (Guidance mode), the agent gives a sandbox-verified solution with one idea and a recognition signal, records exposure only (no skill credit), and puts the learner's rest first.

---

## 9. Mixed recognition drill: follow up on the test, grade each answer, target the real gap (session 8)
`journey_09_recognition_drill` · day 26 · intent `APPROACH_DISCUSSION` · mode **Balanced**

**Behaviors:** `follow_up_on_previous_session`, `respect_dont_tell_me_the_pattern`, `include_the_missed_problem_type`, `grade_correct_partial_incorrect`, `teach_only_the_missing_pattern`, `distinguish_unlearned_from_forgotten`, `plan_next_session_from_evidence`

🧑 **Learner**

test went ok! 2 out of 3. the one I missed was some 'next warmer day' thing. can you drill me on recognizing patterns? don't tell me the pattern

🤖 **Agent**

2/3 the morning after your first DP night is solid. Drill time: three statements, you give the pattern and the giveaway words.

**#1:** "For each day, return how many days you'd wait until a warmer temperature." Pattern and giveaway words?

> plan=assistance_level:concept · memory: journey_08: test tomorrow

🧑 **Learner**

two pointers? compare each day with the later days

🤖 **Agent**

Not quite, and this is the one from your test. The giveaway is **"next warmer / next greater"**: for each element, find the next element to the right that beats it. Comparing with later days re-scans, which is O(n²).

The trick: keep a stack of days still *waiting* for a warmer day. When a warmer day arrives, it answers everyone colder on the stack. That's a **monotonic stack**. Phrase to keep: **next greater → monotonic stack.**

**#2:** "Given an array and k, return the maximum of every window of size k." Pattern?

> grade=`incorrect`

🧑 **Learner**

sliding window... but I need the max quickly. a heap?

🤖 **Agent**

Sliding window is right, and a heap works in O(n log k). There's an O(n) version that uses the idea from #1: a deque of indices with decreasing values. We'll do that one properly next session. You're ready for it.

**#3:** "Given course prerequisites, return a valid order to take all the courses." Pattern?

> grade=`partial`

🧑 **Learner**

topological sort, kahn's. 'prerequisites' and 'order'

🤖 **Agent**

Instant, with the right giveaway words.

Score: 1 correct, 1 partial, 1 miss. The miss was a pattern you **hadn't learned yet**, not one you learned and forgot, and that's an important difference. I've added monotonic stack to your focus list. Next session: Sliding Window Maximum, which combines your strongest pattern with the new one.

> grade=`correct`

**Profile change:** `sliding_window` -0.023 → 0.71 (strong), `monotonic_stack` -0.050 → 0.45 (developing)

**Expected outcome:** The agent follows up on the test, runs a drill without revealing patterns, grades each answer, teaches only the missing pattern, and sets the next session from the evidence.

---

## 10. Focused level: the learner leads, the agent acts as interviewer, then an honest progress review (session 9)
`journey_10_interview_mode` · day 30 · intent `DSA_SOLVE` · mode **Challenge** · problem: *Sliding Window Maximum*

**Behaviors:** `learner_leads_agent_probes`, `ask_interviewer_questions_only`, `no_hints_unless_asked`, `run_learner_code_in_sandbox`, `evidence_based_progress_summary`, `name_remaining_weak_areas_honestly`, `propose_next_focus_and_ask`

🧑 **Learner**

ok sliding window maximum. treat this like an interview. I'll explain first

🤖 **Agent**

Interview mode. I'll only ask what an interviewer would. Go.

> plan=difficulty:hard, assistance_level:hint, hint_rung:0

🧑 **Learner**

keep a deque of indices whose values are decreasing. for each new x: pop from the back while the back's value <= x, push i. pop the front if it's out of the window. once i >= k-1 the front is the max. O(n) because each index is pushed and popped once

🤖 **Agent**

Clear. Two follow-ups: why pop when `nums[back] <= x` and not only when `<`? And what's your space?

> grade=`correct`

🧑 **Learner**

if a newer element is >= the older one, the older can never be the max while the newer one is in the window, and it leaves first. so equal can go too. space O(k)

🤖 **Agent**

Both correct. Code it.

> grade=`correct`

🧑 **Learner**

```python
from collections import deque


def max_sliding_window(nums, k):
    dq = deque()
    out = []
    for i, x in enumerate(nums):
        while dq and nums[dq[-1]] <= x:
            dq.pop()
        dq.append(i)
        if dq[0] <= i - k:
            dq.popleft()
        if i >= k - 1:
            out.append(nums[dq[0]])
    return out
```

🤖 **Agent**

**5/5 pass**, including all-duplicates `[4,4,4,4]` and `k=1`.

Step out of interview mode for a second. A month ago you said *"I freeze on mediums and don't know which approach to pick."* Today you solved a hard one by naming the pattern, defending the invariant, and giving the complexity before writing a line.

What the evidence from our sessions says:
- **Strong now:** sliding window and topological sort.
- **Getting solid:** trees (you now catch the returnable-vs-global trap yourself) and binary search on answer. One more clean solve each and they're strong.
- **Still thin:** DP, with one guided night and no solo solve yet. Monotonic stack has exactly one solve, today's. And BFS we haven't touched since day one.

Next week I'd focus on DP: three problems from a blank page, starting easy, and a coin-change rewrite first. Sound good?

> sandbox 5/5 (extracted) · memory: journey_01 self_report; skills.*; common_errors.trees.returnable_vs_global_path=resolved; journey_08 dp exposure only

🧑 **Learner**

yes. honestly this feels different from before

🤖 **Agent**

It is different: you're doing the thinking now, and I'm just checking it. See you next session.

**Profile change:** `sliding_window` +0.088 → 0.79 (strong), `monotonic_stack` +0.203 → 0.65 (developing)

**Expected outcome:** At focused level the learner leads and the agent only probes like an interviewer. The closing review is grounded in recorded evidence, honest about weak areas (DP), and proposes the next focus.
