"""
test_regression_iter5_invariants_job_has_real_llm_key.py

Real bug found while diagnosing quality-gate.yml's "Fitness-function
invariants" job (2026-09-29): no OPENROUTER_API_KEY / EMERGENT_LLM_KEY
was set anywhere in that workflow, and MOCK_LLM defaults to "false"
(services/ora_chat_v2/llm_client.py::_MOCK_LLM_AT_BOOT). On a
genuinely fresh checkout (no local .env), every test in this job that
needs a real chat reply — casual hello, greeting-alone, 8-turn chat
history, loop-trip incident log, commit-boundary regression,
prompt-caching max-tool-iters — got an empty reply or a 499
"client disconnected or upstream error", always.

Tried MOCK_LLM=true first: confirmed live it fixed those 6, but broke
a DIFFERENT set (test_intent_gateway_points_4_6_2026_01.py's 3
intent-classification tests, test_b3_gate_stream.py, 2 of
test_loop_gate_parity_and_mode_d_2026_01.py's chat-stream-logging
tests) — the canned mock reply can't distinguish "casual hello" from
a tool request the way a real model does, so intent-routing
assertions fail differently. A real LLM key is the only fix that
satisfies both classes; ci.yml already has this exact pattern for its
"Simulated-user QA" job (`${{ secrets.OPENROUTER_API_KEY }}` /
`${{ secrets.EMERGENT_LLM_KEY }}` on the boot step), so the invariants
job's boot step now references the same repo secrets.

Fix: OPENROUTER_API_KEY / EMERGENT_LLM_KEY (from `secrets.*`) added to
the "Boot backend in background" step's env for the invariants job —
read once at backend import time, so it must be on the BOOT step, not
the pytest step below it.
"""
from __future__ import annotations

_WORKFLOW = "/app/.github/workflows/quality-gate.yml"


def _invariants_job_boot_step_env() -> str:
    """Slice out the "Boot backend in background" step that precedes
    "Run regression + invariant tests" (there are 3 such boot steps
    in this file, one per job — this isolates the right one)."""
    src = open(_WORKFLOW).read()
    marker = "Run regression + invariant tests"
    invariants_idx = src.find(marker)
    assert invariants_idx > -1, f"{marker!r} step not found in {_WORKFLOW}"
    # The boot step immediately precedes it — walk back to the nearest
    # earlier "Boot backend in background" step name.
    boot_idx = src.rfind("Boot backend in background", 0, invariants_idx)
    assert boot_idx > -1
    return src[boot_idx:invariants_idx]


def test_regression_iter5_invariants_boot_step_has_real_llm_key():
    block = _invariants_job_boot_step_env()
    assert "secrets.OPENROUTER_API_KEY" in block or "secrets.EMERGENT_LLM_KEY" in block, (
        "the invariants job's backend-boot step must reference a real "
        "LLM key from secrets.* — without one, every live chat-reply "
        "test gets an empty reply / 499, and MOCK_LLM=true trades that "
        "for breaking intent-classification tests instead"
    )
    assert 'MOCK_LLM: "true"' not in block, (
        "MOCK_LLM=true was tried and confirmed to break "
        "test_intent_gateway_points_4_6_2026_01.py — do not re-add it "
        "as a shortcut without re-verifying that class of test"
    )
