# PHASE-08 — Response Generation, Frontend & Evaluation (LangSmith)

## Status
Not Started

## Goal
Turn structured agent results into clear, adaptive responses; ship a usable
frontend; and close the loop with LangSmith tracing + an evaluation suite so the
agent's teaching quality is measurable.

## Scope
- `app/response/generate.py` — Response Generator: render the correct output
  shape (hint / explanation / correct code) from the agent result + plan, with
  next steps. Optional streaming.
- `app/response/format.py` — consistent structured sections (approach, code,
  complexity, common mistakes) matching the plan's assistance level.
- `frontend/` — Streamlit app: multimodal input (text/code/image upload), chat
  view, hint-ladder controls, learner-profile panel.
- LangSmith: ensure every graph node/LLM/tool call is traced end-to-end.
- `eval/` — dataset + evaluators: routing accuracy, hint-appropriateness
  (no full solution when hint was requested), debug-fix success (verified),
  explanation correctness, groundedness to retrieved context.
- `POST /chat` returns the final structured response; wire the Streamlit client.

## Out of Scope
- React/Next.js frontend (later), Redis/Celery scale-out (later).
- New agent capabilities.

## Current Implementation
Document only what actually exists after inspecting the repository.

## Planned Changes
1. Implement response generation + formatting keyed to `TeachingPlan`.
2. Build the Streamlit UI (input, chat, hint controls, profile panel).
3. Verify LangSmith tracing coverage across the whole graph; add run metadata
   (intent, plan, hints_used, verified).
4. Create an eval dataset (curated problems + expected behaviour) and evaluators.
5. Add a `make eval` target; record baseline scores here.
6. End-to-end tests: full `/chat` for one DSA-hint, one debug, one explain case.

## Files Expected
- Create: `app/response/{__init__,generate,format}.py`,
  `frontend/app.py`, `frontend/components/*.py`,
  `eval/{dataset.py,evaluators.py,run_eval.py}`,
  `tests/e2e/test_chat_flows.py`
- Modify: `app/graph/{nodes,build}.py` (final_response uses generator),
  `app/main.py`, `README.md` (run frontend + eval)
- Delete: None unless explicitly approved

## Architecture Decisions
- Response shape is derived from the plan, so a hint request can never render as
  a full solution — enforced at generation, not just prompting.
- Evaluation measures *teaching* behaviour (hint appropriateness, verified fixes,
  groundedness), not just answer correctness — matching the project's thesis.

## Dependencies
- PHASE-07 (agent results), PHASE-04 (plan/state), PHASE-05 (context for
  groundedness eval), PHASE-06 (verified fixes for debug eval).

## Implementation Notes
Keep this section short. Record eval dataset location, evaluator names, and the
baseline scores once measured.

## Manual Test Cases
### Test 1
Input: End-to-end `DSA_HINT` request via the Streamlit UI.
Expected: UI shows a single next hint with a control to request the next level;
no full code until the top level; trace appears in LangSmith.
Actual:

### Test 2
Input: `make eval` on the seed dataset.
Expected: Produces routing accuracy, hint-appropriateness, and debug-fix-success
scores; baseline recorded in this doc.
Actual:

## Commands Run
```text
# commands
```

## Test Results
-

## Security / Reliability Notes
- Never send secrets or raw user PII to LangSmith metadata.
- Frontend calls the API only; it never executes code or hits the sandbox
  directly.

## Known Issues
-

## Git Commit
Not created yet.

## Next Phase
- v2 backlog: React/Next.js frontend, Redis short-term cache, Celery for heavy
  eval/execution, multi-language sandbox, preference-optimization from evaluated
  data.
