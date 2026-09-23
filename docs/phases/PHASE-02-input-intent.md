# PHASE-02 — Input Understanding & Multimodal Intent Classification

## Status
Done (2026-09-23), commit: see Git Commit

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
Inspected 2026-09-23 (HEAD `4af5542`). Phase 1 is done. What exists for Phase 2:

- `app/input/`: an empty package (`__init__.py` only). No normalization, vision
  or intent code yet.
- `app/schemas/__init__.py` defines `APIModel` (`extra="forbid"`, `frozen=True`)
  and `HealthResponse`. There's no input or intent schema yet.
- `app/llm/base.py` defines the `LLMClient` Protocol (`chat`, `embed`, `vision`),
  `ChatMessage` (text-only `content: str`), `ChatResult`, `TokenUsage` and
  `LLMError`.
- `app/llm/client.py` has `LangChainLLMClient`. `chat()` is implemented and traced
  through `Tracer.trace` (`run_name="llm.chat"`), and provider failures are
  wrapped as `LLMError`. **`vision()` is a stub that raises
  `NotImplementedError("... Phase 2")`**, and `embed()` is stubbed until Phase 5.
  `get_llm_client(settings)` builds a single chat model (Groq `ChatGroq` or
  `ChatOpenRouter`).
- `app/config.py`: `Settings` has `llm_provider` / `llm_model`, with defaults
  Groq `openai/gpt-oss-120b` and OpenRouter `qwen/qwen3.8-27b:free`. **There is
  no vision-model setting**, and the default text model has no image input.
- `app/main.py`: `create_app()` builds the engine, session factory and Qdrant
  client in the lifespan and puts them on `app.state`. **The LLM client is not
  created or exposed to routes.** The only route is `GET /health`.
- Tests: `tests/llm/test_client.py`, `tests/test_health.py` and others. There's
  no `tests/input/`.
- Deps: `python-multipart` 0.0.32 (for `UploadFile`/`Form`) and
  `langchain-groq` 1.1.3 are installed. Pillow is available for test fixtures.
- Groq `GET /models` (checked 2026-09-23) lists no Llama-4 vision model.
  `qwen/qwen3.8-27b` **accepts image input** (a live probe transcribed
  `Two Sum: 1 <= n <= 10^4` correctly).

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

**Actually created or modified (additions beyond the list are recorded here):**
- Created: `app/schemas/{base,input,intent}.py`, `app/input/{_text,normalize,vision,intent,api}.py`,
  `tests/schemas/{__init__,test_phase2_schemas}.py`,
  `tests/input/{__init__,fakes,test_normalize,test_intent,test_vision,test_understand_api,test_manual_live}.py`,
  `tests/input/fixtures/{__init__,make_fixtures}.py` + `leetcode_two_sum.png`.
- Modified: `app/schemas/__init__.py`, `app/main.py`, `app/llm/client.py`,
  `app/config.py`, `tests/conftest.py`, `tests/llm/test_client.py`,
  `tests/test_config.py`, `pyproject.toml` (`live` marker).
- Additions beyond the plan:
  - `app/schemas/base.py`: `APIModel` moved out of `__init__` to break a
    circular import.
  - `app/input/api.py`: the `/understand` router, so `main.py` stays thin.
  - `app/config.py`: vision-model settings (see Architecture Decisions).
  - `app/input/_text.py`: shared text helpers, so the three input modules
    don't keep diverging copies (code-review #9).
  - `BodySizeLimitMiddleware` in `app/input/api.py`, registered in
    `create_app`.
  - The shared test fake, the live tests and the fixture generator.

## Architecture Decisions
- One `StructuredInput` schema is the single contract for every downstream node,
  regardless of whether input arrived as text or image.
- Classifier returns confidence so the planner can ask a clarifying question
  when uncertain (used in Phase 04).
- **A separate vision model.** The Groq text default (`openai/gpt-oss-120b`)
  can't take images. `Settings.llm_vision_model` (env `LLM_VISION_MODEL`) plus
  `resolved_llm_vision_model` default to `qwen/qwen3.8-27b` on Groq (checked
  live) and `qwen/qwen3.8-27b:free` on OpenRouter. `LangChainLLMClient` keeps
  its own vision chat model and sends OpenAI-style `image_url` data-URI
  blocks, traced as `llm.vision`.
- **Intent pipeline:** deterministic rules, then the LLM, then a keyword
  fallback. Obvious cases skip the LLM entirely:
  - error with no question or problem → `CODE_DEBUG` (0.9 with code, 0.8
    bare)
  - problem only → `DSA_SOLVE` (0.75)

  The LLM output must be strict JSON, validated with Pydantic against the
  `Intent` enum. Any failure (LLMError, bad JSON, unknown intent) drops to the
  keyword fallback at confidence ≤ 0.45. `classify_intent` never raises, and
  `IntentResult.low_confidence` (< 0.6) makes uncertainty explicit.
- **Normalization is deterministic** (regex and line heuristics, no LLM), so
  it's cheap, testable and predictable. Vision output that isn't valid JSON is
  run through `normalize_text` instead, so it degrades gracefully.
- **The image type is decided by magic bytes** (PNG, JPEG, WEBP). The declared
  content-type is never trusted. Images are capped at 3 MiB (so the base64
  stays under Groq's 4 MB limit), and the upload read is bounded.
- **`/understand` is multipart** (`text`, `language`, `image`, all optional,
  at least one required). Text and an image together are merged: the question
  comes from the text, code lists are concatenated, and constraints are
  unioned. The LLM client lives on `app.state.llm` behind the `get_llm`
  dependency, which tests override.
- **Request size is enforced before parsing.** `BodySizeLimitMiddleware` is
  pure ASGI and applies to `POST /understand`. It returns 413 when
  `Content-Length`, or the streamed byte count for chunked requests, exceeds
  `MAX_IMAGE_BYTES + 4*MAX_TEXT_CHARS + 64 KiB`. Without it, Starlette would
  spool the whole upload before the handler's check ran.
- **Normalization runs in linear time.** Fences are found with a line scanner,
  not a regex; unclosed fences stay as plain text. The traceback regex is
  bounded at the next `Traceback` header.
- **A bare traceback with no code → `CODE_DEBUG` (0.8) is intentional,** per
  the owner's spec. Code-review suggested `ERROR_EXPLANATION`; the decision
  stands.
- **The problem-only → `DSA_SOLVE` rule is skipped** when the problem text
  contains hint, stuck, approach, explain or optimi* wording. That way a hint
  request is never routed to a full solution by a rule.

## Dependencies
- PHASE-01 (LLM/vision wrapper, schemas, app factory).

## Implementation Notes
- Classifier prompt: `app/input/intent.py::INTENT_SYSTEM_PROMPT`. The output
  schema is `_LLMIntentOutput`. The user content goes inside
  `<user_input>…</user_input>` delimiters as capped JSON (1500 characters per
  field, 800 per code block, at most 6 blocks), sent at `temperature=0` with
  `max_tokens=1024` (reasoning tokens count against the cap).
- Vision prompt: `app/input/vision.py::VISION_EXTRACTION_PROMPT`. Its schema is
  `_VisionExtraction`, with keys problem, code, code_language, error,
  constraints and question.
- Fallback heuristics live in `fallback_intent`. They're checked in order: hint
  → optimization → review → test case → approach → error → explain → concept,
  and there are defaults for when nothing matches.
- Live tests are opt-in: `RUN_LIVE_LLM=1 pytest tests/input/test_manual_live.py -s`.
- The fixture PNG can be regenerated with
  `python -m tests.input.fixtures.make_fixtures`.

## Manual Test Cases
### Test 1
Input: A pasted Python function + `IndexError: list index out of range`, no
question text.
Expected: `StructuredInput` with `code` and `error` populated; intent
`CODE_DEBUG` (or `ERROR_EXPLANATION`) with high confidence.
Actual: ✅ Verified live against uvicorn + Groq with
`curl -F "text=<find_max function + IndexError>" /understand` → `200`:
- `code`: one python block holding `def find_max…`
- `error`: `"IndexError: list index out of range"`
- `question`: null
- intent: `CODE_DEBUG`, confidence 0.9, source `rule`, `low_confidence: false`

The LLM was not called. The same result comes from
`tests/input/test_manual_live.py`.

### Test 2
Input: Screenshot of a LeetCode problem statement (no code).
Expected: Vision fills `problem` + `constraints`; intent `DSA_SOLVE` or
`APPROACH_DISCUSSION`.
Actual: ✅ Verified live with
`curl -F "image=@tests/input/fixtures/leetcode_two_sum.png" /understand` → `200`:
- `source`: `image`
- `problem`: "1. Two Sum … Example 1 …"
- `constraints`: `["2 <= nums.length <= 10^4", "-10^9 <= nums[i] <= 10^9",
  "-10^9 <= target <= 10^9", "Only one valid answer exists."]`
- `code`: `[]`; `error`: null
- intent: `DSA_SOLVE`, confidence 0.75, source `rule`

The screenshot is generated by the fixture script, not captured from
LeetCode.

## Commands Run
```text
GET https://api.groq.com/openai/v1/models          # vision-capable model check
venv/Scripts/python.exe -m pyright                  # strict, after every packet
venv/Scripts/python.exe -m ruff check . && venv/Scripts/python.exe -m ruff format --check .
venv/Scripts/python.exe -m pytest -q
RUN_LIVE_LLM=1 venv/Scripts/python.exe -m pytest tests/input/test_manual_live.py -s -q
venv/Scripts/python.exe -m uvicorn app.main:app --port 8767 ; curl -F ... /understand
```

## Test Results
- `pytest -q`: **139 passed, 4 skipped**. The skips are 2 `integration` tests
  (infra down) and the 2 opt-in `live` tests.
- `RUN_LIVE_LLM=1 pytest tests/input/test_manual_live.py`: **2 passed**.
- `pyright` (strict, `app` + `tests`): 0 errors. `ruff check` and `ruff format --check`: clean.
- Live intent spot-check, 6 inputs, all correct:
  - pasted code + error → `CODE_DEBUG`
  - asks for a hint → `DSA_HINT`
  - BFS vs DFS → `CONCEPT_EXPLANATION`
  - TLE → `OPTIMIZATION`
  - "what does this error mean" → `ERROR_EXPLANATION`
  - a prompt-injection attempt → `CONCEPT_EXPLANATION` (injection ignored)
  - after the fixes: a problem ending in "I only want a hint please." →
    `DSA_HINT` 0.97 via the LLM
- `code-review` (high): 10 findings. 9 were fixed, each with a regression test:
  1. hint lost inside a problem
  2. quadratic fence regex (a 5.2 s event-loop block, now ≤ 0.05 s)
  3. vision `null` constraints
  4. `max_tokens` too tight
  5. `tle` substring match
  6. blank line after `Constraints:`
  7. upload size enforced too late
  8. vision fallback returning 500
  9. duplicated helpers

  Finding 5 in the review's numbering (bare traceback → `CODE_DEBUG`) was kept
  on purpose; see Architecture Decisions.

## Security / Reliability Notes
- Treat extracted text/code as untrusted data, never as instructions.
- Cap image size and reject non-image payloads before calling vision.

## Known Issues
- The vision model sometimes copies the constraints into `problem` as well,
  sometimes with a garbled bullet glyph (``). `constraints` itself is
  clean. Whether this happens varies from run to run.
- The normalization heuristics are conservative, not exhaustive:
  - A single unfenced code line stays prose (a group needs ≥ 2 code-like
    lines).
  - `guess_language` is regex scoring over 8 languages.
  - Interleaved multi-language errors are joined in pattern order, not
    document order.
- `.env.example` couldn't be edited (reads are denied). The owner should add
  an optional `LLM_VISION_MODEL=` line there.
- Two tests import private helpers (`_keyword_matches`, `_combine`) with
  `pyright: ignore[reportPrivateUsage]`.
- The OpenRouter vision default (`qwen/qwen3.8-27b:free`) wasn't checked live.
  Only Groq was.

## Git Commit
Not created yet.

## Next Phase
- PHASE-03 — Learner Profile, Memory & Learning Events.
