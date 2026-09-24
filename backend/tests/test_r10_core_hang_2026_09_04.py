"""
test_r10_core_hang_2026_09_04.py

FOCUS MODE (2026-09-04) — R10: root-caused + fixed 2 of the 3 stacking
bugs behind the "check my page" ~90s hang / wrong-answer symptom,
live-reproduced via curl (raw SSE, timestamped) and the real browser
against project p_85cab6b777 (R2-live-drill-ora-grounding).

PROVEN root causes (see OVERNIGHT-LOG.md's R10 entry for the full
timestamped trace):

  Bug #2 — core/intent_gateway.py's heuristic tier classifier had NO
  entry for "check" in `_AGENTIC_VERBS` (its exact synonyms "scan"/
  "audit"/"review" WERE already there). "check my page" (3 words, no
  recognized verb) fell through to the length-based TIER_CASUAL bucket
  and got a completely ungrounded, fabricated reply ("Got it! Just
  scanned your page — looks good") from services/intent_gateway_
  casual_reply.py, which has ZERO real page content — the real
  orchestrator/tool-calling pipeline was never invoked at all.
  Live-reproduced via `POST /chat/stream` (curl, no browser): tier=
  "casual", confidence=0.86, total round-trip ~2-4s (fast, but wrong
  — an instant confabulation, not a real check).

  Bug #1 — services/chat_helpers.py's A-F mode classifier routes to
  Mode D (debug) unconditionally whenever ANY f12_payload network
  error is present, with NO check that the error's URL actually
  belongs to the CURRENT project. A stale, cross-project captured
  browser error (e.g. a `/cto/projects/p_demo_a/warm-start` 403 sitting
  in the F12 buffer from a PREVIOUSLY visited, unrelated project)
  hijacked an unrelated "check my page" turn on a DIFFERENT project
  into debugging THAT stale error instead. response_confidence.py's
  mismatch-retry safety net catches this ~50% of the time (wasting
  ~15-20s on a full re-run) but the retry can inherit the same
  cross-project pollution and double-mismatch into a dead-end
  "I don't actually have that open in front of me" bail message.
  Live-reproduced via the real browser (test@aurem.dev, project
  R2-live-drill-ora-grounding): backend log showed a hallucinated
  answer about "GitHub API returned a 404 when minting an App
  installation token" (an unrelated project's real captured error) in
  response to "check my page", followed by
  "chat_stream: mismatch detected on first response — retrying".

Bug #3 (a genuinely slow ~60s single-model-call / background-job gap
observed in a 3rd repro) is NOT fixed here — flagged in the round's
report for further investigation, no code change made for it (kept
scope to the 2 proven, bounded, low-risk fixes above per the founder's
explicit "no while-you're-here fixes" instruction).
"""
from core.intent_gateway import classify_heuristic_sync, TIER_AGENTIC, TIER_CASUAL
from services.chat_helpers import _f12_has_real_signal, classify_intent


def test_check_my_page_is_agentic_not_casual():
    result = classify_heuristic_sync("check my page")
    assert result["tier"] == TIER_AGENTIC
    assert "check" in result["reasoning"].lower() or "agentic_lead_verb:check" in result["signals"]


def test_check_synonyms_all_agentic():
    for verb in ("check", "review", "audit", "inspect", "verify", "test"):
        result = classify_heuristic_sync(f"{verb} my page")
        assert result["tier"] == TIER_AGENTIC, f"{verb!r} should be agentic, got {result['tier']!r}"


def test_genuinely_casual_messages_still_casual():
    # Guard against over-correcting — plain greetings must stay casual.
    for msg in ("hi", "thanks!", "ok cool", "hey there"):
        result = classify_heuristic_sync(msg)
        assert result["tier"] == TIER_CASUAL, f"{msg!r} should stay casual, got {result['tier']!r}"


def test_f12_network_error_from_different_project_is_ignored():
    payload = {
        "network_errors": [
            {"status": 403, "url": "https://x.example.com/api/aurem-dev/cto/projects/p_demo_a/warm-start",
             "response_body": "GitHub App auth failed"},
        ],
    }
    assert _f12_has_real_signal(payload, project_id="p_85cab6b777") is False
    # Without a project_id filter (old behavior — e.g. a caller that
    # genuinely can't know the current project yet), it's still a
    # real signal.
    assert _f12_has_real_signal(payload) is True


def test_f12_network_error_from_same_project_still_real_signal():
    payload = {
        "network_errors": [
            {"status": 403, "url": "https://x.example.com/api/aurem-dev/cto/projects/p_85cab6b777/warm-start",
             "response_body": "GitHub App auth failed"},
        ],
    }
    assert _f12_has_real_signal(payload, project_id="p_85cab6b777") is True


def test_classify_intent_does_not_force_mode_d_for_cross_project_f12():
    payload = {
        "network_errors": [
            {"status": 403, "url": "/api/aurem-dev/cto/projects/p_demo_a/warm-start",
             "response_body": "GitHub App auth failed"},
        ],
    }
    mode = classify_intent("check my page", payload, "p_85cab6b777")
    assert mode != "D"
