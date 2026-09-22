# PHASE-07 — Specialized Agents (DSA Solver, Debugger, Code Explainer)

## Status
Not Started

## Goal
Replace the Phase 04 stubs with the three real capabilities — as LangGraph
subgraphs — using the planner, RAG context, and the sandbox. This is where the
"teach, don't dump" behaviour becomes real.

## Scope
- `app/agents/dsa_solver.py` + subgraph — derive a solution the teaching way:
  understand → constraints → pattern → brute force → why it's slow → key insight
  → pseudocode → code → line-by-line → tests → complexity → common mistakes.
- `app/agents/hint_engine.py` — the **progressive hint ladder** (L0 nudge → L1
  what-to-track → L2 data-structure hint → L3 concrete idea → L4 pseudocode →
  L5 partial → L6 full). Emits the *next* hint given how far the user has gotten;
  stops when solved.
- `app/agents/debugger.py` + subgraph — static analysis → infer intended
  approach → run tests (Phase 06) → find failing case → trace → localize → explain
  the bug → patch → re-run → verify.
- `app/agents/explainer.py` + subgraph — AST/Tree-sitter parse → structure
  (module/class/function/block/line) → line-by-line explanation → complexity.
- `app/agents/reviewer.py` — code review pass (correctness via tests,
  complexity, readability, Python practices, edge cases, improvements).
- Swap the stub route targets in `app/graph/build.py` for these subgraphs.

## Out of Scope
- Final response formatting/streaming (Phase 08) — agents return structured
  results, not final prose.
- Frontend, evaluation.
- Future phases.

## Current Implementation
Document only what actually exists after inspecting the repository.

## Planned Changes
1. Implement the hint ladder as a pure function of (problem, plan, progress).
2. Build the DSA subgraph using RAG context + hint engine; never emit full code
   when the plan says hint-level.
3. Build the debugger subgraph on top of the sandbox; always re-verify patches.
4. Build the AST-based explainer (Python via `ast`; Tree-sitter for structure).
5. Implement the reviewer, grounding "correctness" in sandbox results.
6. Register subgraphs; update routing; add `LearningEvent` fields (hints_used,
   needed_full_solution) from actual agent behaviour.
7. Tests per agent with fixed fixtures.

## Files Expected
- Create: `app/agents/{dsa_solver,hint_engine,debugger,explainer,reviewer}.py`,
  `app/graph/subgraphs/{dsa,debug,explain}.py`, `app/schemas/agent_results.py`,
  `tests/agents/test_hint_engine.py`, `tests/agents/test_debugger.py`,
  `tests/agents/test_explainer.py`
- Modify: `app/graph/{build,routing,state}.py`, `app/memory/events.py`
- Delete: None unless explicitly approved

## Architecture Decisions
- Hint level is driven by the `TeachingPlan`, so the same problem yields
  different help for different learners — the core differentiator.
- Debugger and reviewer treat sandbox verification as ground truth; the LLM
  explains results, it does not certify correctness.
- Each capability is an isolated subgraph so the system stays clean as it grows.

## Dependencies
- PHASE-04 (planner/graph), PHASE-05 (RAG context), PHASE-06 (sandbox/verify).

## Implementation Notes
Keep this section short. Record the hint-ladder levels and the agent result
schema (consumed by Phase 08).

## Manual Test Cases
### Test 1
Input: `DSA_HINT` on "Subarray Sum Equals K", learner mid-level, `prefers_hints`.
Expected: Returns the next single hint (not full code); repeated asks climb the
ladder; full solution only at the top level.
Actual:

### Test 2
Input: `CODE_DEBUG` on a wrong sliding-window solution with tests.
Expected: Identifies the failing case, explains the window-shrink bug, patches,
re-runs, and verification passes.
Actual:

### Test 3
Input: `CODE_EXPLAIN` on a `two_sum` implementation.
Expected: Line-by-line explanation from AST structure + correct O(n)/O(n)
complexity.
Actual:

## Commands Run
```text
# commands
```

## Test Results
-

## Security / Reliability Notes
- All code runs go through the Phase 06 sandbox — agents never execute directly.
- Guard against hint-ladder skipping (don't leak full solution at a low level).

## Known Issues
-

## Git Commit
Not created yet.

## Next Phase
- PHASE-08 — Response Generation, Frontend & Evaluation (LangSmith).
