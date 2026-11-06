"""tests/test_regression_iter2026_67_phase3_wiring.py

Security audit Phase 3 — the glue around the new scan rules:

  * the deep scan (`two_round`) runs the slop-squat check on the fetched
    package.json / requirements.txt and never on a normal scan, and a dead
    registry cannot fail the scan;
  * the fix flow's re-validation (`_finding_still_present`) understands the new
    rule ids — vanguard's scan_text does not, so without this every new finding
    would read as "already fixed — nothing to do";
  * mass-assignment false-positive shapes (server-built dict, admin-gated);
  * the audit log lines added to the auth routes that the new
    `missing_audit_logging` rule found in AUREM itself (and never log a token).
"""
from __future__ import annotations

import json
import logging

import httpx
import pytest

from services import slop_squat_check as ssc
from services.finding_fix_applier import _finding_still_present
from services.security_extra_rules import scan_extra_rules

# ── fix-flow re-validation ────────────────────────────────────────────
MASS_BAD = "const u = await User.create(req.body);\n"
MASS_FIXED = "const u = await User.create({ email: req.body.email });\n"


def test_still_present_true_while_the_new_rule_still_fires():
    f = {"rule_id": "mass_assignment_request_body"}
    assert _finding_still_present(MASS_BAD, "api/a.js", f) is True


def test_still_present_false_once_the_new_rule_no_longer_fires():
    f = {"rule_id": "mass_assignment_request_body"}
    assert _finding_still_present(MASS_FIXED, "api/a.js", f) is False


def test_still_present_does_not_confuse_one_new_rule_with_another():
    f = {"rule_id": "open_redirect_user_param"}
    assert _finding_still_present(MASS_BAD, "api/a.js", f) is False


def test_still_present_slop_squat_follows_the_package_name():
    f = {"rule_id": "slop_squat_unknown_package", "snippet": "react-ai-magic-hooks-pro"}
    assert _finding_still_present('{"dependencies": {"react-ai-magic-hooks-pro": "1"}}', "package.json", f) is True
    assert _finding_still_present('{"dependencies": {"react": "18"}}', "package.json", f) is False
    assert _finding_still_present("{}", "package.json", {"rule_id": "slop_squat_unknown_package"}) is False


# ── mass assignment: legitimate shapes are not flagged ────────────────
def test_set_of_a_server_built_dict_is_not_mass_assignment():
    code = (
        "async def save(user_id):\n"
        "    payload = {\n"
        '        "user_id": user_id,\n'
        "    }\n"
        '    await db.x.update_one({"user_id": user_id}, {"$set": payload})\n'
    )
    assert scan_extra_rules("svc/a.py", code) == []


def test_admin_gated_settings_write_is_not_flagged():
    code = (
        "async def save_settings(request, authorization=None):\n"
        "    await _require_admin(authorization)\n"
        "    body = await request.json()\n"
        '    await db.s.update_one({"_id": "g"}, {"$set": body}, upsert=True)\n'
    )
    assert scan_extra_rules("routers/s.py", code) == []


def test_same_write_without_the_admin_gate_is_flagged():
    code = (
        "async def save_settings(request):\n"
        "    body = await request.json()\n"
        '    await db.s.update_one({"_id": "g"}, {"$set": body}, upsert=True)\n'
    )
    assert [f["rule_id"] for f in scan_extra_rules("routers/s.py", code)] == ["mass_assignment_request_body"]


# ── deep scan wiring ──────────────────────────────────────────────────
class _FakeDb:
    class cto_projects:
        @staticmethod
        async def find_one(*a, **kw):
            return {"github_owner": "ownerx", "github_repo": "repoy", "github_token": "plain_pat_xyz"}

    class cto_fixed_findings:
        @staticmethod
        def find(*a, **kw):
            class _C:
                def __aiter__(self):
                    return self

                async def __anext__(self):
                    raise StopAsyncIteration

                def sort(self, *a, **k):
                    return self

                def limit(self, *a, **k):
                    return self

                async def to_list(self, *a, **k):
                    return []
            return _C()


REPO = {
    "package.json": json.dumps({"name": "app", "dependencies": {"react": "18", "react-ai-magic-hooks-pro": "1.0.0"}}, indent=2),
    "backend/requirements.txt": "requests==2.31\nfastapi-turbo-guard==0.1\n",
    "public/index.html": '<script src="https://cdn.jsdelivr.net/npm/lodash@4/lodash.min.js"></script>\n',
}


@pytest.fixture
def scan_env(monkeypatch):
    from routers import security_scan as sec

    async def _dev(*a, **k):
        return {"user_id": "u1", "is_admin": True}

    async def _tree(*a, **k):
        return [{"path": p, "type": "blob", "size": 200} for p in REPO]

    async def _fetch(client, owner, repo, path, pat):
        return REPO[path]

    async def _llm(*a, **k):
        return {"ok": False, "content": ""}

    async def _token(proj):
        return "tok", None, None

    monkeypatch.setattr("services.pat_vault.get_repo_token_or_error", _token)
    monkeypatch.setattr(sec, "require_admin", _dev)
    monkeypatch.setattr(sec, "get_db", lambda: _FakeDb())
    monkeypatch.setattr(sec, "_list_repo_tree", _tree)
    monkeypatch.setattr(sec, "_fetch_file", _fetch)
    monkeypatch.setattr("services.llm.call_llm_with_meta", _llm)

    async def _fixed_map(*a, **k):
        return {}

    monkeypatch.setattr("services.fixed_findings.get_fixed_map", _fixed_map)
    ssc.clear_cache()
    yield sec
    ssc.clear_cache()


def _registry(monkeypatch, handler):
    calls: list[str] = []

    def wrapped(req):
        calls.append(str(req.url))
        return handler(req)

    monkeypatch.setattr(
        ssc, "_open_client",
        lambda factory: _Ctx(httpx.AsyncClient(transport=httpx.MockTransport(wrapped))),
    )
    return calls


class _Ctx:
    def __init__(self, client):
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, *exc):
        await self.client.aclose()


def _missing(req):
    name = str(req.url).rsplit("/json", 1)[0].rsplit("/", 1)[-1]
    return httpx.Response(404 if name in {"react-ai-magic-hooks-pro", "fastapi-turbo-guard"} else 200, json={})


@pytest.mark.asyncio
async def test_deep_scan_reports_invented_packages_and_cdn_without_sri(scan_env, monkeypatch):
    _registry(monkeypatch, _missing)
    resp = await scan_env.run_security_scan({"project_id": "p1", "two_round": True}, authorization="Bearer x")
    slop = {(f["file"], f["snippet"]) for f in resp["findings"] if f["rule_id"] == "slop_squat_unknown_package"}
    assert slop == {("package.json", "react-ai-magic-hooks-pro"), ("backend/requirements.txt", "fastapi-turbo-guard")}
    assert any(f["rule_id"] == "sri_missing_cdn_resource" and f["file"] == "public/index.html" for f in resp["findings"])


@pytest.mark.asyncio
async def test_normal_scan_makes_no_registry_requests(scan_env, monkeypatch):
    calls = _registry(monkeypatch, _missing)
    resp = await scan_env.run_security_scan({"project_id": "p1"}, authorization="Bearer x")
    assert calls == []
    assert not any(f["rule_id"] == "slop_squat_unknown_package" for f in resp["findings"])
    assert any(f["rule_id"] == "sri_missing_cdn_resource" for f in resp["findings"])  # static rule still runs


@pytest.mark.asyncio
async def test_dead_registry_cannot_fail_the_deep_scan(scan_env, monkeypatch):
    def down(req):
        raise httpx.ConnectError("registry down")

    _registry(monkeypatch, down)
    resp = await scan_env.run_security_scan({"project_id": "p1", "two_round": True}, authorization="Bearer x")
    assert resp["ok"] is True
    assert not any(f["rule_id"] == "slop_squat_unknown_package" for f in resp["findings"])


@pytest.mark.asyncio
async def test_registry_rate_limit_is_not_a_finding(scan_env, monkeypatch):
    _registry(monkeypatch, lambda req: httpx.Response(429, json={}))
    resp = await scan_env.run_security_scan({"project_id": "p1", "two_round": True}, authorization="Bearer x")
    assert not any(f["rule_id"] == "slop_squat_unknown_package" for f in resp["findings"])


def test_scan_targets_include_manifests_html_and_terraform_only_where_needed():
    from routers.security_scan import _is_extra_rule_target as t
    assert t("backend/requirements.txt") and t("requirements-dev.txt") and t("public/index.html") and t("infra/main.tf")
    assert not t("README.txt") and not t("notes/requirements_doc.md") and not t("src/app.py")


# ── audit log lines in the auth routes (found by the new rule) ────────
class _Col:
    def __init__(self, row=None):
        self.row = row
        self.updates: list = []

    async def find_one(self, *a, **k):
        return self.row

    async def update_one(self, *a, **k):
        self.updates.append((a, k))


class _AuthDb:
    def __init__(self, token_row=None):
        self.password_reset_tokens = _Col(token_row)
        self.dev_users = _Col()
        self.magic_login_tokens = _Col(token_row)


@pytest.mark.asyncio
async def test_password_reset_writes_an_audit_line_without_the_token(monkeypatch, caplog):
    from routers import auth

    secret_token = "SECRET-RESET-TOKEN-123"
    db = _AuthDb({"_id": "r1", "user_id": "u-42", "used": False, "expires_at": 9e12, "token": secret_token})
    monkeypatch.setattr(auth, "get_db", lambda: db)
    with caplog.at_level(logging.INFO, logger=auth.logger.name):
        out = await auth.reset_password(auth.ResetPasswordBody(token=secret_token, new_password="longenough1"))
    assert out == {"ok": True}
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "audit password_reset_completed user_id=u-42" in text
    assert secret_token not in text and "longenough1" not in text


@pytest.mark.asyncio
@pytest.mark.parametrize("row,reason", [
    (None, "invalid_link"),
    ({"used": True, "expires_at": 9e12}, "already_used"),
    ({"used": False, "expires_at": 1}, "expired"),
])
async def test_rejected_magic_login_is_logged_without_the_token(monkeypatch, caplog, row, reason):
    from fastapi import HTTPException

    from routers import auth

    token = "MAGIC-TOKEN-ABC"
    monkeypatch.setattr(auth, "get_db", lambda: _AuthDb(row))
    with caplog.at_level(logging.WARNING, logger=auth.logger.name), pytest.raises(HTTPException):
        await auth.magic_login_exchange(auth.MagicLoginExchangeBody(token=token))
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert f"audit magic_login_exchange_rejected reason={reason}" in text
    assert token not in text
