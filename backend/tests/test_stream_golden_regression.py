"""
tests/test_stream_golden_regression.py — 2026-09-08 StreamState
refactor anti-regression gate.

Compares the 6 scenarios in _stream_golden_harness.SCENARIOS against
the golden reference captured pre-refactor
(tests/_golden/stream_refactor_2026_09_08.json, via
_capture_stream_golden.py). Wall-clock-derived fields
(thinking_s/elapsed_s/t_started) are normalized before comparison —
see _stream_golden_harness.py module docstring for why we don't fake
time.monotonic globally. Every other field — event order, event
types, content, mode, provider, council/verified_paths data — is
compared byte-for-byte via the normalized event list.

This is the anti-regression net for the worker.py/watchdog.py/
retries.py/context_build.py/sse_stream.py split. If this test is
green, the refactor did not change observable behavior for any of
the 6 covered code paths (orchestrator fallback, Mode-D fast-path,
Mode-F engage, confidence-mismatch retry, soft timeout, hard timeout).

2026-09-08 · Turn-Completion Contract re-record — this round's fix
(root-caused 3 separate silent-idle bugs to one contract violation;
see orchestrator.py's module comment above _is_intent_only_announcement)
is an INTENTIONAL behavior change, not a mechanical refactor: the
final `done` event on all 6 scenarios now also carries `turn_state`,
`continue_available`, `error`, `failure_mode`, `recovery_action`.
Verified before re-recording that this was the ONLY diff (no other
field/event changed on any of the 6 scenarios) — golden re-captured
via `python -m tests._capture_stream_golden` to reflect the new
contract fields as the new baseline.

2026-09-22 · INTERACTION_CONTRACT_V1 activated (P1 follow-up round:
4-clock timeout tracker + idempotent-retry ledger + stream-state
enum — see services/timeout_clocks.py, services/chat_reliability.py).
Two more INTENTIONAL, verified diffs, both re-recorded:
  1. Every `meta` frame now also carries `request_id` — needed so the
     frontend's "Retry" action can reference the exact attempt it is
     retrying. Random per request by design; stripped from comparison
     in normalize_events() rather than baked into the fixture.
  2. The final `done` event's `interaction` field went from `None`
     (the flag was OFF during every prior golden capture) to the real
     envelope (`turn_state`, `request_hash`, `content_hash`, ...) now
     that `INTERACTION_CONTRACT_V1=1` is the active configuration —
     this machinery (services/interaction_contract.py) already
     existed pre-2026-09-22; only its activation is new here. Its own
     nested `request_id` is likewise random-per-run and stripped.
  Verified before re-recording that these 2 were the ONLY diffs (ran
  with -vv, confirmed every other field/event on all 6 scenarios was
  byte-identical to the pre-activation golden) — re-captured via
  `python -m tests._capture_stream_golden`.

2026-09-29 · User-facing provider branding re-record. Two more
INTENTIONAL, verified diffs, both from services/user_response_sanitizer.py
(the engine/provider is never shown to users):
  1. `provider` on every `meta`/`done` frame is now the branded "ORA"
     instead of the raw provider id ("aurem-cto", "golden-provider", ...).
  2. `llm_provenance` is now `{"is_emergency": bool}` — provider/model
     are dropped, never forwarded raw.
  Verified with INTERACTION_CONTRACT_V1=1 that these were the ONLY diffs
  on all 6 scenarios (soft_timeout/hard_timeout: provider only) —
  re-captured via `python -m tests._capture_stream_golden`. The test now
  pins INTERACTION_CONTRACT_V1=1 itself, since the golden assumes it.
"""
import json
import os

import pytest

from tests._stream_golden_harness import SCENARIOS, normalize_events, parse_events

GOLDEN_PATH = os.path.join(os.path.dirname(__file__), "_golden",
                           "stream_refactor_2026_09_08.json")

with open(GOLDEN_PATH, encoding="utf-8") as _fh:
    _GOLDEN = json.load(_fh)


@pytest.mark.parametrize("scenario_name", sorted(SCENARIOS.keys()))
def test_stream_matches_golden(scenario_name, monkeypatch):
    # The golden was captured with the interaction contract active (see
    # the 2026-09-22 note above); pin it so the result never depends on
    # which CI job or shell happens to export the flag.
    monkeypatch.setenv("INTERACTION_CONTRACT_V1", "1")
    runner = SCENARIOS[scenario_name]
    actual_text = runner(monkeypatch)
    actual = normalize_events(parse_events(actual_text))
    golden = normalize_events(parse_events(_GOLDEN[scenario_name]))
    assert actual == golden, (
        f"stream.py refactor changed observable behavior for "
        f"scenario '{scenario_name}' — event sequence no longer "
        f"matches the pre-refactor golden reference."
    )
