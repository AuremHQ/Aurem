"""
routers/chat/stream_state.py — shared per-request state for the
chat_stream() SSE pipeline (StreamState refactor, 2026-09-08).

Replaces the ~20 variables that used to live as closures shared
between chat_stream()/gen()/_ticker()/_worker() with one explicit
object threaded through context_build.py / worker.py / watchdog.py /
retries.py / sse_stream.py. Mechanical de-closuring — no behavior
change; every field here is read/written at the exact same logical
point the old closure variable was.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional

from fastapi import Request

from .turn import ChatBody


@dataclass
class StreamState:
    # ── Request-scoped, set once by context_build.py ──
    request: Request
    body: ChatBody
    authorization: Optional[str]
    user: dict
    user_id: str
    jwt_token: str
    pid_stream: str

    # ── Context built before gen() (immutable once set) ──
    bin_ctx: Any = None
    repo_ctx: str = ""
    brain_ctx: str = ""
    extra_sys: str = ""
    council_recalled: int = 0
    council_block: str = ""
    recall_mode: Optional[str] = None
    plain_english_active: bool = False
    is_founder: bool = False

    # ── gen()-scoped runtime primitives. `default_factory` guarantees
    # a NEW asyncio.Queue/Event per StreamState instance — sharing
    # either across concurrent streams would interleave/corrupt
    # unrelated chats. Never pass shared instances in here. ──
    q: asyncio.Queue = field(default_factory=asyncio.Queue)
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    activity: dict = field(
        default_factory=lambda: {"label": "thinking…", "invocations": []})
    t_start: float = 0.0
    hard_timeout_s: float = 180.0
    soft_timeout_s: float = 48.0
    # INTERACTION_CONTRACT_V1 P1 — 4-clock timeout tracker (see
    # services/timeout_clocks.py). None when the flag is OFF; the
    # legacy hard/soft-timeout pair above stays the sole mechanism in
    # that case, unchanged. Also used as the durable ledger's request
    # identity carrier so watchdog.py can record a terminal ledger
    # state without needing extra params threaded through it.
    timeout_clocks: Optional[Any] = None
    interaction_request_id: str = ""

    # ── worker.py output, read by sse_stream.py's cost-logging call
    # after the worker completes ──
    sys_for_advisor: str = ""
    # Part B3 — required_read_gate (services/required_tool_gate.py).
    # 2026-02 round-4 — set at the very TOP of worker.py's run_worker(),
    # BEFORE any of its mode-dispatch branches (Mode D/E/B/F/ORA-panel/
    # orchestrator) run, so it's populated no matter which branch ends
    # up producing `state.result` (confirmed live: the exact incident
    # message can non-deterministically land in either Mode D's debug
    # session or the general orchestrator path — both must be covered).
    # Read by stream.py's own enforce_required_read_gate() call AFTER
    # run_confidence_gate / apply_output_guards / business_voice_filter
    # — mirrors turn.py's identical last-step placement (worker.py's
    # OWN enforcement call runs too early, before those 3 guards,
    # which can undo it).
    required_read_gate: Optional[dict] = None

    # 2026-02 — Context-Confusion fix Part A. Set alongside
    # `_pre = await resolve_pre_llm(...)` in worker.py's tool-cascade
    # entry function, read by `_run_tools_cascade`'s own downstream
    # tool-orchestrator call further down in the same file so Part
    # C's scope-gate is seeded even on the streaming path (not only
    # turn.py's non-streaming /chat/send).
    task_state: Optional[dict] = None

    # Trust & Verification round — Enhancement 5 (pre-execution scope
    # confirmation). Stashed by _mode_broadcast() in worker.py so the
    # gate that runs right after it can read the SAME classify_intent_v2
    # confidence/needs_confirm signal that was already computed for the
    # "mode" SSE badge, instead of recomputing it a second time.
    mode_confidence: Optional[dict] = None

    # ── watchdog.py output, read by sse_stream.py after the queue-
    # consumption loop ends ──
    result: Any = None
    collected_steps: list = field(default_factory=list)
    timed_out: bool = False

    # ── retries.py output, read by sse_stream.py after the
    # confidence-mismatch gate runs ──
    low_confidence: bool = False
    ship_suppressed: bool = False
    bail_reason: Optional[str] = None
    prior_fix_signal: Any = None

    def __post_init__(self):
        # Cheap per-instance guard — see class docstring.
        assert isinstance(self.q, asyncio.Queue), "q must be per-instance"
        assert isinstance(self.stop_event, asyncio.Event), (
            "stop_event must be per-instance")
