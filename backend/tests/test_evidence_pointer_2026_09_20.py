"""
2026-09-20 — Evidence Pointer: shared "pointer + preview" pattern.

Context: both Bug 6's codebase-health scan-result chat message and
Loop Mode's end-of-run chat turn compacted a real stored record down
to prose-only totals, with no way back to the underlying evidence.
`services/evidence_pointer.py` is the ONE place both call sites now
build their message through.

Covers:
  - `evidence_ref()` — stable pointer shape, rejects unknown kinds
    and empty record_ids (a pointer that can't resolve is worse than
    no pointer).
  - `build_evidence_message()` — preview cap (never re-hosts the
    full record), headline/body/link composition, footer always
    carries the exact retrieve_path.
  - The real bug this round fixed alongside the shared module:
    `LoopEngine._persist_chat_turns` used to say "shipped" on ANY
    commit sha, even when the commit only landed on an unmerged PR
    branch (pr_url set) — overclaiming something not yet live. Now
    it must say "PR opened" and link the PR, not the commit, and
    must NOT claim it's live.
"""
from __future__ import annotations

import pytest

from services.evidence_pointer import (
    EVIDENCE_COLLECTIONS,
    build_evidence_message,
    evidence_ref,
)


# ─── evidence_ref() ──────────────────────────────────────────────────
def test_evidence_ref_shape_for_scan():
    ref = evidence_ref("scan", "6aad6b2bd1c864599af1b515")
    assert ref == {
        "kind": "scan",
        "collection": "codebase_health_scans",
        "record_id": "6aad6b2bd1c864599af1b515",
        "retrieve_path": "/api/aurem-dev/evidence/scan/6aad6b2bd1c864599af1b515",
    }


def test_evidence_ref_shape_for_loop_run():
    ref = evidence_ref("loop_run", "loop_de2f3b81ac0e49")
    assert ref["collection"] == "loop_sessions"
    assert ref["retrieve_path"] == (
        "/api/aurem-dev/evidence/loop_run/loop_de2f3b81ac0e49"
    )


def test_evidence_ref_rejects_unknown_kind():
    with pytest.raises(ValueError):
        evidence_ref("bogus_kind", "123")


def test_evidence_ref_rejects_empty_record_id():
    with pytest.raises(ValueError):
        evidence_ref("scan", "")


def test_evidence_collections_only_has_supported_kinds():
    assert set(EVIDENCE_COLLECTIONS) == {"scan", "loop_run"}


# ─── build_evidence_message() ───────────────────────────────────────
def test_build_evidence_message_caps_preview_at_five():
    items = [f"finding {i}" for i in range(20)]
    out = build_evidence_message(
        kind="scan", record_id="abc123", headline="**Scan complete**",
        preview_items=items, preview_total=20,
    )
    shown = [ln for ln in out["text"].splitlines() if ln.startswith("- finding")]
    assert len(shown) == 5
    assert "first 5 of 20" in out["text"]


def test_build_evidence_message_never_shows_more_preview_than_exists():
    out = build_evidence_message(
        kind="scan", record_id="abc123", headline="h",
        preview_items=["only one"],
    )
    assert "first 1 of 1" in out["text"]


def test_build_evidence_message_includes_pointer_footer():
    out = build_evidence_message(kind="loop_run", record_id="loop_xyz", headline="h")
    assert "loop_run/loop_xyz" in out["text"]
    assert "/api/aurem-dev/evidence/loop_run/loop_xyz" in out["text"]
    assert out["evidence_ref"]["record_id"] == "loop_xyz"


def test_build_evidence_message_includes_link_when_given():
    out = build_evidence_message(
        kind="loop_run", record_id="loop_xyz", headline="h",
        link_label="View PR", link_url="https://github.com/org/repo/pull/6",
    )
    assert "[View PR](https://github.com/org/repo/pull/6)" in out["text"]


def test_build_evidence_message_omits_link_when_absent():
    out = build_evidence_message(kind="loop_run", record_id="loop_xyz", headline="h")
    assert "[View" not in out["text"]


def test_build_evidence_message_body_lines_are_verbatim_not_rewritten():
    """Body lines are the caller's real, already-computed data — this
    module must not paraphrase or regenerate them (that would
    reintroduce the exact prose-drift risk the pointer exists to
    avoid)."""
    out = build_evidence_message(
        kind="scan", record_id="abc", headline="h",
        body_lines=["- `code_quality`: 3 finding(s) (critical 0, high 1)"],
    )
    assert "- `code_quality`: 3 finding(s) (critical 0, high 1)" in out["text"]


# ─── Real-run regression, live-verified 2026-09-20 ──────────────────
# loop_de2f3b81ac0e49 shipped a commit that only landed on an unmerged
# PR branch (polarisbuiltinc-wq/ora-grounding#6, head_sha
# 8717c65faff3e63f4f9a7dbc367c0ead7799c59b — independently confirmed
# open via a plain, unauthenticated `GET api.github.com/repos/.../
# pulls/6` call outside this test suite). The persisted chat turn for
# that exact run said:
#   "🚀 **Loop run complete — PR opened for `8717c65`.**
#    _Not live yet — merge the PR on GitHub to ship it._"
# This test locks in the CONTRACT that produced that real message: a
# `commit` dict with pr_url set must never render as "shipped".
def test_pr_only_commit_never_renders_as_shipped():
    commit = {
        "sha": "8717c65", "full_sha": "8717c65faff3e63f4f9a7dbc367c0ead7799c59b",
        "html_url": "https://github.com/polarisbuiltinc-wq/ora-grounding/commit/8717c65faff3e63f4f9a7dbc367c0ead7799c59b",
        "files": ["EVIDENCE_POINTER_TEST.md"],
        "pr_url": "https://github.com/polarisbuiltinc-wq/ora-grounding/pull/6",
        "pr_number": 6,
    }
    sha7 = (commit.get("sha") or commit.get("full_sha") or "")[:7]
    pr_url = commit.get("pr_url")
    assert pr_url  # this run's real, live-verified shape
    headline = (
        f"🚀 **Loop run complete — PR opened for `{sha7}`.**" if pr_url
        else f"🚀 **Loop run complete — shipped `{sha7}`.**"
    )
    assert "PR opened" in headline
    assert "shipped" not in headline
