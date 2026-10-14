# Adaptive behaviour dataset: 10-session learner journey

Reference conversations showing how the Adaptive Coding Agent should behave as
one learner grows from **confused intermediate** ("I freeze on mediums, I don't
know which approach to pick") to **focused problem-solver** (solves a hard
problem in interview mode, explaining first).

Put this folder at `eval/behavior/journey/` in the repo.

## Files

| File | What it is |
|---|---|
| `adaptive_journey_10.jsonl` | The dataset: one conversation per line, with per-turn agent metadata, evidence, profile before/after, and eval checks |
| `adaptive_journey_10.md` | The same content, readable |
| `verify_examples.py` | Runs every learner/agent code snippet in the dataset |
| `sandbox_ground_truth.json` | Real results from that run; every "N/M pass" line in the dataset comes from here |
| `build_dataset.py` | Builds and validates the JSONL (re-run after any edit) |
| `render_md.py` | Regenerates the readable version |

## The journey

| # | Day | What the agent demonstrates | Problem |
|---|---|---|---|
| 1 | 1 | Diagnose the real gap with 3 graded micro-questions; get consent for a plan | (no code) |
| 2 | 1 | Hint ladder; sandbox finds a bug; learner fixes it himself after one trace question | Longest Substring w/o Repeating |
| 3 | 2 | Spaced recall of yesterday's mistake; visibly less help because evidence says so | Max Consecutive Ones III |
| 4 | 6 | Bad mock test: friend-like reply grounded in real evidence, then teach the missed pattern | Koko Eating Bananas |
| 5 | 9 | Misconception (one return value doing two jobs) repaired, named, recorded | Diameter of Binary Tree |
| 6 | 16 | Harder sibling: learner spots the recorded trap himself; sandbox beats page examples | Binary Tree Max Path Sum |
| 7 | 20 | "It works but I don't know why": verify, then build interview-ready understanding | Course Schedule |
| 8 | 24 | Night before a test: verified full solution on request, **no fake skill credit** | Coin Change |
| 9 | 26 | Recognition drill without revealing patterns; teach only the missing one | (mixed) |
| 10 | 30 | Interview mode: learner leads; honest, evidence-based progress review | Sliding Window Maximum |

## Record schema (per line)

- `conversation_id`, `session`, `day`, `title`, `intent`, `teaching_mode`, `problem`
- `agent_behavior`: behaviour tags this example demonstrates
- `conversation[]`: `{role, content}`. Assistant turns also carry `meta`:
  - `intent`, `route`, `plan {difficulty, assistance_level, hint_rung}`
  - `grade`: grade of the learner's previous reply (`correct` / `partial` / `incorrect`)
  - `pending_check`: what the agent is waiting for (`question` or `code_submission`)
  - `sandbox {ref, passed, total, first_failing, provenance}`: real execution result
  - `evidence[]`: learner-model events (`sandbox`, `concept_check`, `exposure`)
  - `misconception`: id from a closed catalog
  - `memory_used`: which stored facts the turn relied on (and why it is allowed to)
  - `escalation`: why full code was revealed (session 8 only)
- `profile_before` / `profile_after`: skills, labels, deltas, common_errors, preferences
- `eval_checks`: turn-level assertions for the replay harness

## Rules every example follows (and the validator enforces)

1. Every "N/M pass" line matches a real sandbox run. No pass claim without one.
2. Full code from the agent appears only when `assistance_level == "full"` (session 8).
3. `solved` comes only from sandbox runs of the learner's own code.
4. A revealed solution gives **exposure only**: the skill doesn't move.
5. A `question` turn always asks something; the agent asks one thing at a time.
6. Topics come from a closed vocabulary; misconceptions use stable ids.
7. Progress statements match the computed profile. Session 10 says "getting solid", not "strong", for trees, because the numbers say so.

## Calibration used

`PRIOR 0.5` · `ALPHA 0.3` (sandbox evidence) · `CONCEPT_ALPHA 0.1` (graded
conceptual answers, cannot push a skill past `HARD 0.68` on its own) · labels:
`< 0.42` weak, `>= 0.68` strong. `CONCEPT_ALPHA` is the value proposed in the
tutoring-features upgrade; if you pick a different one, re-run
`build_dataset.py` and update any progress wording the validator flags.
