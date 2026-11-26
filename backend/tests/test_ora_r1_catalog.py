"""test_ora_r1_catalog.py — R1 regression tests (ora-r1, 2026-11-26).

Ora executes tasks: the 7-action catalog widened to 11 with
task-executing actions that reuse existing deterministic backend
functions (backlog edit, admin email, house-rules management).
Every test exercises REAL catalog/prompt code with only the DB and
the mail/house-rules collaborators faked — no Mongo, no Resend, no
live model.

Fake-DB + fresh-event-loop harness copied from the Agent 1 repro
file (test_agent1_ora_reply_mismatch_repro.py), which already runs
in this venv.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import types

from services.ora_chat_v2 import catalog
from services.ora_chat_v2.prompt import SYSTEM_PROMPT


# ── Fake Mongo surface ──────────────────────────────────────────────
class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def sort(self, *_a, **_k):
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def __aiter__(self):
        self._it = iter(self._rows)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class _Coll:
    async def count_documents(self, *a, **k):
        return 0

    def find(self, *a, **k):
        return _Cursor([])

    async def find_one(self, *a, **k):
        return None

    async def insert_one(self, *a, **k):
        return None

    async def update_one(self, *a, **k):
        return None


class _BacklogColl:
    """Stateful ora_backlog_items: find/update/insert by backlog_id."""

    def __init__(self, docs=()):
        self.docs = {d["backlog_id"]: dict(d) for d in docs}

    async def find_one(self, query, *a, **k):
        doc = self.docs.get((query or {}).get("backlog_id"))
        return dict(doc) if doc is not None else None

    async def update_one(self, query, update, *a, **k):
        doc = self.docs.get((query or {}).get("backlog_id"))
        if doc is not None:
            doc.update((update or {}).get("$set", {}))

    async def insert_one(self, doc, *a, **k):
        self.docs[doc["backlog_id"]] = dict(doc)


class _FakeDb:
    def __init__(self, backlog_docs=()):
        self._colls = {"ora_backlog_items": _BacklogColl(backlog_docs)}

    def __getattr__(self, name):
        if name not in self._colls:
            self._colls[name] = _Coll()
        return self._colls[name]

    async def command(self, *a, **k):
        return {"ok": 1}


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _sensitive_on(monkeypatch):
    monkeypatch.setenv("ORA_CHAT_SENSITIVE", "on")


def _sensitive_off(monkeypatch):
    monkeypatch.setenv("ORA_CHAT_SENSITIVE", "off")


def _stub_daily_digest(monkeypatch, sent):
    """Stub services.daily_digest so _email_admin never touches Resend."""
    mod = types.ModuleType("services.daily_digest")

    async def _fake_send(to_email, subject, body):
        sent.append((to_email, subject, body))
        return True

    mod._send_via_resend = _fake_send
    monkeypatch.setitem(sys.modules, "services.daily_digest", mod)
    return mod


def _stub_daily_digest_failing(monkeypatch):
    mod = types.ModuleType("services.daily_digest")

    async def _fake_send(to_email, subject, body):
        return False

    mod._send_via_resend = _fake_send
    monkeypatch.setitem(sys.modules, "services.daily_digest", mod)
    return mod


def _stub_house_rules(monkeypatch, calls):
    """Stub services.ora_chat.house_rules with recording fakes."""
    mod = types.ModuleType("services.ora_chat.house_rules")

    async def _fake_update(admin_id, text):
        calls.append(("update", admin_id, text))
        return {"ok": True,
                "rules": {"version": 7, "rules_text": text},
                "soft_warning": None}

    async def _fake_restore(admin_id, version):
        calls.append(("restore", admin_id, version))
        return {"ok": True,
                "rules": {"version": 8, "rules_text": "restored"},
                "soft_warning": None}

    mod.update = _fake_update
    mod.restore = _fake_restore
    monkeypatch.setitem(sys.modules, "services.ora_chat.house_rules", mod)
    return mod


# ── Catalog shape ───────────────────────────────────────────────────
def test_r1_catalog_has_eleven_actions():
    assert len(catalog.ACTION_CATALOG) == 11
    for action_id in ("trigger_digest", "unpark_backlog_item",
                      "park_backlog_item", "create_backlog_item",
                      "set_funnel_slo", "set_env", "toggle_flag"):
        assert action_id in catalog.ACTION_CATALOG, f"original action lost: {action_id}"
    for action_id in ("update_backlog_item", "email_admin",
                      "update_house_rules", "restore_house_rules"):
        assert action_id in catalog.ACTION_CATALOG, f"new action missing: {action_id}"


def test_r1_new_action_risks_and_defaults():
    assert catalog.ACTION_CATALOG["update_backlog_item"]["risk"] == "reversible"
    assert catalog.ACTION_CATALOG["update_backlog_item"]["enabled_by_default"] is True
    assert catalog.ACTION_CATALOG["email_admin"]["risk"] == "reversible"
    assert catalog.ACTION_CATALOG["email_admin"]["enabled_by_default"] is True
    assert catalog.ACTION_CATALOG["update_house_rules"]["risk"] == "sensitive"
    assert catalog.ACTION_CATALOG["update_house_rules"]["enabled_by_default"] is False
    assert catalog.ACTION_CATALOG["restore_house_rules"]["risk"] == "sensitive"
    assert catalog.ACTION_CATALOG["restore_house_rules"]["enabled_by_default"] is False


def test_r1_catalog_prompt_block_lists_new_actions(monkeypatch):
    _sensitive_off(monkeypatch)
    block = catalog.catalog_prompt_block()
    for action_id in ("update_backlog_item", "email_admin",
                      "update_house_rules", "restore_house_rules"):
        assert action_id in block, f"{action_id} not shown to the model"
    # Sensitive entries are visible but marked DISABLED while the flag is off.
    assert "update_house_rules [sensitive] (currently DISABLED" in block
    assert "restore_house_rules [sensitive] (currently DISABLED" in block


# ── update_backlog_item ─────────────────────────────────────────────
def test_r1_update_backlog_item_edits_title_and_note():
    db = _FakeDb([{"backlog_id": "b1", "title": "Old",
                   "note": "old note", "status": "queued"}])
    out = _run(catalog.execute_action(
        db, "update_backlog_item",
        {"id": "b1", "title": "New", "note": "new note"}))
    assert out["ok"] is True
    assert out["result"]["before"]["title"] == "Old"
    assert out["result"]["after"]["title"] == "New"
    assert out["result"]["after"]["note"] == "new note"
    assert out["result"]["after"]["status"] == "queued"
    assert db.ora_backlog_items.docs["b1"]["title"] == "New"


def test_r1_update_backlog_item_title_only_leaves_note():
    db = _FakeDb([{"backlog_id": "b1", "title": "Old",
                   "note": "keep me", "status": "queued"}])
    out = _run(catalog.execute_action(
        db, "update_backlog_item", {"id": "b1", "title": "New"}))
    assert out["ok"] is True
    assert db.ora_backlog_items.docs["b1"]["note"] == "keep me"


def test_r1_update_backlog_item_unknown_id():
    out = _run(catalog.execute_action(
        _FakeDb(), "update_backlog_item", {"id": "nope", "title": "x"}))
    assert out == {"ok": False, "error": "not_found"}


def test_r1_update_backlog_item_no_fields():
    db = _FakeDb([{"backlog_id": "b1", "title": "Old", "status": "queued"}])
    out = _run(catalog.execute_action(
        db, "update_backlog_item", {"id": "b1"}))
    assert out["ok"] is False and out["error"] == "missing_param"


def test_r1_update_backlog_item_missing_id():
    out = _run(catalog.execute_action(
        _FakeDb(), "update_backlog_item", {"title": "x"}))
    assert out["ok"] is False and out["error"] == "missing_param"


# ── email_admin ─────────────────────────────────────────────────────
def test_r1_email_admin_sends_to_admin_email_only(monkeypatch):
    sent = []
    _stub_daily_digest(monkeypatch, sent)
    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.com")
    out = _run(catalog.execute_action(
        _FakeDb(), "email_admin",
        {"subject": "Hi", "body": "hello"}))
    assert out["ok"] is True
    assert out["result"] == {"sent_to": "admin@example.com", "subject": "Hi"}
    assert sent == [("admin@example.com", "Hi", "hello")]
    # The model cannot choose a recipient: no such field exists.
    assert "to" not in catalog.ACTION_CATALOG["email_admin"]["params_schema"]
    assert "recipient" not in catalog.ACTION_CATALOG["email_admin"]["params_schema"]


def test_r1_email_admin_ignores_recipient_param(monkeypatch):
    """A model-supplied "to" must never redirect the mail."""
    sent = []
    _stub_daily_digest(monkeypatch, sent)
    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.com")
    out = _run(catalog.execute_action(
        _FakeDb(), "email_admin",
        {"subject": "Hi", "body": "hello", "to": "attacker@example.com"}))
    assert out["ok"] is True
    assert sent == [("admin@example.com", "Hi", "hello")]


def test_r1_email_admin_send_failure_is_honest(monkeypatch):
    _stub_daily_digest_failing(monkeypatch)
    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.com")
    out = _run(catalog.execute_action(
        _FakeDb(), "email_admin", {"subject": "Hi", "body": "hello"}))
    assert out["ok"] is False and out["error"] == "email_not_sent"


def test_r1_email_admin_missing_admin_email(monkeypatch):
    sent = []
    _stub_daily_digest(monkeypatch, sent)
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    out = _run(catalog.execute_action(
        _FakeDb(), "email_admin", {"subject": "Hi", "body": "hello"}))
    assert out["ok"] is False and out["error"] == "admin_email_not_configured"
    assert sent == []


def test_r1_email_admin_missing_subject_or_body():
    out = _run(catalog.execute_action(
        _FakeDb(), "email_admin", {"subject": "Hi"}))
    assert out["ok"] is False and out["error"] == "missing_param"


# ── house-rules actions ─────────────────────────────────────────────
def test_r1_update_house_rules_needs_admin(monkeypatch):
    _sensitive_on(monkeypatch)
    calls = []
    _stub_house_rules(monkeypatch, calls)
    out = _run(catalog.execute_action(
        _FakeDb(), "update_house_rules",
        {"rules_text": "Be brief."}))
    assert out["ok"] is False and out["error"] == "admin_required"
    assert calls == []


def test_r1_update_house_rules_sensitive_gate_off(monkeypatch):
    _sensitive_off(monkeypatch)
    calls = []
    _stub_house_rules(monkeypatch, calls)
    out = _run(catalog.execute_action(
        _FakeDb(), "update_house_rules",
        {"rules_text": "Be brief."}, admin_id="u1"))
    assert out["ok"] is False and out["error"] == "sensitive_actions_disabled"
    assert calls == []


def test_r1_update_house_rules_delegates(monkeypatch):
    _sensitive_on(monkeypatch)
    calls = []
    _stub_house_rules(monkeypatch, calls)
    out = _run(catalog.execute_action(
        _FakeDb(), "update_house_rules",
        {"rules_text": "Be brief."}, admin_id="u1"))
    assert out["ok"] is True
    assert out["result"]["version"] == 7
    assert calls == [("update", "u1", "Be brief.")]


def test_r1_update_house_rules_empty_text(monkeypatch):
    _sensitive_on(monkeypatch)
    calls = []
    _stub_house_rules(monkeypatch, calls)
    out = _run(catalog.execute_action(
        _FakeDb(), "update_house_rules",
        {"rules_text": "   "}, admin_id="u1"))
    assert out["ok"] is False and out["error"] == "missing_param"
    assert calls == []


def test_r1_restore_house_rules_delegates(monkeypatch):
    _sensitive_on(monkeypatch)
    calls = []
    _stub_house_rules(monkeypatch, calls)
    out = _run(catalog.execute_action(
        _FakeDb(), "restore_house_rules",
        {"version": 3}, admin_id="u1"))
    assert out["ok"] is True
    assert out["result"]["version"] == 8
    assert calls == [("restore", "u1", 3)]


def test_r1_restore_house_rules_invalid_version(monkeypatch):
    _sensitive_on(monkeypatch)
    calls = []
    _stub_house_rules(monkeypatch, calls)
    out = _run(catalog.execute_action(
        _FakeDb(), "restore_house_rules",
        {"version": "abc"}, admin_id="u1"))
    assert out["ok"] is False and out["error"] == "invalid_version"
    assert calls == []


def test_r1_restore_house_rules_needs_admin(monkeypatch):
    _sensitive_on(monkeypatch)
    calls = []
    _stub_house_rules(monkeypatch, calls)
    out = _run(catalog.execute_action(
        _FakeDb(), "restore_house_rules", {"version": 3}))
    assert out["ok"] is False and out["error"] == "admin_required"
    assert calls == []


# ── Backward compat: old callers pass no admin_id ───────────────────
def test_r1_old_actions_work_without_admin_id():
    db = _FakeDb()
    out = _run(catalog.execute_action(
        db, "create_backlog_item", {"title": "T", "note": "n"}))
    assert out["ok"] is True
    backlog_id = out["result"]["backlog_id"]
    out2 = _run(catalog.execute_action(
        db, "park_backlog_item", {"id": backlog_id, "note": "later"}))
    assert out2["ok"] is True
    assert out2["result"]["after"]["status"] == "parked"


# ── Prompt: doer, not just advisor ──────────────────────────────────
def test_r1_prompt_tells_ora_to_execute():
    assert "EXECUTE catalog actions" in SYSTEM_PROMPT
    # The phrase wraps across a line break in prompt.py source — normalise
    # whitespace so the assertion tests the wording, not the source layout.
    assert "do the work, don't describe doing it" in " ".join(SYSTEM_PROMPT.split())
    assert "Never answer with a dev prompt" in SYSTEM_PROMPT
    # The dev prompt survives — only for work the catalog does NOT cover.
    assert "When the catalog DOES" in SYSTEM_PROMPT
    assert "paste-ready developer prompt" in SYSTEM_PROMPT


def test_r1_prompt_still_forbids_outside_catalog():
    assert "never edit, deploy, or run" in SYSTEM_PROMPT
    assert "outside the catalog" in SYSTEM_PROMPT
    assert "ONLY propose actions from the [ACTION CATALOG] block" in SYSTEM_PROMPT
    assert "Never write code or shell" in SYSTEM_PROMPT


# ── Router call-site: approve threads the verified admin id ─────────
# Source-level assertion (same precedent as Agent 1's RC-4 test,
# which asserts on engine source): importing routers.ora_chat needs
# the full app dependency set, so the one-line call-site change is
# pinned here instead of via a live endpoint test.
def test_r1_approve_threads_admin_id():
    router_path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "routers" / "ora_chat.py"
    )
    src = router_path.read_text()
    start = src.index("async def approve_action")
    end = src.index("async def reject_action", start)
    body = src[start:end]
    assert "execute_action(" in body
    assert 'admin_id=user["user_id"]' in body, (
        "approve_action must pass the verified admin id to "
        "execute_action (house-rules actions fail closed without it)")
