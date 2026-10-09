# Behaviour gap: evidence before any edit

Branch `experimental` @ `61a3823`. Written 2026-10-09. No code was changed to
produce this document. Scored against `docs/target_behavior.md` section 26 (the
15 decision questions) and section 30 (the 12 invariants).

## 1. What the evidence is, and its limits

Source: Postgres, read-only (`messages`, `conversations`, `learning_events`,
`learner_profiles`, `hint_progress`).

- **There are not 15 real conversations.** The database has 332 users; 329 are
  test, probe and eval accounts. Three are people: `Sravan`, `sravan`, `Anu`.
  Between them 11 conversations with messages survive (a 12th is empty), holding
  **48 assistant turns**. Older ones were deleted: 28 of `Sravan`'s 39 learning
  events have `conversation_id = NULL`.
- **Most turns predate the fixes.** By date (UTC):

  | When | Code at the time | Turns | BAD |
  |---|---|---|---|
  | 09-27 | before the audit | 15 | 14 |
  | 10-03 | before the audit | 15 | 12 |
  | 10-05 | before / during rounds 2-3 | 9 | 6 |
  | 10-06, before 15:03 | before `61a3823` | 6 | 4 |
  | 10-07 | after `61a3823` | 3 | 3 |

  Only the three 10-07 turns can have run on the current commit, and whether
  the server had been restarted on it is not recorded anywhere. For the other
  36 BAD turns a later fix may already cover them; **none was replayed**, so
  this document does not claim they still fail. It claims what caused them.
- Messages do not store the route, the plan or whether an image was attached.
  Where a score depends on that, the row says so.

## 2. Every assistant turn

Conversations: `S-A` = Sravan `648416bd` (trapping-rain-water code), `S-B` =
Sravan `c0d7c02a` (recursion, 678, roadmap), `s-1..5` = sravan `de37`, `53ce`,
`f395`, `30dd`, `70f5`, `A-1..4` = Anu `421c`, `743b`, `6895`, `e6fe`.
The number is the assistant message's `seq`.

| Turn | Learner said | Tutor did | Verdict | Broke | Cause |
|---|---|---|---|---|---|
| S-A 2 | "give correct code of this" + code | A hint and a question; the code was never read | BAD | Inv 4, 5 | RC-1 |
| S-A 4 | "give full code and tell me where is the bug" | "I won't show code that hasn't been run", then two corpus sections on sorted two-pointer sums | BAD | Inv 4, 5; sec 25 | RC-4 |
| S-A 6 | "give python code" | Another hint question | BAD | Inv 4, 2 | RC-1 |
| S-A 8 | "i asked for python code" | "Hi! Share a problem statement..." | BAD | Inv 4 | RC-1 |
| S-A 10 | "fix this code" + fragment with `left -= 1`, `right += 1` | Five-section report blaming indentation; the two real bugs were not found | BAD | Inv 5, 11 | RC-6 |
| S-A 12 | "is their another method for this program?" | "It looks like you might want to explain a concept. Could you confirm" | BAD | Q1, Inv 2 | RC-1 |
| S-B 2 | "explain the concept of recursion with examples" | "The references don't cover recursion" | BAD | Inv 4 | RC-4 |
| S-B 4 | same | Detailed explanation, two examples run in the sandbox | GOOD | | |
| S-B 6 | "explain sliding window with examples" | Same shape (citation marks leaked) | GOOD | | |
| S-B 8 | "how to solve this problem" (678, image) | One step, one question | GOOD | | |
| S-B 10 | "give roadmap to master stack,queue" | A roadmap (5,088 chars, unrelated families) | GOOD | | |
| S-B 12 | "first where should i start" | Next hint on 678, the problem from the day before | BAD | Q1, Inv 2 | RC-1 |
| S-B 14 | "im asking about the roadmap" | A new 22-week plan for all of DSA | BAD | Inv 2 | RC-2 |
| S-B 16 | "im asking where should i start with stack and queue" | A third full plan instead of an answer | BAD | Inv 2, Q8 | RC-2 |
| S-B 18 | "give code the valid paranthesis problem" | "The model did not return usable code this time" | BAD | Inv 4 | RC-4 |
| S-B 20 | same | Code for 678, sandbox 2/2 | GOOD | | |
| S-B 22 | "give code using stack" | "It looks like you might want to explain a concept" | BAD | Inv 4 | RC-1 |
| S-B 24 | "give code using stack" | Prose describes a stack solution, O(n) space; the code is the same greedy low/high counter as before | BAD | Inv 4 | RC-5 |
| s-1 2 | "find root cause error" + Two Sum code | Named the exact fault (`target + nums`) | GOOD | | |
| s-2 2 | Max Path Sum, "intuition first, then code, then tests" | Canned "restate the problem in your own words" | BAD | Inv 4 | RC-3 |
| s-2 4 | "Give me the next hint." | Canned "think about what state you need to track" | BAD | Inv 7 | RC-3 |
| s-2 6 | "give full answer" | A hint: "For a heaps problem, trees is often the right shape" | BAD | Inv 4 | RC-1 |
| s-2 8 | A new problem (tree cameras) | The previous problem's last hint, "this is as far as this hint level goes" | BAD | Q1 | RC-1 |
| s-2 10 | "give full code" | The same hint again, word for word | BAD | Inv 2, 4 | RC-1 |
| s-2 12 | A new problem (recover tree) | The same hint a third time | BAD | Inv 2 | RC-1 |
| s-2 14 | Word Ladder, with requirements | Canned "restate the problem" | BAD | Inv 4 | RC-3 |
| s-2 16 | Critical Connections, with requirements | Canned "restate the problem" | BAD | Inv 4 | RC-3 |
| s-2 18 | "give code for that" | "The learner asks for code but does not provide a problem" | BAD | Inv 4 | RC-1 |
| s-2 20 | "give full code for this problem:" + statement | Hint 1 | BAD | Inv 4 | RC-1 |
| s-3 2 | "hi" | "It looks like you might want to discuss an approach" | BAD | Q1 | RC-1 |
| s-3 4 | "give proper plan to master DSA in 4 months" | Hint 1 of the ladder, on a study plan | BAD | Inv 4 | RC-1 |
| s-3 6 | "Give me the next hint." | Hint 2 of the ladder, on a study plan | BAD | Q1 | RC-1 |
| s-3 8 | same | Hint 3 of the ladder, on a study plan | BAD | Q1 | RC-1 |
| s-4 2 | "Hi" | Greeting | GOOD | | |
| s-4 4 | Recover-tree problem, "explain, implement, execute" | Canned hint plus a pattern-recognition dump | BAD | Inv 4; sec 25 | RC-3 |
| s-4 6 | "find root cause error" + code | Named the exact fault | GOOD | | |
| s-4 8 | "how to start dsa preparation... give roadmap" | A roadmap | GOOD | | |
| s-4 10 | "first what should i master." | A second 4,925-char roadmap | BAD | Inv 2 | RC-2 |
| s-4 12 | "lets start with easy problem" | A **medium** bit-manipulation problem | BAD | Inv 4 | RC-5 |
| s-4 14 | "hint?" | A trees hint; the practice problem was Sum of Two Integers | BAD | Q1 | RC-1 |
| s-5 2 | "How to solve this problem" | "Could you confirm" (no image stored; cannot tell if one was sent) | BAD | Q1 | RC-1 |
| s-5 4 | "check the image i shared" | The same "could you confirm" | BAD | Inv 2 | RC-1 |
| A-1 2 | "how to start DSA preparation" | Raw corpus listing ("DSA corpus - Advanced Range family") | BAD | sec 25 | RC-3 |
| A-2 2 | "hi agent" | A full study plan | BAD | Q1 | RC-1 |
| A-3 2 | "how to solve this problem" (longest palindrome) | Canned hint, labelled a sliding-window problem | BAD | Q5 | RC-3 |
| A-3 4 | "answer this only. what question i gave to u" | The same raw corpus listing | BAD | Inv 4 | RC-1 |
| A-3 6 | "Give me the next hint." | Canned hint, same wrong pattern | BAD | Inv 7 | RC-3 |
| A-4 2 | "hii agent" | A 12-week study plan | BAD | Q1 | RC-1 |

**39 BAD, 9 GOOD of 48.**

Invariants 8 and 12 (reduce help after success; use learner state later) are
not scored per turn. They fail on all 48 for one reason, section 4.

## 3. BAD turns grouped by root cause

| | Root cause | Turns | Share | Example | Produced at |
|---|---|---|---|---|---|
| RC-1 | **No single decision about the turn.** What the turn is about and what to do are worked out in five places, each from the classifier's label. When the label is unsure or wrong the turn falls to "could you confirm", a greeting, a study plan, or the wrong subject | 22 | 56% | S-B 22: "give code using stack" on an open problem got "It looks like you might want to explain a concept" | `app/graph/nodes.py:797` (`_problem_update`), `:936` (`_continues_last_reply`), `app/graph/routing.py:256` (`select_route`, low confidence at `:277`), `app/agents/planner.py:661` (`build_plan`), `nodes.py:2609` (`_with_tutoring`) |
| RC-3 | **The reply is a template, not a tutor.** Canned ladder text, fixed sections under headers, or the corpus printed back | 8 | 21% | s-2 2: asked for intuition first, got "restate the problem in your own words" | `app/agents/hint_engine.py:332` (`_rung_text`), `app/response/generate.py:361` (`_assemble_text`), `nodes.py:2762` |
| RC-2 | **The agent answers without the conversation.** A follow-up is answered as a first message | 3 | 8% | S-B 14: "im asking about the roadmap" got a new 22-week plan | `nodes.py:2255` (history passed only when `thread_followup`); `debug_agent` `:1874`, `explain_agent` `:2078`, `practice_agent` `:1909` never get it |
| RC-4 | **An internal limit is handed to the learner as a refusal.** A failed model call, an unverified result or an empty retrieval becomes the whole reply | 3 | 8% | S-B 18: "the model did not return usable code this time" | `nodes.py:1629` (`verified_reference` returns `None`), `app/execution/synth.py:324` |
| RC-5 | **What the learner asked for never reaches the thing that writes the answer.** "using stack", "easy", a language | 2 | 5% | S-B 24: asked for a stack solution, got the earlier greedy one under prose describing a stack | `app/execution/synth.py:89` and `:346`, `app/agents/dsa_solver.py:118`, `nodes.py:1949` |
| RC-6 | **A pasted fragment is not made runnable, so the debugger reports the paste as the bug** | 1 | 3% | S-A 10: blamed indentation, missed `left -= 1` | `app/input/snippet.py` |

**One cause explains most of it.** RC-1 is 22 of 39. RC-2 is the same failure
seen from the agent's side (the turn was not placed in its conversation), and
together they are 25 of 39, 64%. The four earlier rounds each added a rule to
one of the five places in RC-1; none removed a place.

Detail on RC-5, because it is the one about code and language. On an explicit
ask for the code, the code comes from `verified_reference`, which calls the
**test-synthesis prompt**. That prompt is told the learner's text is data to
analyse, never an instruction, and to return "a complete, standalone Python
module". So the learner's own words about the code they want are ignored by
design. The "key insight" and complexity above the code come from a different
model call, which did read the ask. That is why S-B 24 describes one algorithm
and shows another.

## 4. The learner profile receives no evidence

This is separate from the table and it is total.

| Measure | Value |
|---|---|
| Learning events from the three people | 59 |
| Of those, carrying an outcome (`solved` not null) | **0** |
| Of those, a graded answer (`concept_check`) | 4 |
| Topics that have moved off 0.50, all three profiles | **0 of 19** |
| `skill_seen` (when a skill last got evidence) | empty in all three |
| `learning_preferences` set | 1 of 3, both values `false` |
| `learner_profiles.language` set, all 283 profiles | **0** |

`Sravan`'s profile: every topic exactly 0.50, last written 2026-10-05, after 39
events. Two of those events say `needed_full_solution = true` with 7 hints
used. They moved nothing.

Why:

1. `_build_learning_event` (`nodes.py:2909`-`2937`) takes `solved` from the
   agent, and it is set only when the sandbox judged the **learner's own code**
   against a suite it trusts. Everything else is stored as `evidence_source =
   "none"`.
2. `apply_event` (`app/memory/profile.py:216`) treats such an event as exposure
   and leaves the skill "bit-for-bit unchanged".
3. `FULL_SOLUTION_SCORE` (0.30) is reachable only when `solved` is true, so
   asking for the answer is never evidence of anything.
4. The only other path is answering the tutor's question and being graded. The
   owner asks for code and explanations and does not answer those questions
   (4 graded answers in 39 events).

The audit's section 11 table ("needed the full solution: 2 events to become
weak") describes the arithmetic, not what happens. In real use the input to
that arithmetic never arrives.

So every one of the 48 turns was planned at skill 0.50, difficulty medium.
Nothing in any reply changed because of who the learner was.

**The badge.** For a live reply `adapted` comes from `plan_adapted`
(`app/agents/planner.py:832`), which is false at 0.50. But a message loaded
from history gets `adapted: message.role === "assistant"`
(`frontend/js/api.js:197`): every stored reply shows "Adapted to your level".
That is the badge in the pasted transcripts.

**Language.** `learner_profiles.language` exists and `set_language`
(`profile.py:379`) is exported; nothing calls it and no agent reads the
column. The sandbox type is `Language = Literal["python"]`
(`app/schemas/execution.py:47`) and both code prompts say Python. A learner who
wants Java gets Python. No real turn asked for another language, so this is
confirmed from the code, not from a transcript.

## 5. The five expected causes, checked

| | Expected | From the evidence |
|---|---|---|
| A | No single tutoring decision | **Confirmed, and it is the main cause** (RC-1, 22 turns; 25 with RC-2). No step produces the section 2.2 label for what the learner just showed; agents each re-read the message |
| B | Agents answer without the conversation | **Confirmed, smaller than expected in real data** (3 turns, all study-plan follow-ups, addressed by `61a3823` and not re-measured). For the debugger, code explainer and reviewer it is confirmed in the code and has no real turn behind it |
| C | Replies are assembled reports | **Confirmed** as the primary cause of 8 turns and present in most others. One conflict to settle: audit section 12 records an owner decision that every explanation is detailed with examples; the target is one step and one question |
| D | Adaptation is mostly a label | **Understated.** It is entirely a label: no evidence reaches the profile in real use (section 4), and the badge on history is unconditional |
| E | Rule lists as the router | **Confirmed in the code.** `_EXPLICIT_ASK_RE`, `_GUIDANCE_ASK_RE`, `_LEARNING_ASK_RE` and the meta regexes still decide whenever the classifier is unsure, which is when routing fails. The same word ("start") is in two lists with opposite effects. Not counted separately: it is how RC-1 fails |

Not on the list, found in the evidence:

- **F (RC-5):** the learner's constraints and language do not reach the code
  generator.
- **G (RC-4):** capacity and verification failures surface as refusals.
- **H (RC-6):** a method-body fragment is diagnosed as an indentation error.

## 6. Proposed order for Phase 2 (not started)

1. **A + E:** one `decide_turn` step after classification that outputs the
   learner evidence label, the subject (active problem, last reply, new) and the
   intervention. `_problem_update`, `select_route`, `build_plan` and
   `_with_tutoring` read it; the relabel rules go. This is a rewrite of the
   routing layer, justified by 25 of 39 turns.
2. **D:** make the profile receive evidence, then make it visible, then fix the
   badge. Needs a decision (below).
3. **F:** pass the learner's ask and preferred language to the code generator
   as a structured field, not as text. Needs a decision (below).
4. **B:** the same `<conversation_so_far>` block for the debugger, explainer and
   reviewer.
5. **C:** `final_response` as one step and one question, hard gates kept in code.
6. **G, H:** a failed reference call retries or says what it can still do;
   snippet repair handles a body fragment.

## 7. Decisions (owner, 2026-10-09)

1. **"As planned"** is the five conversations in
   `eval/behavior/adaptive_examples.jsonl` (`adaptive_001` to `adaptive_005`):
   one step at a time for a beginner, the exact bug for shared code, a
   challenge left alone until help is asked for, the mental model corrected
   before any code, and difficulty that rises after success and help that
   rises again when the learner is stuck.
2. **Language: Python.** Code is written and verified in Python. Other
   languages are out of scope; the unused `learner_profiles.language` column
   stays unused.
3. **Evidence** and 4. **explanations**: "implement the best choice for the
   agent to behave as the adaptive agent". Taken as the recommendations made
   with the question: needing the full solution and the hints used count as
   weak evidence; an explanation the learner asks for stays detailed, and a
   follow-up on one is answered short.

## 8. What was done, and what was measured

The changes per root cause, the live numbers and what still does not match
the target are in `docs/AUDIT_REPORT.md`, section 14. In short: the five-run
measurement was started on commit `d8e4021` and the Groq quota ran out during
run 3, so two runs are valid (19 of 20 scenarios each). That is evidence, not
the merge bar.
