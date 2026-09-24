"""Actual callback -> Mongo -> add -> list persistence. GitHub/scans are MOCKED.

Authentication is a test-only account resolver; ownership checks stay real.
Each test owns and drops a unique database. No preview users or repos change.
"""
import time
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from dotenv import dotenv_values
from fastapi import FastAPI, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient


@pytest_asyncio.fixture
async def flow(monkeypatch):
    from routers import cto_projects as projects
    from routers import github_app as github_router
    from routers import github_funnel
    from routers.cto_projects import management
    from services import github_app, onboarding_first_scan, project_onboarding_scan

    env = dotenv_values(Path(__file__).parents[1] / ".env")
    mongo = AsyncIOMotorClient(env["MONGO_URL"], serverSelectionTimeoutMS=5000)
    db_name = f"{env['DB_NAME'][:20]}_connect_test_{uuid.uuid4().hex[:10]}"
    db = mongo[db_name]

    async def identity(auth):
        if auth not in ("Bearer account-a", "Bearer account-b"):
            raise HTTPException(401, "test session required")
        return {"user_id": auth.split()[1]}

    monkeypatch.setattr(github_router, "current_dev", identity)
    monkeypatch.setattr(projects, "current_dev", identity)
    monkeypatch.setattr(github_router, "require_db", lambda: db)
    monkeypatch.setattr(projects, "require_db", lambda: db)
    monkeypatch.setattr(github_router, "_app_configured", lambda: True)
    monkeypatch.setattr(github_router, "_funnel_track", AsyncMock())
    monkeypatch.setattr(github_funnel, "track_server_side", AsyncMock())
    monkeypatch.setattr(github_router, "_fetch_installation_meta", AsyncMock(return_value={
        "id": 42001, "account": {"id": 100, "login": "fixture-org", "type": "Organization"},
        "repository_selection": "selected", "permissions": {"contents": "write"},
    }))
    repository = {"id": 701, "full_name": "fixture-org/new-repo", "default_branch": "develop", "private": True}
    monkeypatch.setattr(github_app, "list_installation_repos", AsyncMock(return_value=[repository]))
    probe = AsyncMock(return_value=repository)
    monkeypatch.setattr(github_app, "get_repo_via_installation", probe)
    monkeypatch.setattr(github_app, "get_installation_token", AsyncMock(return_value=("MOCKED-provider-token", time.time() + 60)))
    monkeypatch.setattr(management, "_run_project_indexing", AsyncMock())
    monkeypatch.setattr(project_onboarding_scan, "run_onboarding_scan", AsyncMock())
    monkeypatch.setattr(onboarding_first_scan, "trigger_first_scan", AsyncMock())

    app = FastAPI()
    app.include_router(github_router.router, prefix="/api/aurem-dev")
    app.include_router(projects.router, prefix="/api/aurem-dev")
    try:
        await mongo.admin.command("ping")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
            yield client, db, mongo, probe
    finally:
        await mongo.drop_database(db_name)
        mongo.close()


@pytest.mark.asyncio
async def test_regression_callback_then_explicit_add_persists_across_clients(flow):
    client, db, _mongo, probe = flow
    state = f"gha:account-a:{uuid.uuid4().hex}"
    await db.oauth_states.insert_one({
        "state": state, "kind": "github_app_install", "used": False,
        "user_id": "account-a", "expires_at": time.time() + 600,
    })
    callback = await client.get("/api/aurem-dev/github/app/callback", params={
        "state": state, "installation_id": 42001, "setup_action": "install",
    })
    assert callback.status_code == 302
    assert "status=success" in callback.headers["location"]
    installation = await db.github_installations.find_one({"installation_id": 42001})
    assert installation["user_id"] == "account-a"
    assert await db.cto_projects.count_documents({}) == 0  # access grant is not import-all

    body = {"name": "New project", "github_url": "https://github.com/fixture-org/new-repo",
            "installation_id": 42001, "branch": "develop"}
    headers = {"Authorization": "Bearer account-a"}
    added = await client.post("/api/aurem-dev/cto/projects/add", headers=headers, json=body)
    assert added.status_code == 200, added.text
    project_id = added.json()["project_id"]
    assert "_id" not in added.json()
    probe.assert_awaited_once_with(42001, "fixture-org", "new-repo")

    # A new database client proves persistence is not an in-memory response.
    cfg = dotenv_values(Path(__file__).parents[1] / ".env")
    fresh_client = AsyncIOMotorClient(cfg["MONGO_URL"])
    try:
        persisted = await fresh_client[db.name].cto_projects.find_one({"project_id": project_id})
        assert persisted["user_id"] == "account-a"
        assert persisted["branch"] == "develop"
        assert persisted["installation_id"] == 42001
    finally:
        fresh_client.close()

    listed = await client.get("/api/aurem-dev/cto/projects/list", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["projects"][0]["project_id"] == project_id
    assert "github_token" not in listed.json()["projects"][0]
    assert "_id" not in listed.json()["projects"][0]
    duplicate = await client.post("/api/aurem-dev/cto/projects/add", headers=headers, json=body)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["project_id"] == project_id
    assert await db.cto_projects.count_documents({}) == 1
    other = await client.get("/api/aurem-dev/cto/projects/list", headers={"Authorization": "Bearer account-b"})
    assert other.json()["projects"] == []


@pytest.mark.asyncio
async def test_regression_foreign_installation_cannot_create_a_project(flow):
    client, db, _, probe = flow
    await db.github_installations.insert_one({
        "installation_id": 42001, "user_id": "account-a", "active": True,
    })
    response = await client.post("/api/aurem-dev/cto/projects/add", headers={"Authorization": "Bearer account-b"}, json={
        "name": "Not mine", "github_url": "https://github.com/fixture-org/new-repo", "installation_id": 42001,
    })
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "installation_not_found_or_inactive"
    assert await db.cto_projects.count_documents({}) == 0
    probe.assert_not_awaited()