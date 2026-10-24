"""
test_regression_iter2026_10_app_install_account_switch.py

Founder-reported bug: "Add repository" for a SECOND project kept
landing on the FIRST project's already-installed GitHub account's
settings page instead of letting the user pick a different account.
Root cause: `github.com/apps/<slug>/installations/new` is unconditional
GitHub behavior for an already-authenticated browser — there is no
query param to force a fresh install screen for the SAME account.

Fix: every "Connect GitHub" click now routes through a new, lightweight
IDENTITY-ONLY OAuth round-trip first (`/github/oauth/connect?app_install=1`)
that always appends `prompt=select_account`, forcing GitHub's account
picker in the browser BEFORE chaining into the real App install URL.
Nothing from that identity exchange is persisted to `dev_users.github` —
it exists purely to switch the browser's github.com session.

Covers:
  1. `/connect?app_install=1` → redirects to github.com with
     `prompt=select_account` AND identity-only scopes (not full `repo`).
  2. The minted oauth_states row has `mode: "app_install_switch"`.
  3. Cancelling that step redirects to the App-install popup bridge
     (`/github/app/installed`), NOT `/projects` — this round-trip runs
     inside a small popup, not a full tab.
  4. On success, the callback does NOT write anything to
     `dev_users.github` and instead mints a fresh `gha:` state + redirects
     straight into `services.github_app.install_url()` — the exact URL
     `/github/app/install` itself would have produced.
  5. No regression: a plain `?signup=1` / connect-mode call (no
     `app_install`) still behaves exactly as before.
"""
from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

os.environ.setdefault("APP_URL", "https://auremcto.com")

from main import app  # noqa: E402
from cto_services.db import get_db, set_db  # noqa: E402
from cto_services.auth import create_token  # noqa: E402
import services.github_app as _ga  # noqa: E402


@pytest.fixture(autouse=True)
def _ensure_db_connected():
    """Pytest's ASGITransport doesn't run FastAPI's lifespan, so
    `main.py`'s own Mongo-connect-at-startup never fires here. Mirror it
    directly — same MONGO_URL/DB_NAME env vars main.py itself reads.
    Function-scoped (not module/session) so the Motor client is always
    bound to THIS test's own event loop — pytest-asyncio tears the loop
    down between tests, and a client built on a dead loop raises
    'Event loop is closed' on first use."""
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    set_db(client[os.environ.get("DB_NAME", "aurem_dev")])


@pytest.fixture
def db():
    return get_db()


@pytest.fixture
async def test_user_token(db):
    """Reuses the standing admin/founder test account already documented
    in /app/memory/test_credentials.md — no throwaway user needed."""
    user = await db.dev_users.find_one({"email": "test@aurem.dev"})
    assert user, "test@aurem.dev must exist (see memory/test_credentials.md)"
    return create_token(user["user_id"], user.get("email", "test@aurem.dev"), is_admin=True), user["user_id"]


@pytest.mark.asyncio
async def test_app_install_switch_forces_account_picker(test_user_token):
    token, _ = test_user_token
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(
            "/api/aurem-dev/github/oauth/connect",
            params={"app_install": "1", "auth": token},
            follow_redirects=False,
        )
    loc = r.headers.get("location", "")
    assert "github.com/login/oauth/authorize" in loc
    assert "prompt=select_account" in loc
    # Identity-only — must NOT request the broad `repo` scope just to
    # force an account switch.
    assert "scope=read%3Auser%2Cuser%3Aemail" in loc or "scope=read:user,user:email" in loc
    assert "scope=repo" not in loc


@pytest.mark.asyncio
async def test_app_install_switch_state_row_tagged_correctly(test_user_token, db):
    token, user_id = test_user_token
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(
            "/api/aurem-dev/github/oauth/connect",
            params={"app_install": "1", "auth": token},
            follow_redirects=False,
        )
    loc = r.headers.get("location", "")
    from urllib.parse import urlparse, parse_qs
    state = parse_qs(urlparse(loc).query)["state"][0]
    row = await db.oauth_states.find_one({"state": state})
    assert row is not None
    assert row["mode"] == "app_install_switch"
    assert row["user_id"] == user_id
    await db.oauth_states.delete_one({"state": state})


@pytest.mark.asyncio
async def test_cancelling_account_switch_closes_popup_not_projects_tab(db):
    state = f"{uuid.uuid4().hex}:switchnonce"
    await db.oauth_states.insert_one({
        "state": state, "mode": "app_install_switch",
        "user_id": "some_user", "origin": "https://auremcto.com",
    })
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(
            "/api/aurem-dev/github/oauth/callback",
            params={"error": "access_denied", "state": state},
            follow_redirects=False,
        )
    loc = r.headers.get("location", "")
    assert "/github/app/installed" in loc, f"expected the popup bridge, got {loc!r}"
    assert "status=err" in loc
    assert "/projects" not in loc
    # Single-use: the row must be consumed, not replayable.
    assert await db.oauth_states.find_one({"state": state}) is None


@pytest.mark.asyncio
async def test_successful_account_switch_chains_into_real_install_url_without_touching_dev_users(db):
    from services.github_app_config import ensure_configured_from_db
    await ensure_configured_from_db(db)

    user_id = "switch_test_user_001"
    await db.dev_users.update_one(
        {"user_id": user_id},
        {"$set": {"user_id": user_id, "email": "switchtest@example.com"}},
        upsert=True,
    )
    # Snapshot any prior github link so we can prove it's untouched.
    before = await db.dev_users.find_one({"user_id": user_id}, {"_id": 0, "github": 1})

    state = f"{user_id}:switchnonce2"
    await db.oauth_states.insert_one({
        "state": state, "mode": "app_install_switch", "user_id": user_id,
        "origin": "https://auremcto.com",
    })

    with patch("routers.github_oauth.exchange", new=AsyncMock(return_value="fake-token")), \
         patch("routers.github_oauth.gh_user", new=AsyncMock(return_value={
             "login": "a-different-github-account", "avatar_url": "", "name": "", "email": None,
         })):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get(
                "/api/aurem-dev/github/oauth/callback",
                params={"code": "fakecode", "state": state},
                follow_redirects=False,
            )

    loc = r.headers.get("location", "")
    assert "github.com/apps/" in loc and "/installations/new" in loc, (
        f"expected the real GitHub App install URL, got {loc!r}"
    )
    from urllib.parse import urlparse, parse_qs
    new_state = parse_qs(urlparse(loc).query)["state"][0]
    assert new_state.startswith("gha:"), f"expected a gha: state, got {new_state!r}"
    gha_row = await db.oauth_states.find_one({"state": new_state})
    assert gha_row is not None
    assert gha_row["kind"] == "github_app_install"
    assert gha_row["user_id"] == user_id

    after = await db.dev_users.find_one({"user_id": user_id}, {"_id": 0, "github": 1})
    assert after == before, (
        "the account-switch identity exchange must NEVER write to "
        "dev_users.github — it exists only to switch the browser's "
        "github.com session, not to link an identity"
    )

    await db.oauth_states.delete_one({"state": new_state})
    await db.dev_users.delete_one({"user_id": user_id})


@pytest.mark.asyncio
async def test_plain_connect_mode_unaffected_no_regression(test_user_token):
    """Without `app_install=1`, /connect must behave exactly as before:
    no `prompt=select_account` forced, full repo scope requested."""
    token, _ = test_user_token
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(
            "/api/aurem-dev/github/oauth/connect",
            params={"auth": token},
            follow_redirects=False,
        )
    loc = r.headers.get("location", "")
    assert "github.com/login/oauth/authorize" in loc
    assert "prompt=select_account" not in loc
    assert "scope=repo" in loc or "scope=repo%2Cread" in loc
