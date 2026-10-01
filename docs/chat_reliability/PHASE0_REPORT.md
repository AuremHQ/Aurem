# ORA chat reliability — Phase 0 report (reproduce and confirm, no fixes)

Scope: the 11-turn production transcript from the owner's test on
`AuremHQ/secure-team-workspace`. Nothing in this PR changes product code.
It adds one test file that reproduces the problem:
`backend/tests/test_chat_reliability_replay_phase0.py`.

## How it was reproduced

The test feeds the 11 user messages, in order, through the real pipeline
on both endpoints — `routers/chat/turn.py::chat_send` and
`POST /api/aurem-dev/chat/stream`. Only the LLM is stubbed, to the exact
texts ORA produced. Routing, intent tiers, output guards, the
business-voice filter, the interaction contract and session state are the
real code (Mongo is `mongomock`).

Run: `cd backend && python3 -m pytest tests/test_chat_reliability_replay_phase0.py -rx`
Result today: `14 xfailed` (7 checks x 2 endpoints). Run with `--runxfail`
and each one fails on its own assertion, not on a harness error. Each is
`xfail(strict=True)`, so the PR that fixes a problem has to delete its
marker. This adds no new CI failure.

Per-turn output (identical on `chat_send` and `chat_stream`):

| # | User | Tier | Label shown | What came out |
|---|------|------|-------------|---------------|
| 1 | hi | casual | Response delivered | "Clean bill of health on the scans…" (no scan record exists) |
| 2 | hi | casual | Response delivered | "That checklist **Review request** is ready…" (PR -> "Review request") |
| 3 | show me issues | query | Response delivered | "I tried to pull your GitHub issues but the call failed…" |
| 4 | ? | casual | Response delivered | generic reply ending "Short and simple!" |
| 5 | any issue in my this project | clarify | Response delivered | docs lookup; both paths became "**that file** was not found" |
| 6 | docs/OPERATIONS.md build it | clarify | Incomplete | "I'll create a fresh **that file**…" + narration + one partial notice |
| 7 | Please continue… | casual | Response delivered | draft in a code block whose 15 lines are empty numbers |
| 8 | continue | casual | Response delivered | same, 13 empty lines |
| 9 | continue next | casual | Response delivered | code fence never closed; `` `docs/OPERATIONS.md` `` -> `` `that file` ``; fence tag `markdown` -> `text` |
| 10 | next ? | casual | Response delivered | "I'll submit the full draft as a **Review request**… (Standby — building that now...)" |
| 11 | ok | casual | Response delivered | "I don't have a change waiting right now…" |

## Verdicts

### H1 — talks instead of acting: CONFIRMED
- `services/orchestrator.py:284` `_is_intent_only_announcement` decides by
  phrase (`_INTENT_LEAD_RE`, `_STALL_TELL_RE`) and a length cap (<220 chars).
  Run on the 11 ORA texts it fires on turn 6 only. Turn 10 ("Let's finish…
  Standby — building that now...") is not caught: "Standby" is not
  "stand by".
- It never looks at tool results, so there is no check that something
  actually happened. Anything that merely sounds finished closes as
  "delivered".
- `services/chat_helpers.py:184-211` adds the same notice again behind a
  marker check. In this replay the notice printed once. The owner saw it
  twice on production. **Not reproduced here; cause unknown, NOT VERIFIED.**

### H2 — output filter corrupts text: CONFIRMED (one sub-claim not reproduced)
- `services/output_guard.py` (`_FILE_PATH_PATTERN`) and
  `services/business_voice_filter.py` (`_FILENAME_RE`, `_DEFAULT_FILE_TERM
  = "that file"`) rewrite paths in plain text and inside inline code.
  Reproduced on turns 5, 6, 7, 9.
- `config/business_voice_map.json` maps "pull request" -> "Review request"
  (turns 2, 10).
- Turn 9's fence is never closed, so `_CODE_FENCE_RE` (`business_voice_filter.py:185`, needs a closed ``` pair) does not shield it and the word map rewrites the tag `markdown` -> `text` and the path inside inline code.
- `output_guard.strip_machinery_leak` is a second path rewriter: the same text becomes "a project file" there (checked directly).
- **Empty code blocks (turns 7, 8): the stub text had empty numbered lines,
  and the filters did not empty any block that had content.** So the
  empty lines come from the model, not the filter. The pipeline still
  delivers them untouched and labels it "Response delivered", which is the
  gap PR B closes.
- "Short and simple!" does not exist anywhere in the repo (case-insensitive
  search of `.py/.md/.json/.txt`). It came from the model, probably from
  context outside this repo (recalled past answer, user memory or
  preferences). **Source NOT VERIFIED.** A fix by adding this phrase to a
  blocklist would be wrong (R5); PR B must address it structurally.

### H3 — wrong routing, lost state: CONFIRMED
- Short messages ("?", "continue", "next ?") are classed `casual` by
  `core/intent_gateway.py` (`_classify_heuristic`), which takes only
  `pending_fix` as state, so they never see the draft.
- "ok" with nothing stored goes to
  `services/actions/pending_action.py:84,765` (`NO_PENDING_ACTIONABLE_MESSAGE`),
  and `routers/chat_pre_llm.py:334` has a second no-pending path. Nothing
  stored the turn-6 draft as a pending change, so "ok" had nothing to
  approve. Reproduced on turn 11.
- "any issue in my this project" (turn 5) reached the docs-lookup path as
  tier `clarify`, not project findings.

### H4 — claims with no evidence: CONFIRMED
- Turns 1-2 come from `casual_direct_reply` (`services/intent_gateway_casual_reply.py`):
  a tool-free LLM reply with no check against stored scan or PR records.
  In the replay DB there is no scan and no PR, and the reply is delivered
  anyway. Whether production has such records is NOT VERIFIED.

### H5 — draft content not grounded: PARTLY
- No repo read is forced before a "build it" draft, so the draft can't be
  grounded. Whether the production model had read the repo before turn 6
  is NOT VERIFIED (needs the production transcript's tool calls).

### H6 — tool failures: CONFIRMED
- `services/error_translator.py:50`: the pattern `401|invalid token|bad
  credentials` returns "GitHub rejected the App installation for this
  repo…" for any error text. Real output:
  `_static_match('OpenRouter HTTP 401: No auth credentials found')` ->
  "GitHub rejected the App installation…". So an LLM-provider auth failure
  is shown as a GitHub App problem.
- Turn 3's single failed call gets no retry in this path. Whether the
  production failure was transient: NOT VERIFIED.

### H7 — silent monitoring: PARTLY
- `services/ora_chat/canary.py` drives the admin ORA Chat (`/sessions`,
  `/message`), not the project chat that the owner tested, so it can't see
  these defects.
- In `run_canary`, an empty reply only fails the check for `challenge` and
  `meta_gaps`. For other prompts (e.g. `codebase_specific`) empty text
  leaves `ok` true, so no alert. That fits "no assistant reply for two
  days" without alarm. Why production's reply was empty: NOT VERIFIED.

## Production relevance

- These paths are on `main` today. Production's build may be older: the
  live `build_hash` has not been checked (needs a `curl /api/health`
  from the owner; NOT VERIFIED).
- `INTERACTION_CONTRACT_V1` is opt-in (`services/interaction_contract.py:21`).
  The replay runs with it on, because that is what produces the labels in
  the transcript. Whether production has it on: NOT VERIFIED.
- `checkup_flow`, `fix_list_panel`, `chat_clean_ui`, `fix_loop_v2` are
  default OFF; none of the findings above depend on them.
- Not verified at all here: real LLM behaviour, real GitHub calls, the
  frontend rendering. The replay stubs the LLM, so it proves what the
  pipeline does to a given text, not what the model will say.

## Fix plan (Phase 1, one PR each)

A outcome decided by evidence · B output filter never damages content ·
C routing and state · D "create a file" end to end · E tool-failure
handling · F canary and metrics. Each PR deletes the matching `xfail`
markers in this file.
