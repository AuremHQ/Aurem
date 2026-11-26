"""
services/mode_routing.py — 2026-09-02, extended 2026-09-03 (Root 4,
core-flow round)

Auto-escalation: a genuine code/content edit (agentic tier) on Swift
should never land on the fast/cheap Swift model and produce a raw or
unreliable result; it should go to the reliable model (Pro/Claude).
A quick chat/query turn stays on the fast/cheap Swift model (GLM).

TWO config-gated paths, both fully implemented (founder flips via the
`EDIT_TIER_MODE` env var — no code change needed to switch):

  Path A — "transparent" — a real edit on Swift silently uses the
    reliable model regardless of the account's plan. No manual mode
    switch, no jargon shown to the user. The business absorbs the
    extra model cost. This was the ONLY path before 2026-09-03
    (founder's original connect-flow-refinement call: do NOT make Pro
    the blanket default, do NOT ask the user to manually switch
    modes — but that call predates the free/paid Swift-vs-Pro split
    being enforced here).

  Path B — "gated" (DEFAULT, 2026-09-03) — Swift is genuinely the
    free/starter tier's only mode (`subscription_tiers.TIER_LIMITS`:
    free and starter both have `modes: ["swift"]` only; pro/team/
    founder include "pro"). A free/starter account attempting a real
    edit gets an HONEST upgrade offer (`UPGRADE_OFFER_MESSAGE`) —
    never silently escalated (no surprise cost absorbed on their
    behalf), and never the "nothing pending" dead end either (a real,
    concrete next step is always offered). An account that ALREADY
    has Pro access (`account_has_pro=True` — they're paying for it,
    just left the Swift pill selected) is NEVER shown an upgrade
    offer in either path — they already own the capability, so this
    silently escalates exactly like Path A for them.

Both paths guarantee the same thing: NEVER a false "would you like me
to..." confirm with no real backing (Root 1), and NEVER a silent
"nothing happened" for a real edit request.
"""
from __future__ import annotations

import os

EDIT_TIER_MODE_TRANSPARENT = "transparent"  # Path A
EDIT_TIER_MODE_GATED = "gated"              # Path B (default)

# P1k — the user mode-pill vocabulary, shared by chat turns and Loop
# runs. Named in one place so the router, the engine rehydration,
# and the tests all validate against the same set.
MODE_PILLS = ("swift", "pro", "maxx")

UPGRADE_OFFER_MESSAGE = (
    "That's a real change to make — to get it applied correctly I "
    "need our more reliable editing model, which is part of the Pro "
    "plan. Want me to set up that upgrade, or would you like to keep "
    "browsing on the free plan for now?"
)


def edit_tier_mode() -> str:
    """Reads `EDIT_TIER_MODE` env var — "transparent" (Path A) or
    "gated" (Path B, default when unset/unrecognized)."""
    v = (os.environ.get("EDIT_TIER_MODE") or EDIT_TIER_MODE_GATED).strip().lower()
    if v not in (EDIT_TIER_MODE_TRANSPARENT, EDIT_TIER_MODE_GATED):
        return EDIT_TIER_MODE_GATED
    return v


def resolve_model_mode(tier: str, req_mode: str, *, account_has_pro: bool = False) -> str:
    """`tier` is the intent-gateway classification for THIS turn
    ("casual" / "clarify" / "query" / "agentic"). `req_mode` is the
    user's selected mode pill ("swift" / "pro" / "maxx").
    `account_has_pro` — True iff the caller's ACCOUNT tier already
    includes "pro" in `subscription_tiers.allowed_modes_for_tier`
    (i.e. not being asked to upgrade to something they don't already
    have). Defaults to False (FAIL-CLOSED, 2026-11-25 audit Part IX):
    a caller that forgets to pass it no longer silently escalates a
    free account to Pro — the mode stays Swift and the caller must
    show the upgrade offer instead. The sole production caller
    (routers/chat_pre_llm.py) passes the flag explicitly, so it is
    unaffected by the flip.

    Inputs are normalized (strip + lower) so "Agentic" / " Swift "
    behave like "agentic" / "swift" — mirroring edit_tier_mode().

    Returns the mode to actually pass to the model call — escalated
    to "pro" for a real edit (agentic tier) landing on Swift, EXCEPT
    when the account has no Pro access AND gated mode is active — in
    that case the mode stays "swift" and the caller must show
    `UPGRADE_OFFER_MESSAGE` instead of running the edit
    (`needs_edit_upgrade_offer` tells the caller when to do that)."""
    tier = (tier or "").strip().lower()
    req_mode = (req_mode or "").strip().lower()
    if tier != "agentic" or req_mode != "swift":
        return req_mode
    if account_has_pro or edit_tier_mode() == EDIT_TIER_MODE_TRANSPARENT:
        return "pro"
    return req_mode


def needs_edit_upgrade_offer(tier: str, req_mode: str, *, account_has_pro: bool = False) -> bool:
    """True iff this turn is a real edit (agentic) on the Swift pill,
    the account has no Pro access, AND gated mode (Path B) is active —
    the caller should show `UPGRADE_OFFER_MESSAGE` instead of running
    the edit attempt on Swift. Defaults to False (FAIL-CLOSED,
    2026-11-25 audit Part IX): an omitted flag now means "no Pro",
    so the offer fires instead of silently escalating. Inputs are
    normalized (strip + lower), mirroring edit_tier_mode()."""
    tier = (tier or "").strip().lower()
    req_mode = (req_mode or "").strip().lower()
    if account_has_pro:
        return False
    if tier != "agentic" or req_mode != "swift":
        return False
    return edit_tier_mode() == EDIT_TIER_MODE_GATED


def normalize_mode_pill(req_mode: str | None) -> str | None:
    """P1k — validate a user mode pill against the known vocabulary.

    Returns the lowercased pill when it is one of swift/pro/maxx,
    else None (absent, blank, non-string, or unknown value → unknown
    mode, which the Parliament gate treats as "full Council"). Pure,
    no I/O — the router and the engine rehydration both use it so a
    stale/forged pill can never reach `LoopEngine.mode` unchecked."""
    if not isinstance(req_mode, str):
        return None
    pill = req_mode.strip().lower()
    return pill if pill in MODE_PILLS else None


def resolve_loop_mode(req_mode: str | None, *, account_has_pro: bool = False) -> str | None:
    """P1k — resolve the user's mode pill for a Loop run.

    A loop run is always a real (agentic) code edit, so the pill goes
    through the SAME escalation `resolve_model_mode` already applies
    to agentic chat turns — swift + Pro access → pro; swift without
    Pro access in gated mode stays swift (the caller shows the upgrade
    offer instead; for loops the tiered gate in `routers/loop.py`
    already blocks free/starter starts, so that arm is defence in
    depth, not a new gate). Unknown/absent pill → None = full Council.
    No new mode system: this is a 4-line composition of the existing
    resolver with the loop's fixed tier.

    Defaults to False (FAIL-CLOSED, 2026-11-26 audit Audit-2 Prompt
    1, leak-close #4): this was the odd one out — its siblings
    `resolve_model_mode` and `needs_edit_upgrade_offer` already
    default False, while this defaulted True, silently escalating a
    free account's loop to Pro when the caller forgot the flag. The
    sole production caller (routers/loop.py) passes the flag
    explicitly, so it is unaffected by the flip."""
    pill = normalize_mode_pill(req_mode)
    if pill is None:
        return None
    return normalize_mode_pill(
        resolve_model_mode("agentic", pill, account_has_pro=account_has_pro)
    )
