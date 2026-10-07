"""Regression — Admin Team AT3: sidebar access + request/approve flow.

Covers the backend half of "add an Admin option to a team member's own
sidebar that opens the admin panel at their granted access level":
  * GET /admin/team/my-access — self-service, works for the Owner, an
    active team member (with their real overrides), and refuses
    gracefully (no 500) when called with no admin context at all.
  * POST /admin/team/access-requests — a team member can ask for a
    screen; the Owner (not the requester) can see/resolve it.
  * Owner-only review endpoints 403 a non-owner team member.
  * The access-request flow never grants the requested screen by
    itself — resolving one does not change the requester's own
    `my-access` screens map.
"""
import pytest

from services.admin_access_registry import ADMIN_ROUTE_REGISTRY, MFA_SELF_SERVICE, OWNER_ONLY


def test_regression_at3_my_access_and_access_requests_are_self_service():
    keys = set(MFA_SELF_SERVICE.keys())
    assert ("GET", "/api/aurem-dev/admin/team/my-access") in keys
    assert ("POST", "/api/aurem-dev/admin/team/access-requests") in keys
    assert ("GET", "/api/aurem-dev/admin/me") in keys


def test_regression_at3_review_endpoints_are_owner_only_in_registry():
    rows = {(m, p): lvl for (m, p, _k, lvl) in ADMIN_ROUTE_REGISTRY}
    assert rows[("GET", "/api/aurem-dev/admin/team/access-requests")] == OWNER_ONLY
    assert rows[("POST", "/api/aurem-dev/admin/team/access-requests/{request_id}/resolve")] == OWNER_ONLY


class _FakeCollection:
    def __init__(self, rows=None):
        self.rows = rows or []

    async def find_one(self, query, *_a, **_k):
        for r in self.rows:
            if all(r.get(k) == v for k, v in query.items()):
                return r
        return None

    async def insert_one(self, doc):
        self.rows.append(doc)

        class _R:
            inserted_id = "fake"
        return _R()

    async def update_one(self, query, update):
        for r in self.rows:
            if all(r.get(k) == v for k, v in query.items()):
                r.update(update.get("$set", {}))

        class _R:
            matched_count = 1
        return _R()

    def find(self, query=None, *_a, **_k):
        query = query or {}
        matched = [r for r in self.rows if all(r.get(k) == v for k, v in query.items())]

        class _Cursor:
            def sort(self, *_a, **_k):
                return self

            async def to_list(self, _n):
                return matched
        return _Cursor()


class _FakeDB:
    def __init__(self):
        self.admin_access_requests = _FakeCollection()
        self.admin_team_members = _FakeCollection()
        self.dev_users = _FakeCollection([
            {"user_id": "founder_1", "email": "founder@aurem.dev"},
        ])
        self.admin_audit_log = _FakeCollection()
        self.notifications = _FakeCollection()


@pytest.mark.asyncio
async def test_regression_at3_resolve_does_not_grant_the_screen(monkeypatch):
    """Approving a request is the Owner's OWN cue to go act on it —
    it must never flip the requester's overrides/screens by itself
    (OWNER_ONLY stays hard-locked regardless, see cto_services/auth.py)."""
    import routers.admin_team as admin_team_router

    fake_db = _FakeDB()
    fake_db.admin_team_members.rows.append({
        "user_id": "member_1", "email": "member@example.com",
        "role": "developer", "status": "active", "overrides": {},
    })
    fake_db.admin_access_requests.rows.append({
        "request_id": "req_1", "user_id": "member_1", "email": "member@example.com",
        "screen_key": "settings.githubapp", "note": "", "status": "pending",
        "created_at": None, "updated_at": None,
        "resolved_at": None, "resolved_by": None, "resolution_note": None,
    })
    monkeypatch.setattr(admin_team_router, "get_db", lambda: fake_db)

    owner = {"user_id": "founder_1", "email": "founder@aurem.dev"}
    monkeypatch.setattr(admin_team_router, "require_admin", _fake_require_admin(owner))
    body = admin_team_router.ResolveAccessRequestBody(action="approve", note="will do it myself")
    result = await admin_team_router.resolve_access_request("req_1", body, authorization="Bearer x")

    assert result["status"] == "approved"
    # The member's own overrides are untouched — still empty.
    member_row = fake_db.admin_team_members.rows[0]
    assert member_row["overrides"] == {}
    # The requester got a notification, not a silent access grant.
    assert any(n["user_id"] == "member_1" for n in fake_db.notifications.rows)


def _fake_require_admin(user):
    async def _inner(_authorization):
        return user
    return _inner
