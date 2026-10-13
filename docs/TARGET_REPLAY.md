# Target replay: where the agent stands against the target conversations

Date: 2026-10-13. Branch `claude/code-answers-knowledge-check`.

The owner wrote 20 target conversations (`docs/target_behavior.md`, which is
`eval/behavior/adaptive_examples.jsonl` in prose, and
`docs/target_behavior2.md`) and recorded 7 turns the agent answered badly
(`docs/current-behavior.md`). `eval/behavior/target_replay.py` sends the
LEARNER turns of all of them through the live API, each conversation on a
fresh account, and writes the agent's reply beside the target reply.

    .\venv\Scripts\python.exe -m eval.behavior.target_replay --out replay.md --pace 4

77 learner turns in 21 conversations.

## How it is scored

Two numbers, and they are not the same thing.

- **Checks** (`CHECKS` in the script): properties of the target reply that can
  be read off the response without judgement: code shown or not, the technique
  not named, a `-1` contract mentioned, no reference list. They are a floor. A
  reply can pass every check and still be the wrong reply.
- **Reading**: each reply read against the target reply and marked as matching
  its behaviour or not. This is one reader's judgement of one run, not a
  measurement with an error bar.

## Result

| | Checks | Reading |
|---|---|---|
| Before (commit `a9569f0`) | 132 / 152 (86.8%) | 21 / 77 turns (27%) |
| After, cloud model, first 60 turns | 141 / 152 over the whole run, see below | 49 / 60 turns (82%) |
| After, local model, the 6 conversations the outage cut (23 turns) | 41 / 42 | 19 / 23 turns (83%) |

The target of a 95% match is **not reached**. By this reading the agent went
from about a quarter of the target turns to about four in five.

The two "after" rows overlap on `a010` (4 turns) and must not be added up.
In the baseline the same 23 local-row turns scored 4 of 23: thirteen of them
were the "I can't tell what that refers to" line or a lecture, three were the
wrong answer to "give me code with a bug".

What limits the "after" row:

- The cloud run was cut short. From turn 61 on every Groq credential returned
  a quota wait of about 25 minutes, so the last 17 turns of that run are the
  fixed "I couldn't put a reply together" line and are not counted in the
  reading. Those conversations (`b007`-`b010`, `a010`, `current`) were replayed
  against the local model (`qwen-direct`, thinking off, through an
  evaluation-only bridge; the project's provider settings were not changed).
  A 9B local model is not the model the product runs on, so those replies show
  that the routing holds, not how good the cloud model's replies are.
- Five of the eleven cloud misses were fixed AFTER that run and have not been
  re-measured on the cloud model: a reply whose JSON contained a code fence was
  dropped (`a001` turn 4), an ask for the corrected code repeated the whole
  diagnosis (`a003` turn 2, `a004` turn 3), "but how does that become code?"
  was answered with the solution (`a006` turn 2), and an unread first message
  got the clarifying question (`a010` turn 0).
- One run. The classifier's reading varies between runs, and so does the
  reply. A single transcript proves little either way (`CLAUDE.md`,
  "Measuring behaviour").

## What was wrong before, by cause

Read from the baseline run, 56 turns did not match. Almost all of them came
from five causes.

1. **The conversation had no subject.** Only a curated problem named by its
   title was stored. "How should I solve Longest Substring Without Repeating
   Characters?" stored nothing, so whenever the classifier was unsure the next
   message got "there is no problem or code from you in this conversation
   yet" (`a002` turns 1-3, `b008`, `b009`, `b010`, `a010`: 14 turns).
2. **A question was handed to a specialist that answers something else.** "So
   is the pattern just two pointers?" got a six-part lesson on two pointers
   over a sorted array; "is DFS fine here?" got a lesson and a program;
   follow-ups got a new sandboxed program each time (`a001`, `a005`, `a006`,
   `b004`-`b006`: 19 turns).
3. **The code was handed over when it had been declined or not asked for.**
   The bare phrase "the answer" counted as an ask ("why can the answer use
   both sides?"); "can I try the code and you review it?" was read as a demand
   for the solution (`a001`, `a004`, `a002`: 6 turns).
4. **A request with no specialist of its own.** "Give me code with a bug and I
   will fix it" got a practice problem, then the correct solution; "don't tell
   me the algorithm" got the topic name, the pattern's signal and "which
   technique would you use?" (`current`, `a007`: 6 turns).
5. **The debugger's evidence was about the wrong thing.** The end of a
   function was wrapped, run, and its crash on the missing lines reported as
   the learner's bug; a correct attempt called "a review" got a page of naming
   remarks (`a009`, `a002` turn 4, `b002`, `b007`: 6 turns).

## What changed

See `CLAUDE.md`, "How a turn actually works", the entries dated 2026-10-13:
talk is its own move (`discuss`), a conversation that opens on a problem has a
subject, "the answer" is a word, an attempt is checked first, the debugging
exercise, a challenge names no technique, part of a function is read and not
run, a shared image is stored with its message, no reference list is printed.

## Known gaps after this change

- **Under rate limiting the reply degrades to a template.** When the solver's
  call fails the hint ladder's fixed rung is said, and it can name a pattern
  from a retrieval guess ("look for what makes this prefix sum problem tick"
  on Contains Duplicate). Not changed here.
- **The dialogue agent sometimes asks a question after "I get it now".** Its
  prompt says not to; the cloud model did it on 2 of 5 such turns.
- **With the answer withheld it can still name the next data structure** when
  the learner's own idea is half way there (`a007` turns 2-3).
- **`a007` cannot be replayed fairly past turn 0.** The target's learner
  answers about Longest Consecutive Sequence; the agent hands out whichever
  problem its corpus picks.
- **The earlier live scenarios (`live_scenarios.py`, 24 of them) were not
  re-run** after this change: the provider quota was exhausted. Several of
  their checks expect the solver's fixed leads ("That's right.") on turns that
  are now talk, so some will need their wording updated, and that has not been
  measured.
- **The rendered web UI was not opened in a browser.** Image persistence was
  verified through the API (stored, served back byte-identical, 401 without a
  token, 404 for another account); the bundle was rebuilt and contains no
  "References" heading.
