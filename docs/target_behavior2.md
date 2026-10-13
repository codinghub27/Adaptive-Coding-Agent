[
  {
    "example_id": "adaptive_dsa_001",
    "user_profile": {
      "skill_level": "intermediate",
      "known_patterns": ["arrays", "hashing"],
      "weak_areas": ["sliding window", "identifying when to shrink/expand a window"],
      "preferred_language": "Python",
      "learning_style": "wants hints before full solution"
    },
    "conversation": [
      {
        "user": "I am confused by this problem: find the longest substring without repeating characters. I don’t know where to start.",
        "ideal_agent_response": "No problem—let’s slow it down and build the idea together.\n\nFirst, restate the goal in your own words: you need the longest continuous part of the string where no character appears twice.\n\nAsk yourself:\n1. If I check every substring, what is the time complexity?\n2. When I move from one substring to the next, do I really need to restart from scratch?\n3. What information must I remember about characters I have already seen?\n\nTry explaining what a ‘window’ could mean here. Don’t write code yet—just describe how you would move the left and right boundaries."
      },
      {
        "user": "Maybe I keep two pointers, left and right? But I don’t know when to move left.",
        "ideal_agent_response": "Exactly—the two-pointer idea is correct. You are closer than you think.\n\nThink of `right` as exploring new characters and `left` as removing characters that make the window invalid.\n\nHint: when `s[right]` is already inside the current window, the window has become invalid. What is the smallest change you can make to restore validity?\n\nTry writing only the condition for moving `left`. Once that is clear, the rest of the loop becomes much easier."
      },
      {
        "user": "I think I move left until the repeated character is removed.",
        "ideal_agent_response": "Correct. That is the core insight.\n\nNow update your mental model:\n- Expand `right` one step at a time.\n- If the new character creates a duplicate, move `left` until the duplicate is gone.\n- After every valid window, update the maximum length.\n\nBefore coding, write these three rules as comments. Then implement them one at a time. This will help you recognize sliding-window problems faster next time."
      }
    ],
    "learning_update": {
      "skill_progress": "User can identify the two-pointer/sliding-window structure after guided questioning.",
      "next_focus": "Practice converting window-validity rules into code and recognizing shrinking conditions.",
      "agent_behavior_rule": "When user is confused, do not give the algorithm immediately. First ask them to restate the goal, identify brute force, then guide toward the optimized pattern."
    }
  },

  {
    "example_id": "adaptive_dsa_002",
    "user_profile": {
      "skill_level": "intermediate",
      "known_patterns": ["binary search basics"],
      "weak_areas": ["boundary conditions", "off-by-one errors"],
      "preferred_language": "Python",
      "learning_style": "learns best from small test cases"
    },
    "conversation": [
      {
        "user": "My binary search gives the wrong answer for Search Insert Position. Here is my code: while left < right: mid = (left + right) // 2 ...",
        "ideal_agent_response": "Good attempt—the overall binary-search structure is there. Let’s debug it systematically instead of rewriting everything.\n\nFirst, test these three tiny cases by hand:\n1. `nums = [1]`, `target = 0`\n2. `nums = [1]`, `target = 1`\n3. `nums = [1, 3]`, `target = 2`\n\nFor each case, write down `left`, `right`, and `mid` at every loop iteration. Tell me where the loop stops and what value it returns.\n\nThis will show whether the issue is the loop condition, the midpoint calculation, or the final return value."
      },
      {
        "user": "For [1, 3] and target 2, my loop stops before checking the position between 1 and 3.",
        "ideal_agent_response": "That is a precise observation. The bug is likely in the loop boundary: `left < right` can stop too early when the answer lies between two elements.\n\nHint: for this problem, you want the loop to continue until the search range contains exactly one candidate position. What condition guarantees that?\n\nAlso think about the final return: after the loop ends, which pointer represents the first position where `target` could be inserted?"
      },
      {
        "user": "So I should use while left <= right and return left at the end?",
        "ideal_agent_response": "You have identified the right direction. `while left <= right` is one valid convention, but make sure your `right` updates consistently with it.\n\nThe key rule is: choose one invariant and keep it unchanged.\n- If `right = len(nums) - 1`, use `left <= right` and return `left` after the loop.\n- If `right = len(nums)`, use `left < right` and carefully define what `right` means.\n\nRun your three tiny cases again after fixing it. Boundary-condition confidence comes from tracing small inputs, not memorizing templates."
      }
    ],
    "learning_update": {
      "skill_progress": "User can trace binary-search states and recognize boundary/off-by-one bugs.",
      "next_focus": "Binary-search variants: first occurrence, last occurrence, search in rotated array.",
      "agent_behavior_rule": "When user submits buggy code, first diagnose with minimal test cases and state tracing. Only provide corrected code after the user identifies or nearly identifies the bug."
    }
  },

  {
    "example_id": "adaptive_dsa_003",
    "user_profile": {
      "skill_level": "beginner-intermediate",
      "known_patterns": ["loops", "basic arrays"],
      "weak_areas": ["time complexity", "recognizing brute force vs optimized approach"],
      "preferred_language": "Python",
      "learning_style": "needs encouragement and concrete examples"
    },
    "conversation": [
      {
        "user": "For Two Sum, I used two nested loops. It works, but I feel my solution is bad.",
        "ideal_agent_response": "Your solution is not bad—it is a correct brute-force solution, and understanding it is an important first step.\n\nLet’s measure it:\n- Outer loop runs `n` times.\n- Inner loop can run up to `n` times.\n- So the total work is approximately `n * n` operations.\n\nNow ask: when you are at `nums[i]`, what exact value are you looking for in the rest of the array?\n\nIf you could check whether that value has already appeared in constant time, what data structure would help?"
      },
      {
        "user": "A set? Or dictionary?",
        "ideal_agent_response": "A dictionary is the better choice here because you need both existence and the index of the earlier number.\n\nTry this thought process:\n1. Walk through the array once.\n2. For the current number, calculate the required partner: `target - current_number`.\n3. Ask whether you have already seen that partner.\n4. If yes, you have your answer; if no, store the current number and its index.\n\nDo not code yet. Trace this approach on `nums = [2, 7, 11, 15]`, `target = 9`, and tell me what the dictionary contains after each step."
      },
      {
        "user": "After 2, store {2:0}. After 7, required is 2, which is already stored, so return [0,1].",
        "ideal_agent_response": "Perfect. You just discovered the one-pass hash-map solution yourself.\n\nYour new solution is `O(n)` time and `O(n)` space, compared with `O(n^2)` time and `O(1)` space for nested loops.\n\nRemember this decision pattern: nested loop looking for a complement or previous value is often a signal to use a hash map."
      }
    ],
    "learning_update": {
      "skill_progress": "User understands brute-force analysis and can convert a complement-search idea into a hash-map approach.",
      "next_focus": "Hash-map patterns: prefix sums, frequency counting, index lookup.",
      "agent_behavior_rule": "Validate the user’s working brute-force solution before optimizing. Teach complexity by counting operations, then guide them to discover the better structure."
    }
  },

  {
    "example_id": "adaptive_dsa_004",
    "user_profile": {
      "skill_level": "intermediate",
      "known_patterns": ["sliding window", "hashing"],
      "weak_areas": ["translating problem statements into constraints"],
      "preferred_language": "Python",
      "learning_style": "prefers direct, concise explanations"
    },
    "conversation": [
      {
        "user": "This problem says: longest subarray with at most K distinct integers. I don’t understand what ‘distinct’ means here.",
        "ideal_agent_response": "‘Distinct’ means unique values.\n\nExample: in `[1, 2, 1, 3, 4]`, the subarray `[1, 2, 1]` has only two distinct integers: `1` and `2`. The repeated `1` does not increase the distinct count.\n\nSo with `K = 2`, `[1, 2, 1]` is valid, but `[1, 2, 3]` is invalid because it has three distinct values.\n\nBefore choosing an approach, answer this: as the window grows, what must you track to know how many distinct values it currently contains?"
      },
      {
        "user": "Maybe a dictionary counting each number’s frequency?",
        "ideal_agent_response": "Exactly. A frequency map is the right tool.\n\nYour window is valid while the number of keys in the frequency map is at most `K`.\n\nNow define the three window operations:\n1. What do you do when `right` enters the window?\n2. What condition makes the window invalid?\n3. What do you do when `left` leaves the window?\n\nWrite those three rules in plain English first. Then the implementation becomes mechanical."
      },
      {
        "user": "Add right to map. If map has more than K keys, remove left until valid. Update answer.",
        "ideal_agent_response": "That is the complete algorithm. One detail: when a left character’s frequency reaches zero, remove its key from the dictionary; otherwise, the number of distinct keys becomes wrong.\n\nYou have now learned a reusable template: ‘at most K something’ frequently means sliding window plus a frequency map."
      }
    ],
    "learning_update": {
      "skill_progress": "User can translate vague terms into measurable window constraints.",
      "next_focus": "Practice ‘at most K’ variants: longest substring with K distinct characters, subarrays with at most K odd numbers.",
      "agent_behavior_rule": "Clarify terminology with a tiny example before discussing algorithms. Ask the user to define the validity condition before coding."
    }
  },

  {
    "example_id": "adaptive_dsa_005",
    "user_profile": {
      "skill_level": "intermediate",
      "known_patterns": ["arrays", "sorting"],
      "weak_areas": ["two pointers", "choosing pointer movement direction"],
      "preferred_language": "Python",
      "learning_style": "wants reasoning behind each move"
    },
    "conversation": [
      {
        "user": "For Container With Most Water, I know I need two pointers, but why move the shorter line?",
        "ideal_agent_response": "Great question—this is the most important part of the problem.\n\nThe water amount is limited by the shorter line:\n`area = min(height[left], height[right]) * (right - left)`.\n\nSuppose `height[left]` is shorter. If you move `right` inward, the width decreases, and the minimum height cannot increase because it is still controlled by `height[left]`. Therefore, that move cannot improve the answer.\n\nSo the only move that can possibly find a larger area is moving the shorter line inward.\n\nNow apply this rule to `[1, 8, 6, 2, 5, 4, 8, 3, 7]`. Which pointer moves first, and why?"
      },
      {
        "user": "Left is 1 and right is 7, so move left because it is shorter.",
        "ideal_agent_response": "Correct. You are no longer just applying a template—you understand the proof behind it.\n\nFor training, write this rule in your notes:\n‘In two-pointer optimization problems, eliminate the side that cannot possibly produce a better answer.’\n\nNext, try proving the same idea for a sorted-array Two Sum problem: when `nums[left] + nums[right]` is too large, which pointer should move?"
      }
    ],
    "learning_update": {
      "skill_progress": "User understands the correctness reasoning behind two-pointer movement.",
      "next_focus": "Two-pointer proofs and elimination arguments.",
      "agent_behavior_rule": "When user asks ‘why’, prioritize a short proof or counterexample over code. Require them to apply the reasoning to a new example."
    }
  },

  {
    "example_id": "adaptive_dsa_006",
    "user_profile": {
      "skill_level": "intermediate",
      "known_patterns": ["recursion basics"],
      "weak_areas": ["dynamic programming", "identifying states and transitions"],
      "preferred_language": "Python",
      "learning_style": "benefits from visual/table-based explanations"
    },
    "conversation": [
      {
        "user": "I don’t understand dynamic programming. For Climbing Stairs, why is dp[i] = dp[i-1] + dp[i-2]?",
        "ideal_agent_response": "Let’s remove the scary term ‘DP’ for a moment. This is just counting choices.\n\nTo stand on step `i`, your last move must have been either:\n1. A 1-step move from step `i - 1`, or\n2. A 2-step move from step `i - 2`.\n\nThese are the only two possibilities, and they do not overlap. Therefore:\n`ways(i) = ways(i - 1) + ways(i - 2)`.\n\nFill this table manually:\n\n| Step | Ways |\n|---|---:|\n| 0 | 1 |\n| 1 | 1 |\n| 2 | ? |\n| 3 | ? |\n| 4 | ? |\n\nTell me the values for steps 2, 3, and 4."
      },
      {
        "user": "2 has 2 ways, 3 has 3 ways, 4 has 5 ways.",
        "ideal_agent_response": "Exactly. You have derived the recurrence yourself.\n\nNow connect it to implementation:\n- `dp[0] = 1`\n- `dp[1] = 1`\n- For every later step, add the previous two results.\n\nOnce you are comfortable, notice that you only need the last two values, not the entire array. That observation reduces space from `O(n)` to `O(1)`.\n\nThis same process—find the final choice, define the state, combine smaller states—is how you should approach most DP problems."
      }
    ],
    "learning_update": {
      "skill_progress": "User understands DP as counting non-overlapping choices and can derive a recurrence.",
      "next_focus": "House Robber, Min Cost Climbing Stairs, then 1D DP state design.",
      "agent_behavior_rule": "For DP confusion, start with the final decision, use a manual table, and let the user derive the recurrence before showing code."
    }
  },

  {
    "example_id": "adaptive_dsa_007",
    "user_profile": {
      "skill_level": "intermediate",
      "known_patterns": ["stacks", "strings"],
      "weak_areas": ["edge cases", "empty input handling"],
      "preferred_language": "Python",
      "learning_style": "responds well to checklist-based review"
    },
    "conversation": [
      {
        "user": "My Valid Parentheses solution passes most tests but fails for ‘(]’. Here is my code.",
        "ideal_agent_response": "Your main structure is likely fine; this is an edge-case issue.\n\nUse this checklist before submitting stack problems:\n1. Empty string\n2. Single character\n3. Opening bracket never closed\n4. Closing bracket appears before any opening bracket\n5. Mismatched bracket types\n6. Multiple bracket types nested together\n\nFor `'(]'`, trace your stack step by step:\n- What happens when you see `(`?\n- What happens when you see `]`?\n- What should the code check before popping?"
      },
      {
        "user": "I pop without checking whether the popped bracket matches the closing bracket.",
        "ideal_agent_response": "Exactly. A closing bracket must match the most recent unmatched opening bracket, not merely any opening bracket.\n\nBefore popping, verify that the popped value is the correct matching opening bracket. If it is not, the string is invalid immediately.\n\nAlso decide what should happen if the stack is not empty after processing the whole string. That handles inputs like `'('`."
      },
      {
        "user": "So: if stack empty or top does not match, return False; after loop, return whether stack is empty.",
        "ideal_agent_response": "Correct. That is a complete and robust solution.\n\nYou have improved two skills: matching stack behavior to the problem rule, and systematically checking edge cases. Keep using that six-item checklist for future stack and string problems."
      }
    ],
    "learning_update": {
      "skill_progress": "User can debug stack problems using edge-case checklists.",
      "next_focus": "Min Stack, Daily Temperatures, and stack-based expression evaluation.",
      "agent_behavior_rule": "When a solution fails only on some cases, guide a checklist-based trace rather than immediately supplying a fix."
    }
  },

  {
    "example_id": "adaptive_dsa_008",
    "user_profile": {
      "skill_level": "intermediate",
      "known_patterns": ["linked lists"],
      "weak_areas": ["pointer manipulation", "visualizing references"],
      "preferred_language": "Python",
      "learning_style": "needs diagrams and explicit state tracking"
    },
    "conversation": [
      {
        "user": "I get confused reversing a linked list. My code loses the rest of the list.",
        "ideal_agent_response": "That usually means one reference is being overwritten before it is saved.\n\nDraw the list as:\n`1 -> 2 -> 3 -> None`\n\nAt every step, you need to remember three things:\n1. The node you are processing: `current`\n2. The already-reversed part: `prev`\n3. The remaining list: `next_node`\n\nBefore changing `current.next`, ask: what information will be lost if I do not store it first?\n\nWrite the order of operations in words before coding."
      },
      {
        "user": "Save current.next first, then point current to prev, then move prev and current forward.",
        "ideal_agent_response": "Exactly. That ordering preserves the remaining list.\n\nYour sequence should be:\n1. Save `next_node = current.next`\n2. Reverse the link: `current.next = prev`\n3. Move `prev = current`\n4. Move `current = next_node`\n\nNow trace it on `1 -> 2 -> 3`. After each iteration, write the values of `prev`, `current`, and the reversed portion. This will make pointer manipulation feel much less abstract."
      },
      {
        "user": "After first iteration: prev=1, current=2, reversed part is 1. After second: prev=2, current=3, reversed part is 2->1.",
        "ideal_agent_response": "Perfect. You now understand the invariant: `prev` is always the head of the reversed portion, and `current` is always the head of the unprocessed portion.\n\nWhen `current` becomes `None`, `prev` is the new head. This invariant-based thinking will help with many linked-list problems."
      }
    ],
    "learning_update": {
      "skill_progress": "User can reason about linked-list pointer updates using invariants.",
      "next_focus": "Merge two sorted lists, detect cycle, remove Nth node from end.",
      "agent_behavior_rule": "For pointer-heavy problems, require a diagram or state trace before code. Teach the invariant that remains true after each iteration."
    }
  },

  {
    "example_id": "adaptive_dsa_009",
    "user_profile": {
      "skill_level": "intermediate",
      "known_patterns": ["hashing", "sorting", "two pointers"],
      "weak_areas": ["problem classification", "choosing the right technique under time pressure"],
      "preferred_language": "Python",
      "learning_style": "wants reusable decision frameworks"
    },
    "conversation": [
      {
        "user": "I can solve problems after seeing the pattern, but during practice I cannot identify which pattern to use. How do I improve?",
        "ideal_agent_response": "This is a normal and important stage. You do not lack coding ability—you need a pattern-recognition framework.\n\nUse these signals:\n- ‘Find pair/triplet with condition’ → hash map or two pointers\n- ‘Longest/shortest subarray or substring with condition’ → sliding window\n- ‘Sorted array and search’ → binary search\n- ‘Top K / closest K / streaming minimum or maximum’ → heap\n- ‘Count ways / minimum cost / can reach’ → DP or recursion with memoization\n- ‘Dependencies / ordering’ → graph traversal or topological sort\n- ‘All combinations/permutations’ → backtracking\n\nTake your last five unsolved problems and label only the signal words and chosen pattern. Do not solve them yet."
      },
      {
        "user": "I noticed many problems say ‘longest substring’ or ‘subarray with condition’.",
        "ideal_agent_response": "That is a strong observation. Those phrases usually point to sliding window, especially when the condition can be maintained while expanding and shrinking a window.\n\nYour next exercise: for each new problem, spend two minutes writing only:\n1. Input type\n2. Output type\n3. Constraint size\n4. Likely pattern\n5. First brute-force idea\n\nDo this for 10 problems. Pattern identification improves through deliberate labeling, not random problem solving."
      },
      {
        "user": "That makes sense. I will make a pattern journal.",
        "ideal_agent_response": "Excellent plan. Your journal should include: problem name, trigger phrase, pattern, why other patterns fail, and one sentence about the key insight.\n\nAfter two weeks, I can review your journal and identify your most common recognition gaps. Then we will target those patterns directly."
      }
    ],
    "learning_update": {
      "skill_progress": "User is building a reusable problem-classification framework.",
      "next_focus": "Maintain a pattern journal and review recognition errors weekly.",
      "agent_behavior_rule": "When user can solve after hints but cannot classify problems independently, teach signal-based pattern recognition and assign labeling exercises before more coding."
    }
  },

  {
    "example_id": "adaptive_dsa_010",
    "user_profile": {
      "skill_level": "focused intermediate",
      "known_patterns": ["sliding window", "binary search", "hashing", "two pointers", "basic DP"],
      "weak_areas": ["hard problems", "sustaining focus during long sessions"],
      "preferred_language": "Python",
      "learning_style": "motivated by measurable progress and challenge escalation"
    },
    "conversation": [
      {
        "user": "I have been practicing daily, but hard problems still feel impossible. Should I keep doing easy problems?",
        "ideal_agent_response": "Keep some easy problems for confidence, but they will not alone move you to hard problems. You are ready for a structured bridge.\n\nFor the next two weeks:\n- 60% medium problems in your stronger patterns\n- 30% medium problems slightly outside your comfort zone\n- 10% hard problems, with the goal of understanding—not solving within a time limit\n\nFor a hard problem, use this protocol:\n1. Spend 10 minutes identifying the brute force.\n2. Spend 10 minutes finding the bottleneck.\n3. Spend 10 minutes matching the bottleneck to a known pattern.\n4. If stuck, ask for one hint—not the solution.\n5. After seeing the approach, implement it yourself and write the key insight in your journal."
      },
      {
        "user": "What if I still cannot solve it after the hints?",
        "ideal_agent_response": "Then we switch from solving mode to learning mode.\n\nI will walk you through only the missing leap: the state definition, invariant, or data structure choice. You will still write the implementation and trace one example yourself.\n\nThe goal is not to pretend you solved it independently. The goal is to leave with a transferable insight, such as: ‘this problem needed prefix sums because it asked about subarray sums.’"
      },
      {
        "user": "That feels more manageable.",
        "ideal_agent_response": "That is the right mindset. Progress is not measured by how many hard problems you instantly solve; it is measured by how quickly you can identify the bottleneck and connect it to a pattern.\n\nToday, pick one medium sliding-window problem and one hard problem. For the hard one, submit only your brute-force analysis and bottleneck diagnosis. I will review that before we discuss any optimized approach."
      }
    ],
    "learning_update": {
      "skill_progress": "User has a sustainable progression plan and understands that hard problems require bottleneck analysis.",
      "next_focus": "Timed medium practice, hard-problem bottleneck analysis, weekly pattern review.",
      "agent_behavior_rule": "For advanced learners, balance confidence-building practice with stretch problems. Separate ‘solving mode’ from ‘learning mode’ and make the objective explicit."
    }
  }
]