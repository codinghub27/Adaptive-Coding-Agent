# PHASE-04 — LangGraph Orchestration & Teaching Planner

## Status
Not Started

## Goal
Wire the pieces from Phases 01–03 into a real LangGraph flow with a typed state,
and add the Teaching Planner node that decides *how* to help based on intent +
learner profile. Specialized agents are stubs here — this phase proves the graph.

## Scope
- `app/graph/state.py` — one typed `AgentState` (structured input, intent,
  profile snapshot, plan, retrieved context, execution result, response, events).
- `app/graph/nodes.py` — nodes: `understand_input`, `classify_intent`,
  `load_learner_profile`, `plan_teaching`, `route`, plus **stub** nodes for
  `dsa`, `debug`, `explain`, and a `final_response` node.
- `app/graph/build.py` — assemble nodes + conditional edges; compile the graph.
- `app/agents/planner.py` — Teaching Planner: given intent + profile + problem
  analysis, choose difficulty, assistance level (hint/concept/pseudocode/
  partial/full), and a solution strategy. Emits a typed `TeachingPlan`.
- `POST /chat` — run the graph end-to-end (stubbed agents), return response +
  the plan for inspection.

Graph skeleton:
```
START → understand_input → classify_intent → load_learner_profile
      → plan_teaching → route ─┬─ DSA ─┐
                               ├─ DEBUG┼→ (stub) → final_response
                               └─EXPLN─┘         → update_learner_model → END
```

## Out of Scope
- Real DSA/debug/explain logic (Phase 07), RAG (05), sandbox (06).
- Frontend, evaluation.
- Future phases.

## Current Implementation
Document only what actually exists after inspecting the repository.

## Planned Changes
1. Define `AgentState` and `TeachingPlan` schemas.
2. Implement the non-agent nodes using Phase 02/03 modules.
3. Implement `plan_teaching` with explicit rules (e.g. `prefers_hints` +
   weak skill → start hint ladder low; strong skill → concise).
4. Implement conditional routing on intent; stub agents return placeholder text.
5. Add clarifying-question branch when intent confidence is low.
6. Wire `update_learner_model` at graph end to emit a `LearningEvent`.
7. `POST /chat`; tests assert routing + plan for representative inputs.

## Files Expected
- Create: `app/graph/{__init__,state,nodes,build,routing}.py`,
  `app/agents/{__init__,planner}.py`, `app/schemas/plan.py`,
  `tests/graph/test_routing.py`, `tests/agents/test_planner.py`
- Modify: `app/main.py` (register `/chat`)
- Delete: None unless explicitly approved

## Architecture Decisions
- State is a single typed object threaded through nodes (no hidden globals).
- Planner output is data (`TeachingPlan`), not prose, so agents and evaluation
  can consume it deterministically.
- Each specialized capability will become a **subgraph** in Phase 07; keep the
  route node's contract stable now.

## Dependencies
- PHASE-02 (understand + intent), PHASE-03 (profile + events).

## Implementation Notes
Keep this section short. Record node names/contract and routing keys — these are
depended on by Phase 07.

## Manual Test Cases
### Test 1
Input: `CODE_DEBUG` request from a user weak in `sliding_window` who
`prefers_hints`.
Expected: Routes to debug stub; `TeachingPlan.assistance_level` starts low
(hint), difficulty matches profile.
Actual:

### Test 2
Input: Ambiguous message (low intent confidence).
Expected: Graph takes the clarifying-question branch instead of guessing.
Actual:

## Commands Run
```text
# commands
```

## Test Results
-

## Security / Reliability Notes
- Node failures must not crash the graph mid-run; return a safe fallback state.
- Bound total LLM calls per graph run to avoid runaway loops.

## Known Issues
-

## Git Commit
Not created yet.

## Next Phase
- PHASE-05 — Knowledge Base & Hybrid RAG.
