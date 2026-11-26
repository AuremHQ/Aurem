"""tests/test_regression_iter2026_35_single_fix_branch_integration.py

FIX_one_branch_for_all_fixes — integration + mixed + H4 concurrency.

  * Integration through the API layer: POST /fix-pipeline/bulk with
    5 findings across 3 files (2 files get 2 findings each) drives
    the REAL apply_finding_fix (not stubbed) end-to-end through a
    mocked GitHub+LLM transport boundary — proves ONE branch, ONE PR,
    5 finding_fixes ledger rows, both same-file fixes present in the
    final committed content, and the bulk summary carries exactly one
    batch link.
  * Mixed: a single fix called outside any bulk job still joins the
    one open batch for that (user, project); after the batch's PR
    merges (webhook), the NEXT fix starts a fresh batch.
  * H4: asyncio.gather across 10 different-file fixes, and 2 fixes on
    the SAME file, both land on exactly one batch/one PR with no lost
    update.

GitHub mocked at the services.loop_safety / services.github_api_writer
boundary; Mongo via the repo's dict-fake-DB pattern (same convention
as tests/test_iter212m121_fix_pipeline.py).
"""
from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

import services.finding_fix_applier as ffa
import services.fix_batches as fb
import services.loop_safety as ls

pytestmark = pytest.mark.asyncio


# ───────────────────────────── fake DB double (test_iter212m121 pattern) ──
class _FakeColl:
    def __init__(self, rows=None):
        self.rows = rows or []

    async def find_one(self, query, projection=None):
        for r in self.rows:
            ok = all(r.get(k) == v for k, v in query.items() if not isinstance(v, dict))
            if ok:
                return dict(r)
        return None

    async def find_one_and_update(self, query, update, return_document=False):
        for r in self.rows:
            if all(r.get(k) == v for k, v in query.items() if not isinstance(v, dict)):
                for k, v in update.get("$inc", {}).items():
                    r[k] = r.get(k, 0) + v
                for k, v in update.get("$push", {}).items():
                    r.setdefault(k, []).append(v)
                for k, v in update.get("$set", {}).items():
                    r[k] = v
                return dict(r)
        return None

    async def update_one(self, query, update, upsert=False):
        for r in self.rows:
            if all(r.get(k) == v for k, v in query.items() if not isinstance(v, dict)):
                inc = update.get("$inc") or {}
                for k, v in inc.items():
                    r[k] = (r.get(k) or 0) + v
                for k, v in (update.get("$set") or {}).items():
                    r[k] = v
                class _R:
                    modified_count = 1
                    upserted_id = None
                return _R()
        class _R:
            modified_count = 0
            upserted_id = None
        return _R()

    async def update_many(self, query, update):
        for r in self.rows:
            if all(r.get(k) == v for k, v in query.items() if not isinstance(v, dict)):
                for k, v in (update.get("$set") or {}).items():
                    r[k] = v

    async def insert_one(self, doc):
        self.rows.append(dict(doc))
        class _R:
            inserted_id = "fake"
        return _R()

    def aggregate(self, pipeline):
        class _AggCur:
            def __init__(self, items):
                self.items = items
            async def to_list(self, length=None):
                return list(self.items)[: (length or len(self.items))]
            def __aiter__(self):
                async def _gen():
                    for x in []:
                        yield x
                return _gen()
        return _AggCur([])

    async def count_documents(self, query):
        return 0

    def find(self, query, projection=None):
        matched = [
            dict(r) for r in self.rows
            if all(r.get(k) == v for k, v in (query or {}).items() if not isinstance(v, dict))
        ]
        class _Cur:
            def __init__(self, items):
                self.items = items
            def sort(self, *a, **k):
                return self
            def limit(self, *a, **k):
                return self
            async def to_list(self, length=None):
                return list(self.items)[: (length if length is not None else len(self.items))]
            def __aiter__(self):
                async def _gen():
                    for x in self.items:
                        yield x
                return _gen()
        return _Cur(matched)


class _FakeDB:
    def __init__(self, user_doc=None, projects=None):
        self.dev_users = _FakeColl([user_doc] if user_doc else [])
        self.cto_projects = _FakeColl(projects or [])
        self.finding_fixes = _FakeColl()
        self.fix_batches = _FakeColl()
        self.cto_tasks = _FakeColl()
        self.vanguard_ci_findings = _FakeColl()
        self.scan_fix_usage = _FakeColl()
        self.scan_fix_jobs = _FakeColl()
        self.topup_alerts = _FakeColl()
        self.incidents = _FakeColl()
        self.loop_events = _FakeColl()
        self.cto_open_findings = _FakeColl()

    def __getattr__(self, name):
        coll = _FakeColl()
        object.__setattr__(self, name, coll)
        return coll

    def __getitem__(self, name):
        return getattr(self, name)


@pytest.fixture(autouse=True)
def _reset_locks():
    fb._batch_locks.clear()
    yield
    fb._batch_locks.clear()


def _mock_pat(monkeypatch):
    async def _get_token(proj):
        return "ghp_tok", None, None
    monkeypatch.setattr("services.pat_vault.get_repo_token_or_error", _get_token)


def _mock_github_boundary(monkeypatch, *, pr_counter=None):
    """GitHub mock transport — matches the boundary services.
    fix_batches / finding_fix_applier actually call (not apply_
    finding_fix itself), so the REAL batching + commit logic runs."""
    counter = pr_counter if pr_counter is not None else [100]

    async def _create_branch(**kw):
        return True, None

    async def _open_pr(**kw):
        counter[0] += 1
        n = counter[0]
        return {"ok": True, "pr_url": f"https://github.com/o/r/pull/{n}",
                "pr_number": n, "reused": False, "error": None}

    async def _add_label(**kw):
        return True, None

    async def _update_body(**kw):
        return True, None

    async def _get_status(**kw):
        return {"ok": True, "merged": False, "state": "open", "merge_commit_sha": None}

    async def _close_pr(**kw):
        return True, None

    monkeypatch.setattr(ls, "create_or_reuse_branch", _create_branch)
    monkeypatch.setattr(ls, "open_or_reuse_pr", _open_pr)
    monkeypatch.setattr(ls, "add_pr_label", _add_label)
    monkeypatch.setattr(ls, "update_pr_body", _update_body)
    monkeypatch.setattr(ls, "get_pr_status", _get_status)
    monkeypatch.setattr(ls, "close_pr", _close_pr)
    return counter


def _mock_llm_boundary(monkeypatch, branch_content: dict):
    """In-memory git — branch -> {path: content}. Content fetch/commit
    both operate against this dict so same-file sequential writes are
    visible to the next read, exactly like real GitHub would behave."""

    async def _fetch(owner, repo, branch, path, token):
        files = branch_content.get(branch) or {}
        if path in files:
            return files[path], None
        return "", "file_not_found"

    async def _llm(*, path, current_content, finding, user_id, db, review_mode=None):
        marker = f"FIXED[{finding.get('rule_id')}]"
        if marker in current_content:
            return "", "llm_no_change"
        return current_content + f"\n{marker}\n", None

    async def _llm_multi(*, path, current_content, findings, user_id, db, review_mode=None):
        # Part 2b F2 — apply_file_fixes's one-call-per-file path.
        # Appends the SAME marker convention for every finding passed
        # in this one call, so _not_present's content-aware check
        # (shared by both apply_finding_fix and apply_file_fixes)
        # works identically either way.
        out = current_content
        for f in findings:
            marker = f"FIXED[{f.get('rule_id')}]"
            if marker not in out:
                out += f"\n{marker}\n"
        if out == current_content:
            return "", "llm_no_change"
        return out, None

    def _not_present(patched_content, path, finding):
        # Part 2b R1 — this is now called BOTH as a pre-check (on the
        # unpatched content, before the LLM runs) and as the original
        # post-patch validation. Content-aware: "still present" means
        # the fix marker this fake LLM adds isn't there yet.
        marker = f"FIXED[{finding.get('rule_id')}]"
        return marker not in patched_content

    commits = []

    async def _commit(*, owner, repo, branch, token, files, commit_message,
                       author_name, author_email, progress):
        commits.append({"branch": branch, "files": dict(files)})
        branch_content.setdefault(branch, {}).update(files)
        n = len(commits)
        return {"sha": f"s{n}", "full_sha": f"s{n}" * 8,
                "html_url": f"https://github.com/o/r/commit/s{n}"}

    monkeypatch.setattr(ffa, "_fetch_file_content", _fetch)
    monkeypatch.setattr(ffa, "_generate_patched_content", _llm)
    monkeypatch.setattr(ffa, "_generate_patched_content_multi", _llm_multi)
    monkeypatch.setattr(ffa, "_finding_still_present", _not_present)
    monkeypatch.setattr("services.github_api_writer.commit_files", _commit)
    return commits


def _project(owner="o", repo="r", branch="main", user_id="u_founder", project_id="p1"):
    return {"project_id": project_id, "user_id": user_id,
            "github_owner": owner, "github_repo": repo, "github_branch": branch}


# ─────────────────────────── API-layer integration ────────────────────────
@pytest.fixture
def client_founder(monkeypatch):
    from cto_services import db as cto_db
    from main import app

    user_doc = {"user_id": "u_founder", "email": "founder@aurem.dev",
                "tokens_remaining": 5, "tier": "founder",
                "is_admin": True, "is_unlimited": True}
    db = _FakeDB(user_doc=user_doc, projects=[_project()])
    with TestClient(app) as c:
        cto_db.set_db(db)
        from cto_services.auth import create_token
        token = create_token("u_founder", "founder@aurem.dev", is_admin=True)
        c.headers["Authorization"] = f"Bearer {token}"
        yield c, db


def _async_true(**kw):
    async def _f():
        return True
    return _f()


class TestBulkIntegrationThroughApiLayer:
    def test_five_findings_three_files_one_branch_one_pr(self, client_founder, monkeypatch):
        c, db = client_founder
        _mock_pat(monkeypatch)
        _mock_github_boundary(monkeypatch)
        branch_content = {"main": {
            "a.py": "orig_a", "b.py": "orig_b", "c.py": "orig_c",
        }}
        commits = _mock_llm_boundary(monkeypatch, branch_content)
        monkeypatch.setattr(
            "routers.fix_pipeline._verify_commit_exists",
            lambda **kw: _async_true(),
        )
        monkeypatch.setattr("routers.fix_pipeline._BULK_INTER_FIX_DELAY_S", 0.0)

        findings = [
            {"id": "f1", "rule_id": "rule_a1", "file": "a.py", "line": 1,
             "severity": "high", "category": "vanguard"},
            {"id": "f2", "rule_id": "rule_a2", "file": "a.py", "line": 2,
             "severity": "high", "category": "vanguard"},
            {"id": "f3", "rule_id": "rule_b1", "file": "b.py", "line": 1,
             "severity": "medium", "category": "vanguard"},
            {"id": "f4", "rule_id": "rule_c1", "file": "c.py", "line": 1,
             "severity": "low", "category": "vanguard"},
            {"id": "f5", "rule_id": "rule_c2", "file": "c.py", "line": 2,
             "severity": "low", "category": "vanguard"},
        ]
        r = c.post("/api/aurem-dev/fix-pipeline/bulk",
                   json={"project_id": "p1", "findings": findings})
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]

        summary = None
        for _ in range(50):
            time.sleep(0.1)
            s = c.get(f"/api/aurem-dev/fix-pipeline/summary/{job_id}")
            if s.status_code == 200:
                summary = s.json()
                if summary.get("closed_at"):
                    break
        assert summary is not None, "bulk job never closed"
        assert summary["completed"] == 5, summary
        assert summary["failed"] == 0

        # ONE batch, ONE PR — the core claim of this whole task.
        assert len(db.fix_batches.rows) == 1
        batch = db.fix_batches.rows[0]
        assert batch["fix_count"] == 5
        assert batch["status"] == "open"
        batch_branch = batch["branch"]

        # 5 ledger rows, all pointing at the same batch_id/branch/pr.
        assert len(db.finding_fixes.rows) == 5
        for row in db.finding_fixes.rows:
            assert row["batch_id"] == batch["batch_id"]
            assert row["branch"] == batch_branch
            assert row["pr_url"] == batch["pr_url"]

        # Both same-file fixes landed — a.py and c.py each carry BOTH
        # of their findings' markers (H1: 2nd fix read the 1st's patch).
        final_a = branch_content[batch_branch]["a.py"]
        assert "FIXED[rule_a1]" in final_a and "FIXED[rule_a2]" in final_a
        final_c = branch_content[batch_branch]["c.py"]
        assert "FIXED[rule_c1]" in final_c and "FIXED[rule_c2]" in final_c
        # Part 2b F2 — ONE commit per FILE now (not per finding): 3
        # files (a.py x2 findings, b.py x1, c.py x2 findings) = 3
        # commits total, all on the same shared branch. This is the
        # actual R2 fix — a.py's 2 findings and c.py's 2 findings
        # each got ONE LLM call + ONE commit covering both.
        assert len(commits) == 3
        assert {c["branch"] for c in commits} == {batch_branch}
        assert {cm["branch"] for cm in commits} == {batch_branch}


# ───────────────────────────── mixed scenarios ─────────────────────────────
class TestMixedBatchJoin:
    async def test_single_fix_outside_scan_joins_same_open_batch(self, monkeypatch):
        """A single-fix click (not part of any bulk job) uses the
        exact same apply_finding_fix entry point — design item 12:
        single fixes outside a scan session DO join the open batch,
        by construction (there is no separate code path)."""
        _mock_pat(monkeypatch)
        _mock_github_boundary(monkeypatch)
        branch_content = {"main": {"x.py": "orig_x", "y.py": "orig_y"}}
        _mock_llm_boundary(monkeypatch, branch_content)
        db = _FakeDB(projects=[_project(project_id="p2")])

        res1 = await ffa.apply_finding_fix(
            db=db, user={"user_id": "u_founder"}, project_id="p2",
            finding={"rule_id": "r1", "file": "x.py", "line": 1},
        )
        res2 = await ffa.apply_finding_fix(
            db=db, user={"user_id": "u_founder"}, project_id="p2",
            finding={"rule_id": "r2", "file": "y.py", "line": 1},
        )
        assert res1["ok"] and res2["ok"]
        assert res1["batch_id"] == res2["batch_id"]
        assert len(db.fix_batches.rows) == 1

    async def test_after_merge_webhook_next_fix_starts_new_batch(self, monkeypatch):
        _mock_pat(monkeypatch)
        _mock_github_boundary(monkeypatch)
        branch_content = {"main": {"x.py": "orig_x", "z.py": "orig_z"}}
        _mock_llm_boundary(monkeypatch, branch_content)
        db = _FakeDB(projects=[_project(project_id="p3")])

        res1 = await ffa.apply_finding_fix(
            db=db, user={"user_id": "u_founder"}, project_id="p3",
            finding={"rule_id": "r1", "file": "x.py", "line": 1},
        )
        assert res1["ok"]
        old_branch = res1["branch"]

        await ls.dispatch_pull_request_webhook(
            db, action="closed",
            payload={
                "pull_request": {
                    "merged": True,
                    "head": {"ref": old_branch},
                    "labels": [{"name": "aura:fix-batch"}],
                },
            },
        )
        batch_after = await db.fix_batches.find_one({"branch": old_branch})
        assert batch_after["status"] == "merged"

        res2 = await ffa.apply_finding_fix(
            db=db, user={"user_id": "u_founder"}, project_id="p3",
            finding={"rule_id": "r2", "file": "z.py", "line": 1},
        )
        assert res2["ok"]
        assert res2["branch"] != old_branch
        assert res2["batch_id"] != res1["batch_id"]
        open_batches = [b for b in db.fix_batches.rows if b["status"] == "open"]
        assert len(open_batches) == 1


# ─────────────────────────────── H4 concurrency ────────────────────────────
class TestH4ConcurrencyGather:
    async def test_ten_different_files_one_batch_one_pr_all_committed(self, monkeypatch):
        _mock_pat(monkeypatch)
        _mock_github_boundary(monkeypatch)
        branch_content = {"main": {f"f{i}.py": f"orig_{i}" for i in range(10)}}
        commits = _mock_llm_boundary(monkeypatch, branch_content)
        db = _FakeDB(projects=[_project(project_id="p4")])

        results = await asyncio.gather(*[
            ffa.apply_finding_fix(
                db=db, user={"user_id": "u_founder"}, project_id="p4",
                finding={"rule_id": f"r{i}", "file": f"f{i}.py", "line": 1},
            )
            for i in range(10)
        ])
        assert all(r["ok"] for r in results)
        batch_ids = {r["batch_id"] for r in results}
        assert len(batch_ids) == 1
        assert len(db.fix_batches.rows) == 1
        assert db.fix_batches.rows[0]["fix_count"] == 10
        assert len(commits) == 10
        batch_branch = next(iter(batch_ids and [results[0]["branch"]]))
        for i in range(10):
            assert f"FIXED[r{i}]" in branch_content[batch_branch][f"f{i}.py"]

    async def test_two_fixes_same_file_concurrent_no_lost_update(self, monkeypatch):
        _mock_pat(monkeypatch)
        _mock_github_boundary(monkeypatch)
        branch_content = {"main": {"shared.py": "orig_shared"}}
        commits = _mock_llm_boundary(monkeypatch, branch_content)
        db = _FakeDB(projects=[_project(project_id="p5")])

        r1, r2 = await asyncio.gather(
            ffa.apply_finding_fix(
                db=db, user={"user_id": "u_founder"}, project_id="p5",
                finding={"rule_id": "same_a", "file": "shared.py", "line": 1},
            ),
            ffa.apply_finding_fix(
                db=db, user={"user_id": "u_founder"}, project_id="p5",
                finding={"rule_id": "same_b", "file": "shared.py", "line": 2},
            ),
        )
        assert r1["ok"] and r2["ok"]
        assert r1["batch_id"] == r2["batch_id"]
        assert len(db.fix_batches.rows) == 1
        assert db.fix_batches.rows[0]["fix_count"] == 2
        # No lost update — the coarse per-batch lock (H4) serializes
        # these two commits, so whichever ran second read the first's
        # already-landed content.
        batch_branch = r1["branch"]
        final = branch_content[batch_branch]["shared.py"]
        assert "FIXED[same_a]" in final
        assert "FIXED[same_b]" in final
        assert len(commits) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
