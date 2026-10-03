"""Fix packs + safe fix loop + guardian reconcile. Behavioural: packs are
proven by re-running the real checks on the patched tree."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from services import fix_items as fi
from services.checkup_orchestrator import run_checkup
from services.fix_loop import run_fix, undo_fix
from services.fix_packs import build_patch
from tests.test_checkup_orchestrator import _jwt
from tests.test_fix_items_layer import _DB

CLOCK = lambda: datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)  # noqa: E731
PKG = json.dumps({"dependencies": {"react": "18", "@supabase/supabase-js": "2"}, "devDependencies": {"vite": "5"}})
SQL = ("create table public.orders (id int, user_id uuid, email text);\n"
       "create table public.invoices (id int, amount int);\n"
       "alter table public.invoices enable row level security;\n"
       "create policy \"open\" on public.invoices for all to anon using (true) with check (true);\n")
TC = {"package.json": PKG, "supabase/migrations/001.sql": SQL,
      "src/lib/sb.js": f'export const k = "{_jwt("service_role")}";\n'}


def _items(tc):
    return {i["rule_id"]: i for i in run_checkup(tc, include_scanners=False)["items"]}


def test_lock_my_data_enables_rls_with_owner_policies_and_passes_proof():
    item = _items(TC)["rls_disabled"]
    patch = build_patch(item, TC, CLOCK)
    (path, sql), = patch["files"].items()
    assert path == "supabase/migrations/20260102030405_aurem_lock_orders.sql"
    assert "enable row level security" in sql and "auth.uid() = user_id" in sql
    after = {i["id"] for i in run_checkup({**TC, **patch["files"]}, include_scanners=False)["items"]}
    assert item["id"] not in after


def test_open_policy_is_dropped_and_proof_passes_without_owner_column():
    item = _items(TC)["policy_open_write"]
    patch = build_patch(item, TC, CLOCK)
    sql = next(iter(patch["files"].values()))
    assert 'drop policy if exists "open" on public.invoices' in sql and "create policy" not in sql
    assert "only be reachable from your server" in " ".join(patch["manual_steps"])
    after = {i["id"] for i in run_checkup({**TC, **patch["files"]}, include_scanners=False)["items"]}
    assert item["id"] not in after


def test_hide_my_keys_removes_literal_and_requires_rotation_note():
    item = _items(TC)["service_role_key_in_client"]
    patch = build_patch(item, TC, CLOCK)
    new = patch["files"]["src/lib/sb.js"]
    assert "import.meta.env.VITE_SUPABASE_ANON_KEY" in new and "eyJ" not in new
    assert any("Rotate" in s for s in patch["manual_steps"])
    assert "service_role_key_in_client" not in _items({**TC, **patch["files"]})


def test_unfixable_items_return_none_not_a_guess():
    assert build_patch({"rule_id": "dead_button", "evidence": {"file": "a.jsx"}}, TC) is None
    assert build_patch({"rule_id": "rls_disabled", "evidence": {"table": "x; drop table y"}}, TC) is None


class _Git:
    def __init__(self, fail=None):
        self.fail, self.calls = fail, []

    async def head_sha(self, b):
        return "base123"

    async def create_branch(self, base, new):
        self.calls.append(("branch", new))
        return self.fail != "branch"

    async def commit(self, branch, files, msg):
        self.calls.append(("commit", branch, sorted(files)))
        return "c1"

    async def open_pr(self, head, base, title, body):
        self.calls.append(("pr", head, base))
        return None if self.fail == "pr" else {"url": "https://x/pr/7", "number": 7}

    async def close_pr(self, n):
        self.calls.append(("close", n))
        return self.fail != "close"


def _seed(db):
    for it in run_checkup(TC, include_scanners=False)["items"]:
        asyncio.run(fi.upsert_item(db, user_id="u", project_id="p", item=it))
    return {i["rule_id"]: i for i in asyncio.run(fi.list_items(db, user_id="u", project_id="p"))}


def _fix(db, item, git):
    return asyncio.run(run_fix(db, user_id="u", project_id="p", item_id=item["id"], tc=TC,
                               base_branch="main", git=git, clock=CLOCK))


def test_loop_opens_pr_only_and_never_touches_default_branch():
    db, git = _DB(), _Git()
    item = _seed(db)["service_role_key_in_client"]
    r = _fix(db, item, git)
    assert r["ok"] and r["pr_url"] == "https://x/pr/7" and r["manual_steps"]
    kinds = [c[0] for c in git.calls]
    assert kinds == ["branch", "commit", "pr"]
    assert all(c[1] != "main" for c in git.calls if c[0] in ("commit",))
    row = next(x for x in db.cto_open_findings.rows if x["finding_id"] == item["id"])
    assert row["status"] == "fixing" and row["fix"]["pr_number"] == 7 and row["fix"]["base_sha"] == "base123"
    ev = [e["event"] for e in db.checkup_events.rows]
    assert {"fix_clicked", "fix_applied", "fix_verified"} <= set(ev)


def test_failures_restore_item_to_open_and_change_nothing():
    for fail in ("branch", "pr"):
        db, git = _DB(), _Git(fail)
        item = _seed(db)["service_role_key_in_client"]
        r = _fix(db, item, git)
        assert not r["ok"] and r["reason"] == "git_failed"
        assert next(x for x in db.cto_open_findings.rows if x["finding_id"] == item["id"])["status"] == "open"


def test_not_auto_fixable_never_calls_git():
    db, git = _DB(), _Git()
    db.cto_open_findings.rows.append({"user_id": "u", "project_id": "p", "finding_id": "d", "scanner": "bug_hunt",
                                      "rule_id": "dead_button", "severity": "medium", "status": "open",
                                      "evidence": {"file": "a.jsx"}})
    r = asyncio.run(run_fix(db, user_id="u", project_id="p", item_id="d", tc=TC, base_branch="main", git=git))
    assert r["reason"] == "not_auto_fixable" and git.calls == []


def test_undo_closes_pr_and_reopens_item():
    db, git = _DB(), _Git()
    item = _seed(db)["service_role_key_in_client"]
    _fix(db, item, git)
    assert asyncio.run(undo_fix(db, user_id="u", project_id="p", item_id=item["id"], git=git))["ok"]
    assert ("close", 7) in git.calls
    assert next(x for x in db.cto_open_findings.rows if x["finding_id"] == item["id"])["status"] == "open"
    bad = _Git("close")
    _fix(db, item, _Git())
    assert not asyncio.run(undo_fix(db, user_id="u", project_id="p", item_id=item["id"], git=bad))["ok"]


def test_guardian_reconcile_marks_fixed_only_with_proof_and_skips_unverified():
    db = _DB()
    _seed(db)
    ids = {r["finding_id"]: r for r in db.cto_open_findings.rows}
    keep = next(i for i, r in ids.items() if r["rule_id"] == "rls_disabled")
    closed = asyncio.run(fi.reconcile(db, user_id="u", project_id="p", detected_ids={keep},
                                      unverified_stages={"permissions"}))
    by = {r["rule_id"]: r for r in db.cto_open_findings.rows}
    assert by["service_role_key_in_client"]["status"] == "fixed"
    assert by["service_role_key_in_client"]["fixed_by"] == "proof_rerun"
    assert by["policy_open_write"]["status"] == "open"        # its stage couldn't be verified
    assert by["rls_disabled"]["status"] == "open"             # still detected
    assert len(closed) == 1
    # a legacy row from another writer is never auto-closed
    db.cto_open_findings.rows.append({"user_id": "u", "project_id": "p", "finding_id": "old", "status": "open",
                                      "source": "loop_full_scan", "stage": "safe"})
    asyncio.run(fi.reconcile(db, user_id="u", project_id="p", detected_ids=set(), unverified_stages=set()))
    assert next(r for r in db.cto_open_findings.rows if r["finding_id"] == "old")["status"] == "open"
