"""routers/rollback_v2.py — Pillar 1 admin surface (founder-approved 2026-06).

Snapshot-based two-phase rollback + attempts ledger + synthetic drill.
All endpoints admin-gated (same require_admin_dep as other admin routes).
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from cto_services.auth import require_admin_dep
from cto_services.db import get_db

router = APIRouter(prefix="/admin/rollback2", tags=["Admin-Rollback-v2"],
                   dependencies=[Depends(require_admin_dep)])


def _db(request: Request):
    return get_db()


async def _resolve_token(repo: str) -> str:
    """2026-09-26 — migrated off the now-expired GITHUB_ACTIONS_TOKEN
    PAT onto the same GitHub App installation-token path the rest of
    the product uses (Task 6). AUREM_DRILL_TOKEN (a separate,
    deliberately-scoped drill-only PAT) stays as an explicit fallback
    since it predates the App and is still used by the synthetic
    drill's own isolated test repo, distinct from real rollback ops."""
    if "/" in repo:
        owner, name = repo.split("/", 1)
        from services.github_app import get_token_for_repo
        token, _reason = await get_token_for_repo(owner, name)
        if token:
            return token
    return os.environ.get("AUREM_DRILL_TOKEN", "").strip()


class SnapshotReq(BaseModel):
    owner: str
    repo: str
    branch: str = "main"
    file_paths: list[str] = Field(min_length=1)


class PreviewReq(BaseModel):
    snapshot_id: str


class ExecuteReq(BaseModel):
    snapshot_id: str
    preview_token: str
    confirm: bool = False
    initiated_by: str = "admin"


@router.post("/snapshot")
async def create_snapshot_ep(req: SnapshotReq, request: Request):
    from services.rollback_snapshot import create_snapshot
    token = await _resolve_token(f"{req.owner}/{req.repo}")
    row = await create_snapshot(
        _db(request), owner=req.owner, repo=req.repo, branch=req.branch,
        token=token, file_paths=req.file_paths, trigger="manual",
    )
    return {"ok": True, "snapshot": row}


@router.get("/snapshots")
async def list_snapshots(request: Request, limit: int = 20):
    out = []
    async for r in _db(request).rollback_snapshots.find(
            {}, {"_id": 0}).sort("created_at", -1).limit(min(limit, 100)):
        out.append(r)
    return {"snapshots": out}


async def _repo_for_snapshot(request: Request, snapshot_id: str) -> str:
    row = await _db(request).rollback_snapshots.find_one(
        {"snapshot_id": snapshot_id}, {"_id": 0, "repo": 1})
    return (row or {}).get("repo", "")


@router.post("/preview")
async def preview_ep(req: PreviewReq, request: Request):
    from services.rollback_two_phase import preview_rollback
    repo = await _repo_for_snapshot(request, req.snapshot_id)
    return await preview_rollback(
        _db(request), snapshot_id=req.snapshot_id,
        token=await _resolve_token(repo))


@router.post("/execute")
async def execute_ep(req: ExecuteReq, request: Request):
    from services.rollback_two_phase import execute_rollback_from_snapshot
    repo = await _repo_for_snapshot(request, req.snapshot_id)
    return await execute_rollback_from_snapshot(
        _db(request), snapshot_id=req.snapshot_id,
        preview_token=req.preview_token,
        initiated_by=req.initiated_by, token=await _resolve_token(repo),
        confirm=req.confirm,
    )


@router.get("/attempts")
async def list_attempts(request: Request, limit: int = 30):
    out = []
    async for r in _db(request).rollback_attempts.find(
            {}, {"_id": 0}).sort("timestamp", -1).limit(min(limit, 100)):
        out.append(r)
    return {"attempts": out}


@router.post("/drill")
async def run_drill_ep(request: Request):
    from services.rollback_drill import run_drill
    return await run_drill(_db(request), initiated_by="admin")


@router.get("/drills")
async def list_drills(request: Request, limit: int = 10):
    out = []
    async for r in _db(request).rollback_drills.find(
            {}, {"_id": 0}).sort("created_at", -1).limit(min(limit, 50)):
        out.append(r)
    return {"drills": out}
