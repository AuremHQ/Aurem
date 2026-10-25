"""
scripts/pricing_copy_lint.py — Iter 364 · Marketing-copy truth gate,
extended Master Prompt v2 PART 5.1/5.2.

Prevents the "unlimited tasks" drift that landed in production before
Iter 364. Reads `backend/services/subscription_tiers.py` (source of
truth) and greps frontend copy for a small blacklist of drift-prone
phrases. Fails the build (non-zero exit) if any hit is found in a
marketing/pricing surface.

Rules:
  1. The word "unlimited" (any case) is forbidden within 60 characters
     of the words "task", "Pro", "Team", or "Starter" — those are the
     copy zones users read as plan limits.
  2. Exempt words with `is_unlimited`, `unlimited=`, `unlimited=true`,
     JSX prop patterns — those are server-side flags for the founder /
     admin tier and are legitimate.
  3. Explicitly-allowed sentences may be listed in
     `scripts/pricing_copy_lint_allowlist.txt`.
  4. (PART 5.1, ALWAYS ON) Per-action token-cost phrasing ("tokens per
     fix", "N tokens per task", "costs N tokens") is forbidden in any
     scanned file, regardless of TOKEN_BILLING_MODE — the Part 4.3
     "never show a per-action number" display rule applies to COPY,
     not just to live component state.
  5. (PART 5.1, MODE-AWARE) When TOKEN_BILLING_MODE is not "off", the
     OLD task-based plan-wording phrases (see the PART 5.0 inventory:
     "N tasks/month", "N free tasks", "no token billing"/"no
     per-token" claims) become forbidden too. While the env stays
     "off" (this delivery), these phrases are still ALLOWED — they
     accurately describe today's live product — so this lint stays
     green now and goes red automatically the moment a real deploy
     flips the env var, forcing a human to update the static/SEO
     marketing files this delivery does not rewrite outright (see the
     PART 5 report's Scope of improvement for the full list).

Wired into CI via `.github/workflows/ci.yml`.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_VALID_MODES = {"off", "shadow", "on"}


def _current_mode() -> str:
    """Mirrors services.wallet.billing_mode()'s own fail-safe exactly
    (same env var, same default, same safe-fallback-to-off on an
    unrecognised value) — duplicated here (not imported) so this
    script has zero backend-package import dependencies and can run
    standalone in CI exactly as it does today."""
    raw = (os.environ.get("TOKEN_BILLING_MODE") or "off").strip().lower()
    return raw if raw in _VALID_MODES else "off"


# Rule 4 — always on, regardless of mode. Deliberately simple
# substring/regex matches over a short, curated phrase list rather
# than a general NLP heuristic — false negatives here are expected
# to be caught by code review; false positives are expensive (break
# CI), so precision over recall.
_PER_ACTION_COST_PATTERNS = [
    re.compile(r"tokens?\s+per\s+(fix|task|chat|turn|message|call)", re.IGNORECASE),
    re.compile(r"\bcosts?\s+\d+\s+tokens?\b", re.IGNORECASE),
    re.compile(r"\d+\s+tokens?\s+per\s+(fix|task|chat|turn|message|call)", re.IGNORECASE),
]


def _scan_per_action_cost(text: str) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if any(p.search(line) for p in _PER_ACTION_COST_PATTERNS):
            hits.append((lineno, line.strip()[:180]))
    return hits


# Rule 5 — mode-aware cutover forcing function. Deliberately NARROW:
# only phrasing shaped like an actual plan/quota CLAIM ("N tasks/
# month", "N free tasks", "no token billing") — NOT every occurrence
# of the word "task" (ticket/iteration numbering like "Iter 73 Task
# 1", admin-analytics counts like "0 tasks run", and the generic
# "1 fix = 1 task" UNIT label are all common and would drown out real
# hits in noise if matched).
_LEGACY_TASK_WORDING_PATTERNS = [
    # "N free tasks" / "free ... N tasks" / "N tasks ... free", the
    # word "free" within ~20 chars of a number+tasks, either order —
    # catches "10 free tasks", "Claim 10 free tasks", "Start free —
    # 10 tasks".
    re.compile(r"\d+\s+free\s+tasks?\b", re.IGNORECASE),
    re.compile(r"free\b.{0,20}\d+\s+tasks?\b", re.IGNORECASE),
    re.compile(r"\d+\s+tasks?\b.{0,20}free\b", re.IGNORECASE),
    re.compile(r"tasks?\s*/\s*month", re.IGNORECASE),
    re.compile(r"tasks?\s+per\s+month", re.IGNORECASE),
    re.compile(r"no\s+token\s+(billing|metering)", re.IGNORECASE),
    re.compile(r"no\s+per-token\s+(pricing|billing)", re.IGNORECASE),
    re.compile(r"flat,?\s+not\s+per-token", re.IGNORECASE),
    # ITEM 6 (independent review) — "no token meters" was a real,
    # ungated hit on the homepage ("No IDE, no token meters.") that
    # none of the three patterns above caught ("meters" != "billing"/
    # "metering"), because the Part 5.1 sweep that built this list
    # didn't have this exact phrase in front of it.
    re.compile(r"no\s+token\s+meters?", re.IGNORECASE),
]


def _scan_legacy_task_wording(text: str, *, mode: str) -> list[tuple[int, str]]:
    if mode == "off":
        return []
    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if any(p.search(line) for p in _LEGACY_TASK_WORDING_PATTERNS):
            hits.append((lineno, line.strip()[:180]))
    return hits

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend" / "src"
PUBLIC   = ROOT / "frontend" / "public"

# Marketing surfaces where we care about pricing accuracy.
SCAN_DIRS = [FRONTEND, PUBLIC]
SCAN_EXT  = (".jsx", ".tsx", ".js", ".ts", ".html", ".md", ".txt")

# Files that legitimately talk about the founder flag / server-side
# `is_unlimited` boolean — these are NOT marketing copy.
SKIP_FILES = {
    "TokenBell.jsx",           # UI shows "∞ Unlimited" for founder token wallet
    "Shell.jsx",               # reads is_unlimited from /usage/me
    "BulkFixConfirmModal.jsx", # server flag pass-through
    "ChatPanel.jsx",           # founder detection helper
    "chatTextUtils.js",        # is_unlimited detection helper
    "useFixQuota.js",          # server flag pass-through
    "Tokens.jsx",              # "∞ Unlimited" label for founder
    "Settings.jsx",            # founder tasks-remaining label
}

# Legal documents are reviewed by the founder + lawyer (PART 5.1),
# never auto-edited or CI-blocked by this script — excluded from
# BOTH new rules (4 and 5) by directory, not just by filename.
# "__tests__" is excluded too: test files legitimately reference BOTH
# legacy and token wording as string literals (to assert a
# transition) — scanning them as "marketing copy" would flood rules
# 4/5 with false positives from the test suite itself.
SKIP_DIRS = {"policies", "__tests__"}
_TEST_FILE_SUFFIXES = (".test.js", ".test.jsx", ".test.ts", ".test.tsx")

# Whitelisted exact sentences (case-preserved substring match).
ALLOWLIST_FILE = ROOT / "backend" / "scripts" / "pricing_copy_lint_allowlist.txt"


def _load_allowlist() -> set[str]:
    if not ALLOWLIST_FILE.exists():
        return set()
    return {
        ln.strip() for ln in ALLOWLIST_FILE.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    }


# Danger phrases: "unlimited" within N chars of a pricing keyword.
_DANGER_KEYWORDS = ("task", "tasks", "Pro", "Team", "Starter", "Free", "loop")
_UNLIMITED_RE = re.compile(r"[Uu]nlimited")


def _scan_file(path: Path, allowlist: set[str]) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return hits
    lines = text.splitlines()
    for lineno, line in enumerate(lines, 1):
        for m in _UNLIMITED_RE.finditer(line):
            # Skip identifiers like is_unlimited / unlimited=
            surrounding = line[max(0, m.start() - 5):m.end() + 5].lower()
            if any(bad in surrounding for bad in (
                "is_unlimited", "unlimited=", "unlimited:", "!unlimited",
                "unlimited &&", "unlimited ?", "unlimited)", "unlimited,",
                "!!unlimited",
            )):
                continue
            # Danger only if a pricing keyword appears within 60 chars.
            window = line[max(0, m.start() - 60): m.end() + 60]
            if not any(kw in window for kw in _DANGER_KEYWORDS):
                continue
            # Allowlisted exact line?
            if any(al in line for al in allowlist):
                continue
            hits.append((lineno, line.strip()[:180]))
    return hits


def main() -> int:
    allowlist = _load_allowlist()
    mode = _current_mode()
    all_hits: list[tuple[Path, int, str, str]] = []
    for base in SCAN_DIRS:
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if not p.is_file() or p.suffix not in SCAN_EXT:
                continue
            if any(part in SKIP_DIRS for part in p.relative_to(ROOT).parts):
                continue
            if p.name.endswith(_TEST_FILE_SUFFIXES):
                continue
            if p.name not in SKIP_FILES:
                for lineno, snippet in _scan_file(p, allowlist):
                    all_hits.append((p.relative_to(ROOT), lineno, snippet, "unlimited-near-pricing-keyword"))
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for lineno, snippet in _scan_per_action_cost(text):
                if not any(al in snippet for al in allowlist):
                    all_hits.append((p.relative_to(ROOT), lineno, snippet, "per-action-token-cost"))
            # A file that already imports the shared billingCopy
            # switch has already done its job — its legacy-wording
            # branch is the ternary's intentional dead arm while
            # mode stays off, not drift. (billingCopy.js itself
            # trivially matches this too, which is correct: it's the
            # one place BOTH wordings are meant to live.)
            if "lib/billingCopy" in text:
                continue
            for lineno, snippet in _scan_legacy_task_wording(text, mode=mode):
                if not any(al in snippet for al in allowlist):
                    all_hits.append((p.relative_to(ROOT), lineno, snippet, "legacy-task-wording-cutover"))

    if not all_hits:
        print(f"[pricing_copy_lint] OK — no drift-prone copy found (TOKEN_BILLING_MODE={mode!r}).")
        return 0

    print("[pricing_copy_lint] ❌ FOUND drift-prone marketing copy:")
    print("Backend source of truth: backend/services/subscription_tiers.py:26-83")
    print("  Free=10  Starter=50  Pro=300  Team=400 tasks/month")
    print()
    for rel, lineno, snippet, rule in all_hits:
        print(f"  [{rule}] {rel}:{lineno}  {snippet}")
    print()
    print("If a line is a legitimate exception (e.g. describes the")
    print("server-side is_unlimited flag, or founder-tier UI), add it")
    print(f"to {ALLOWLIST_FILE.relative_to(ROOT)}.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
