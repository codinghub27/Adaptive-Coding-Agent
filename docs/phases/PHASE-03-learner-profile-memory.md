# PHASE-03 — Learner Profile, Memory & Learning Events

## Status
Not Started

## Goal
Give the agent memory: a structured, evolving learner profile, conversation
memory, and a learning-event system that updates the profile from observed
activity (not guesses).

## Scope
- `app/db/models/` — SQLAlchemy models: `User`, `Conversation`, `Message`,
  `LearnerProfile`, `LearningEvent`.
- `app/memory/profile.py` — read/update learner profile: `skill_levels` per
  topic (0–1), `learning_preferences`, `common_errors`.
- `app/memory/conversation.py` — persist + fetch recent conversation memory
  (short-term context window assembly).
- `app/memory/events.py` — record a `LearningEvent` per interaction and apply a
  deterministic profile-update rule (e.g. EWMA on skill levels).
- Alembic migration for the new tables.

Profile shape (target):
```json
{
  "language": "Python",
  "skill_levels": { "arrays": 0.75, "sliding_window": 0.55, "dp": 0.30 },
  "learning_preferences": { "prefers_hints": true, "likes_step_by_step": true,
                            "wants_line_by_line_explanations": true },
  "common_errors": ["off_by_one", "incorrect_window_shrinking", "edge_cases"]
}
```

Learning event shape (target):
```json
{ "problem": "...", "topic": "arrays", "pattern": "sliding_window",
  "difficulty": "easy", "requested_help": "explanation", "hints_used": 2,
  "needed_full_solution": false, "errors": [], "solved": true,
  "time_spent": 12, "concepts": ["Chebyshev distance"] }
```

## Out of Scope
- Deciding *how* to teach from the profile (Teaching Planner, Phase 04).
- Fine-tuning any model — explicitly not doing this; profile + retrieval only.
- Future phases.

## Current Implementation
Document only what actually exists after inspecting the repository.

## Planned Changes
1. Add SQLAlchemy models + Alembic migration.
2. Implement profile read/update with a documented, deterministic update rule.
3. Implement conversation persistence and a `get_recent_context()` assembler.
4. Implement learning-event creation and profile application; make it idempotent
   per event id.
5. Tests: profile update math, event → profile propagation, context assembly.

## Files Expected
- Create: `app/db/models/{user,conversation,message,profile,event}.py`,
  `app/memory/{__init__,profile,conversation,events}.py`,
  `app/schemas/{profile,event}.py`, `alembic/versions/xxxx_memory.py`,
  `tests/memory/test_profile.py`, `tests/memory/test_events.py`
- Modify: `app/db/base.py` (register models)
- Delete: None unless explicitly approved

## Architecture Decisions
- Skill levels updated by a smoothing rule (record chosen α) so a single
  interaction never swings the profile hard.
- Learning events are the source of truth; the profile is a derived projection
  that can be rebuilt from events.

## Dependencies
- PHASE-01 (DB/session), PHASE-02 (intent + structured input feed events).

## Implementation Notes
Keep this section short. Record the update rule/α and the event→topic mapping.

## Manual Test Cases
### Test 1
Input: Record a solved `sliding_window` event with `hints_used: 0`.
Expected: `sliding_window` skill level increases; event stored; re-applying the
same event id does not double-count.
Actual:

### Test 2
Input: Two turns in one conversation, then `get_recent_context()`.
Expected: Returns both turns in order within the configured window.
Actual:

## Commands Run
```text
# commands
```

## Test Results
-

## Security / Reliability Notes
- Scope every read/write to the authenticated user id; no cross-user reads.
- Store only learning signals, not raw sensitive content beyond what's needed.

## Known Issues
-

## Git Commit
Not created yet.

## Next Phase
- PHASE-04 — LangGraph Orchestration & Teaching Planner.
