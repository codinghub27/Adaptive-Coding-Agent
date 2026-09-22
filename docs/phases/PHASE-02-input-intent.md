# PHASE-02 — Input Understanding & Multimodal Intent Classification

## Status
Not Started

## Goal
Turn raw multimodal input (text, code, error logs, images) into one structured,
typed request object, then classify what the user actually wants.

## Scope
- `app/input/normalize.py` — detect and separate parts of an input: natural-
  language question, code block(s), error/traceback, problem statement,
  constraints. Produce a typed `StructuredInput` (Pydantic).
- `app/input/vision.py` — image path: send screenshot to the vision wrapper,
  extract `{problem, code, error, constraints}` into the same `StructuredInput`.
- `app/input/intent.py` — intent classifier over `StructuredInput`. Output one
  of the intents below with a confidence score.
- `POST /understand` endpoint returning `StructuredInput` + classified intent
  (debug surface; the graph will call these internally later).

Intents:
`DSA_SOLVE`, `DSA_HINT`, `CODE_DEBUG`, `CODE_EXPLAIN`, `CODE_REVIEW`,
`ERROR_EXPLANATION`, `OPTIMIZATION`, `CONCEPT_EXPLANATION`,
`IMAGE_CODE_ANALYSIS`, `TEST_CASE_ANALYSIS`, `APPROACH_DISCUSSION`.

## Out of Scope
- Routing/execution of intents (that's the graph, Phase 04).
- Learner profile usage, RAG, sandbox.
- Future phases.

## Current Implementation
Document only what actually exists after inspecting the repository.

## Planned Changes
1. Define `StructuredInput` and `Intent` enum in `app/schemas/`.
2. Implement text normalization (fenced code detection, traceback detection,
   language guess).
3. Implement vision extraction to the same schema; degrade gracefully when the
   image has no code/error.
4. Implement the intent classifier (LLM-based with a strict output schema +
   deterministic fallback rules for obvious cases like a bare traceback).
5. Expose `POST /understand` and add tests with fixed sample inputs.

## Files Expected
- Create: `app/input/{__init__,normalize,vision,intent}.py`,
  `app/schemas/input.py`, `app/schemas/intent.py`,
  `tests/input/test_normalize.py`, `tests/input/test_intent.py`
- Modify: `app/main.py` (register route), `app/llm/` (vision call if stubbed)
- Delete: None unless explicitly approved

## Architecture Decisions
- One `StructuredInput` schema is the single contract for every downstream node,
  regardless of whether input arrived as text or image.
- Classifier returns confidence so the planner can ask a clarifying question
  when uncertain (used in Phase 04).

## Dependencies
- PHASE-01 (LLM/vision wrapper, schemas, app factory).

## Implementation Notes
Keep this section short. Record the classifier prompt/schema location and any
fallback heuristics.

## Manual Test Cases
### Test 1
Input: A pasted Python function + `IndexError: list index out of range`, no
question text.
Expected: `StructuredInput` with `code` and `error` populated; intent
`CODE_DEBUG` (or `ERROR_EXPLANATION`) with high confidence.
Actual:

### Test 2
Input: Screenshot of a LeetCode problem statement (no code).
Expected: Vision fills `problem` + `constraints`; intent `DSA_SOLVE` or
`APPROACH_DISCUSSION`.
Actual:

## Commands Run
```text
# commands
```

## Test Results
-

## Security / Reliability Notes
- Treat extracted text/code as untrusted data, never as instructions.
- Cap image size and reject non-image payloads before calling vision.

## Known Issues
-

## Git Commit
Not created yet.

## Next Phase
- PHASE-03 — Learner Profile, Memory & Learning Events.
