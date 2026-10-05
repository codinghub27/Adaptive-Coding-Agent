# Merge checklist: `experimental` into `main`

State on 2026-10-05. Reliability numbers and what is and is not within the
merge bar are in `docs/AUDIT_REPORT.md` section 11. Read that first: this file
is the mechanics.

## What a merge brings in

`origin/main` is at `45e0152`. Merging `experimental` brings every commit below,
because local `main` was never pushed past that point.

### Eight commits already on local `main`, not on `origin/main`

| Commit | Summary | Risk |
|---|---|---|
| `8baab46` | UI: structured response sections with icons, working-process panel | Frontend only. Needs a rebuild of `frontend/dist` to be visible |
| `ce6e728` | Test instrument: live replay of the five reference conversations (`eval/behavior/`) | None at runtime. Adds eval code and fixtures |
| `94d00eb` | Tutoring loop: graded questions, conceptual evidence, misconceptions, code-submission review (52 files, +4589) | **Largest change. Carries migration `b5c6d7e8f9a0`** (adds `conversations.pending_check`, `conversations.session_progress`, `learning_events.concept_grade`, `learner_profiles.common_errors_seen`; additive, nullable or defaulted) |
| `0cb2427` | Screenshot + "how do I solve this" is solved, not clarified; spinner fix | Low. Routing rule plus CSS |
| `c51b1da` | "chore: checkpoint before ollama migration" (16 files, +374) | **Read before merging.** A checkpoint commit: a mixed bag of work in progress with a message that does not describe it. Nothing in it is Ollama-specific |
| `a56da35` | Port of the model-independent tutoring fixes from the local-model experiment | Medium. Touches routing, grading and the DSA subgraph; covered by the replay |
| `0087daf` | Docs: cloud replay numbers after the port | None |
| `b031699` | "hi agent" is a greeting, not a study-plan request | Low |

### On `experimental` only

| Commits | What |
|---|---|
| `09824d2` | The uncommitted work found on `fix/conversation-memory-tutor` when round 1 began (ERR-005 to ERR-007) |
| `957684d` to `19c1b23` | Round 1: audit, code-first routing, snippet repair |
| `1acc9dd` to `3c47fe6` | Round 2: classifier sees the conversation, owner decisions A-10 and A-15, suite cache (**migration `c6d7e8f9a0b1`**) |
| `54e0982` to `6a5877f` (and the docs commit after it) | Round 3: tree and linked-list execution, provider telemetry and failover changes, curated examples as test suites, the repeatable scenario runner |

`experiment/local-ollama` is NOT part of this. It diverged before round 1 and
none of rounds 1 to 3 has been ported to it.

## Before merging

0. **Finish the reliability run.** The five-runs-per-scenario measurement was
   not completed (the Groq quota ran out; audit report section 11). On a day
   with quota, with the stack and the API up:

   ```powershell
   .\venv\Scripts\python.exe -m eval.behavior.live_scenarios --runs 5 --pace 12 --base-url http://127.0.0.1:8000
   .\venv\Scripts\python.exe -m eval.behavior.replay --pace 10 --base-url http://127.0.0.1:8000
   ```

   The first exits 1 if any check is below the bar. Do not merge on the
   partial numbers alone.

1. `git fetch origin` and confirm `git log main..origin/main` is still empty.
2. Decide how to merge. A merge commit keeps the history above. A squash loses
   the per-round commits the audit report cites by hash.
3. Back up the database if it holds anything you care about
   (`docker compose exec postgres pg_dump -U coding_agent coding_agent_db`).

## Migrations

Two, both additive. No column is dropped or retyped and no data is rewritten.

```powershell
.\venv\Scripts\python.exe -m alembic upgrade head     # ends at c6d7e8f9a0b1
.\venv\Scripts\python.exe -m alembic check            # "No new upgrade operations detected."
```

| Revision | Adds | Downgrade |
|---|---|---|
| `b5c6d7e8f9a0` | Four columns for the tutoring loop | Drops them (loses pending questions and session progress) |
| `c6d7e8f9a0b1` | Table `test_suite_cache` (per learner and subject) | Drops the table (a cache: nothing is lost that cannot be rebuilt) |

A database already used with `experimental` is at head and needs nothing.

## Environment

No variable became required and no dependency was added (`requirements.txt` is
unchanged against `origin/main`).

| Variable | Default | Set it when |
|---|---|---|
| `LLM_HTTP_LOG_PATH` | unset | You want one JSON line per provider HTTP call: status, seconds, rate-limit headers. Metadata only, never keys or prompts. The file grows without bound; do not leave it on |
| `LLM_CLASSIFIER_MODEL` | unset | You want intent classification on a smaller Groq model. See section 11 of the audit report for the measured accuracy before turning it on |

`.env.example` was not updated: it sits behind a permission rule in this
workspace. Add the two lines by hand if you want them documented there.

Behaviour change with no setting: when more than one LLM credential is
configured, the Groq SDK's own retries are off, so a rate-limited key fails
over to the next one immediately. With a single key nothing changes.
When every key for the main model is throttled for 15 s or less, the call waits
that long once and retries there before using the fallback provider, and the
failover returns to the first key after 60 s (was 300 s).

## Docker

`docker-compose.yml` now sets `restart: unless-stopped` on Postgres and Qdrant.

- Existing containers do not pick that up from the file. Either
  `docker compose up -d --force-recreate` (data volumes are kept), or
  `docker update --restart unless-stopped adaptivecodingagent-postgres-1 adaptivecodingagent-qdrant-1`.
  The second was already run on this machine.
- The sandbox image `aca-sandbox:py3.11-v1` is unchanged. Tree and linked-list
  support is added to the code sent to the sandbox, not to the harness, so no
  rebuild is needed.
- If the server logs `knowledge dense retrieval failed: ... (ConnectError)`,
  Qdrant is down. `GET /health` shows it.

## Automated checks on the final commit

```powershell
docker compose up -d
.\venv\Scripts\python.exe -m pytest tests -q
.\venv\Scripts\python.exe -m pyright app tests
.\venv\Scripts\python.exe -m ruff check .
.\venv\Scripts\python.exe -m ruff format --check .
.\venv\Scripts\python.exe -m alembic check
```

Results are recorded in section 11 of the audit report.

## Manual checks

Start the stack, start the API, rebuild the frontend (`pnpm build` in
`frontend/`), and in the browser:

1. **Beginner, no code on turn 1.** "I'm new to DSA. Can you help me solve Two
   Sum? I don't understand how to start." Expect one guiding question and no code.
2. **Explicit ask.** In the same chat: "show me the solution". Expect code with
   an Execution line. If Docker is stopped, expect the same code under "Not
   verified in sandbox".
3. **Challenge mode.** Switch to Challenge, ask for a hard graph problem, then
   "give me the code". Expect a refusal that says why.
4. **Your code first.** Paste the `current-behavior.md` snippet with "fix this
   code". Expect the IndexError, the `left -= 1` line by ITS number in your
   paste, and a fix.
5. **A tree problem.** Paste a Max Path Sum attempt. Expect a real pass/fail
   count, not "could not be checked".
6. **Follow-ups.** After sharing a problem: "what was that problem called
   again?", then share a second problem and say "go back to the earlier one".
7. **Hint card.** The hint text appears once, not twice (round 1 fix A-07; this
   has no automated test).
8. **Second session.** New chat, same account, a problem on a topic where you
   made a mistake earlier. Expect "you ran into this earlier".
9. **Rate limits.** Watch the server log during a few minutes of use. A burst
   of `llm rate limited` warnings means the keys are throttled; turns will be
   slow and a code reveal may come back "did not return usable code".

## Not done, and worth knowing before you merge

- Reply text is not streamed token by token. The reply is assembled from
  validated JSON after the model call (that validation is what keeps code out
  of a hint), so there are no tokens to forward. See the audit report.
- Skill thresholds were not changed. A proposal is in the audit report and
  needs your decision.
- One earlier subject is remembered per conversation, not a history.
