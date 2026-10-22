"""tests/test_regression_iter2026_10o_chat_fix_entitlement_gate.py

Closing-the-bypass-doors audit — Step 1.

Chat's "fix it" Mode D handoff (`services/mode_d_debugger.py::
try_scan_followup_reply`, wired from `routers/chat/worker.py`) calls
the exact same `apply_finding_fix()` worker as the HTTP fix buttons,
gated only by `assert_can_fix` (task-quota tier gate) — no
fix_entitlement check. This is an SSE/chat response, not a plain JSON
endpoint, so the fix is a try/except around `assert_fix_entitled`
(same pattern already used for `assert_can_fix`'s HTTPException right
above it) that turns a 402 into a normal chat reply instead of letting
it propagate as a raw exception into the stream.
"""
from __future__ import annotations

import time

import pytest

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _reset_global_db():
    yield
    from cto_services import db as _dbmod
    _dbmod.set_db(None)


class _FakeColl:
    def __init__(self, rows=None):
        self.rows = rows or []

    async def find_one(self, query=None, projection=None, sort=None):
        query = query or {}
        matched = [r for r in self.rows
                   if all(r.get(k) == v for k, v in query.items()
                          if not isinstance(v, dict))]
        if sort and matched:
            key, direction = sort[0]
            matched.sort(key=lambda r: r.get(key, 0), reverse=(direction == -1))
        return dict(matched[0]) if matched else None

    async def insert_one(self, doc):
        self.rows.append(dict(doc))
        class _R:
            inserted_id = "fake"
        return _R()

    async def count_documents(self, query):
        return 0

    def aggregate(self, pipeline):
        class _AggCur:
            async def to_list(self, length=None):
                return []
        return _AggCur()


class _FakeDB:
    def __init__(self, *, user_doc, scan_doc):
        self.dev_users = _FakeColl([user_doc])
        self.codebase_health_scans = _FakeColl([scan_doc])

    def __getattr__(self, name):
        coll = _FakeColl()
        object.__setattr__(self, name, coll)
        return coll


_SCAN_DOC = {
    "user_id": "u1", "project_id": "p1",
    "created_at": time.time(), "summary": "16 categories scanned",
    "breakdown": {
        "security": {"findings": [
            {"id": "f1", "file": "app.py", "line": 10, "severity": "critical",
             "rule_id": "secret_aws_key", "title": "AWS key leak",
             "message": "x", "fix_hint": "remove it"},
        ]},
    },
}

_MESSAGE = "please fix the critical findings from that scan"


class TestChatFixEntitlementGate:
    async def test_on_mode_graceful_reply_when_not_entitled_no_commit_attempted(
        self, monkeypatch,
    ):
        from services import finding_fix_applier as ff
        from services.mode_d_debugger import try_scan_followup_reply
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")

        called = {"apply": False}
        async def fake_apply(**kw):
            called["apply"] = True
            return {"ok": True}
        monkeypatch.setattr(ff, "apply_finding_fix", fake_apply)

        db = _FakeDB(
            user_doc={"user_id": "u1", "tier": "team"},
            scan_doc=dict(_SCAN_DOC),
        )
        from cto_services import db as _dbmod
        _dbmod.set_db(db)
        result = await try_scan_followup_reply(
            db, user_id="u1", project_id="p1", user_message=_MESSAGE,
        )
        assert result is not None
        assert "upgrade" in result["ora_reply"].lower() or \
               "locked" in result["ora_reply"].lower()
        assert called["apply"] is False, (
            "no fix/commit attempt must happen when the chat path is "
            "not entitled"
        )

    async def test_on_mode_runs_fix_when_entitled(self, monkeypatch):
        from services import finding_fix_applier as ff
        from services.mode_d_debugger import try_scan_followup_reply
        monkeypatch.setenv("TOKEN_BILLING_MODE", "on")

        async def fake_apply(**kw):
            return {"ok": True, "commit_sha": "abc1234",
                    "file": kw["finding"]["file"], "rule_id": kw["finding"]["rule_id"]}
        monkeypatch.setattr(ff, "apply_finding_fix", fake_apply)

        db = _FakeDB(
            user_doc={"user_id": "u1", "tier": "team", "fix_entitlement": True},
            scan_doc=dict(_SCAN_DOC),
        )
        from cto_services import db as _dbmod
        _dbmod.set_db(db)
        result = await try_scan_followup_reply(
            db, user_id="u1", project_id="p1", user_message=_MESSAGE,
        )
        assert result is not None
        assert "fixed" in result["ora_reply"].lower()

    async def test_off_mode_unaffected_default_behavior(self, monkeypatch):
        from services import finding_fix_applier as ff
        from services.mode_d_debugger import try_scan_followup_reply
        monkeypatch.delenv("TOKEN_BILLING_MODE", raising=False)

        async def fake_apply(**kw):
            return {"ok": True, "commit_sha": "abc1234",
                    "file": kw["finding"]["file"], "rule_id": kw["finding"]["rule_id"]}
        monkeypatch.setattr(ff, "apply_finding_fix", fake_apply)

        db = _FakeDB(
            user_doc={"user_id": "u1", "tier": "team"},
            scan_doc=dict(_SCAN_DOC),
        )
        from cto_services import db as _dbmod
        _dbmod.set_db(db)
        result = await try_scan_followup_reply(
            db, user_id="u1", project_id="p1", user_message=_MESSAGE,
        )
        assert result is not None
        assert "fixed" in result["ora_reply"].lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
