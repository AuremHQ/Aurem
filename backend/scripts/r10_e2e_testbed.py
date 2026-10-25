"""R10 real-GitHub E2E driver — pinned to the disposable testbed repo.

Phases (run in order, each idempotent-ish and logging to stdout):
  ship    — create auremcto/ship-* branch, commit one throwaway file,
            open draft PR labelled aura:ship, record loop_outcomes +
            loop_sessions exactly like loop_engine.confirm_ship does.
  merge   — mark PR ready, squash-merge it (simulates the user merging
            on GitHub), then deliver a signed pull_request webhook to the
            preview backend so merged_sha is captured.
  verify  — after the UI rollback: read GitHub main history, prove the
            revert commit is there and its parent chain contains merged_sha.
State is kept in /app/e2e-proof/R10-e2e/state.json.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")

import httpx  # noqa: E402

OWNER, REPO, BASE = "polarisbuiltinc-wq", "aurem-rollback-testbed", "main"
INSTALL_ID = 152797252
USER_EMAIL = "test@aurem.dev"
PROJECT_ID = "p_r10_testbed_e2e"
# TODO: set R10_E2E_STATE_DIR in .env (and gitignore .env)
STATE_DIR = os.environ.get("R10_E2E_STATE_DIR", "/app/e2e-proof/R10-e2e")
STATE = f"{STATE_DIR}/state.json"
GH = "https://api.github.com"


def _load():
    return json.load(open(STATE)) if os.path.exists(STATE) else {}


def _save(d):
    os.makedirs(STATE_DIR, exist_ok=True)
    json.dump(d, open(STATE, "w"), indent=1)


async def _db():
    from motor.motor_asyncio import AsyncIOMotorClient
    from services.github_app_config import ensure_configured_from_db
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    assert await ensure_configured_from_db(db), "GitHub App config missing"
    return db


async def _token():
    from services.github_app import get_installation_token
    tok, _ = await get_installation_token(INSTALL_ID)
    return tok


def _h(tok):
    return {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}


async def _head_sha(c, tok):
    r = await c.get(f"{GH}/repos/{OWNER}/{REPO}/git/refs/heads/{BASE}", headers=_h(tok))
    r.raise_for_status()
    return r.json()["object"]["sha"]


async def ship():
    db = await _db()
    tok = await _token()
    async with httpx.AsyncClient(timeout=30) as c:
        # Safety: the installation token must see the testbed repo, and we
        # never touch anything else.
        r = await c.get(f"{GH}/repos/{OWNER}/{REPO}", headers=_h(tok))
        r.raise_for_status()
        assert r.json()["full_name"] == f"{OWNER}/{REPO}"
        sha_before = await _head_sha(c, tok)
    print("main HEAD before ship:", sha_before)

    user = await db.dev_users.find_one({"email": USER_EMAIL}, {"user_id": 1})
    uid = user["user_id"]
    await db.ct