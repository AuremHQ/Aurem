"""tests/test_regression_iter2026_51_part2c_c3_no_content_leak_measurement.py

Part 2c C3 [Med] — apply_finding_fix/apply_file_fixes still RETURN
`original_content`/`patched_content` in their result dict, and
fix_pipeline.py's `_file_result_to_legacy_shape` (~line 541) passes
them on to the per-finding `res` dict used throughout `_run_bulk_job`.
The review asked us to CONFIRM whether any of that reaches a Mongo
doc or a captured log line, not assume either way.

MEASUREMENT (not a fix): drives a real bulk-fix job end-to-end
through the API layer (TestClient, GitHub mocked at the loop_safety/
github_api_writer boundary, same convention as test_regression_iter
2026_35) on a file containing a realistic fake secret, then greps the
literal secret value across EVERY Mongo collection the job touches
(fix_jobs, finding_fixes, fix_batches, brain_calls) AND every log
record pytest's `caplog` captured during the run.

Code-path trace performed before writing this test (documented in the
report, not just asserted here):
  - services.fix_job_manager.emit()'s "fix-done" handler builds an
    explicit WHITELIST for `_JOBS[job_id]["results"]`
    (finding_id/ok/commit_sha/full_sha/html_url/file/rule_id/verified)
    — no content field. persist_event() only ever snapshots that
    whitelisted list to `fix_jobs`.
  - services.ora_fix_learning.record_fix_outcome() builds its own
    explicit whitelist `row` from `finding`/`result` — it is handed
    `result={**res, ...}` (which DOES include the content fields)
    but only ever reads `result.get("full_sha")` etc., never spreads
    the dict into the persisted row.
  - services.brain_gateway.attach_outcome_for_task() receives only a
    `task_id` string and an `outcome` enum value — never `res`.
  - services.brain_gateway's OWN docstring: "No raw prompt/response
    text is stored by default... Full-text storage is a separate,
    hardcoded-off flag (`_TEXT_STORAGE_ENABLED`)" — verified
    `_TEXT_STORAGE_ENABLED = False` in the current tree.
  - fix_batches.build_pr_body() only renders rule_id/file/line/
    severity/commit_sha/status — never content.
  - routers/fix_pipeline.py's own `fix-done` SSE emit() call passes
    explicit named kwargs, none of which is original_content/
    patched_content; the "fix-diff" event IS content-derived but
    already redacted per-line (Part 2b R3, test_regression_iter
    2026_44).

This test is the live measurement proving that trace, not a
substitute for it — if any future change starts spreading `res`
wholesale into a persisted dict, this test is what catches it.
"""
from __future__ import annotations

import logging
import time

import pytest
from fastapi.testclient import TestClient

import services.finding_fix_applier as ffa
import services.fix_batches as fb
import services.loop_safety as ls

pytestmark = pytest.mark.asyncio

_FAKE_SECRET = "sk_live_C3_MEASUREMENT_FAKE_SECRET_9f8e7d6c5b4a"


class _FakeColl:
    def __init__(self, rows=None):
        self.rows = rows or []

    async def find_one(self, query, projection=None):
        for r in self.rows:
            if all(r.get(k) == v for k, v in query.items() if not isinstance(v, dict)):
                return dict(r)
        return None

    async def find_one_and_update(self, query, update, return_document=False):
        for r in self.rows:
            if all(r.get(k) == v for k, v in query.items() if not isinstance(v, dict)):
                for k, v in update.get("$inc", {}).items():
                    r[k] = r.get(k, 0) + v
                for k, v in update.get("$push", {}).items():
                    if isinstance(v, dict) and "$each" in v:
                        r.setdefault(k, []).extend(v["$each"])
                    else:
                        r.setdefault(k, []).append(v)
                for k, v in update.get("$set", {}).items():
                    r[k] = v
                return dict(r)
        return None

    async def update_one(self, query, update, upsert=False):
        for r in self.rows:
            if all(r.get(k) == v for k, v in query.items() if not isinstance(v, dict)):
                for k, v in (update.get("$inc") or {}).items():
                    r[k] = (r.get(k) or 0) + v
                for k, v in (update.get("$set") or {}).items():
                    r[k] = v

                class _R:
                    modified_count = 1
                return _R()
        if upsert:
            doc = dict(query)
            doc.update(update.get("$set") or {})
            self.rows.append(doc)

        class _R:
            modified_count = 0
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

    async def count_documents(self, query):
        return len(self.rows)

    def aggregate(self, pipeline):
        class _AggCur:
            async def to_list(self, length=None):
                return []

            def __aiter__(self):
                async def _gen():
                    for x in []:
                        yield x
                return _gen()
        return _AggCur()


class _FakeDB:
    def __init__(self, user_doc=None, projects=None):
        self.dev_users = _FakeColl([user_doc] if user_doc else [])
        self.cto_projects = _FakeColl(projects or [])
        self.finding_fixes = _FakeColl()
        self.fix_batches = _FakeColl()
        self.fix_jobs = _FakeColl()
        self.brain_calls = _FakeColl()
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


def _mock_github_boundary(monkeypatch):
    async def _create_branch(**kw):
        return True, None

    async def _open_pr(**kw):
        return {"ok": True, "pr_url": "https://github.com/o/r/pull/101",
                "pr_number": 101, "reused": False, "error": None}

    async def _add_label(**kw):
        return True, None

    async def _update_body(**kw):
        return True, None

    async def _get_status(**kw):
        return {"ok": True, "merged": False, "state": "open", "merge_commit_sha": None}

    monkeypatch.setattr(ls, "create_or_reuse_branch", _create_branch)
    monkeypatch.setattr(ls, "open_or_reuse_pr", _open_pr)
    monkeypatch.setattr(ls, "add_pr_label", _add_label)
    monkeypatch.setattr(ls, "update_pr_body", _update_body)
    monkeypatch.setattr(ls, "get_pr_status", _get_status)


def _mock_llm_and_git(monkeypatch, branch_content: dict):
    async def _fetch(owner, repo, branch, path, token):
        files = branch_content.get(branch) or {}
        if path in files:
            return files[path], None
        return "", "file_not_found"

    async def _llm_multi(*, path, current_content, findings, user_id, db):
        # The LLM call's OWN prompt embeds current_content (the
        # secret) — that's expected and is the LLM provider's
        # business, not persisted anywhere by AUREM. The "fix"
        # removes the secret.
        fixed = current_content.replace(_FAKE_SECRET, "os.environ.get('API_KEY')")
        return fixed, None

    def _not_present(patched_content, path, finding):
        return _FAKE_SECRET in patched_content

    async def _get_branch_head(owner, repo, branch, token):
        return {"sha": "head_sha"}

    commits = []

    async def _commit(*, owner, repo, branch, token, files, commit_message,
                       author_name, author_email, progress):
        commits.append({"branch": branch, "files": dict(files),
                         "commit_message": commit_message})
        branch_content.setdefault(branch, {}).update(files)
        n = len(commits)
        return {"sha": f"s{n}", "full_sha": f"s{n}" * 8,
                "html_url": f"https://github.com/o/r/commit/s{n}"}

    monkeypatch.setattr(ffa, "_fetch_file_content", _fetch)
    monkeypatch.setattr(ffa, "_generate_patched_content_multi", _llm_multi)
    monkeypatch.setattr(ffa, "_finding_still_present", _not_present)
    monkeypatch.setattr("services.github_api_writer._get_branch_head", _get_branch_head)
    monkeypatch.setattr("services.github_api_writer.commit_files", _commit)
    return commits


def _project(owner="o", repo="r", branch="main", user_id="u_founder", project_id="p1"):
    return {"project_id": project_id, "user_id": user_id,
            "github_owner": owner, "github_repo": repo, "github_branch": branch}


def _async_true(**kw):
    async def _f():
        return True
    return _f()


class TestC3NoContentLeak:
    def test_fake_secret_never_reaches_any_mongo_collection_or_log_line(
        self, monkeypatch, caplog,
    ):
        from cto_services import db as cto_db
        from main import app

        user_doc = {"user_id": "u_founder", "email": "founder@aurem.dev",
                    "tokens_remaining": 5, "tier": "founder",
                    "is_admin": True, "is_unlimited": True}
        db = _FakeDB(user_doc=user_doc, projects=[_project()])

        _mock_pat(monkeypatch)
        _mock_github_boundary(monkeypatch)
        branch_content = {"main": {
            "config.py": f'API_KEY = "{_FAKE_SECRET}"\nOTHER = "y"\n',
        }}
        _mock_llm_and_git(monkeypatch, branch_content)
        monkeypatch.setattr(
            "routers.fix_pipeline._verify_commit_exists",
            lambda **kw: _async_true(),
        )
        monkeypatch.setattr("routers.fix_pipeline._BULK_INTER_FIX_DELAY_S", 0.0)

        findings = [
            {"id": "f1", "rule_id": "hardcoded_secret", "file": "config.py",
             "line": 1, "severity": "critical", "category": "vanguard"},
        ]

        caplog.set_level(logging.DEBUG)
        with TestClient(app) as c:
            cto_db.set_db(db)
            from cto_services.auth import create_token
            token = create_token("u_founder", "founder@aurem.dev", is_admin=True)
            c.headers["Authorization"] = f"Bearer {token}"

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
            assert summary.get("completed") == 1, summary

        # THE MEASUREMENT — scan every Mongo collection the job
        # touched, plus every captured log record, for the LITERAL
        # secret value.
        mongo_hits = []
        for coll_name in ("fix_jobs", "finding_fixes", "fix_batches", "brain_calls",
                          "cto_open_findings"):
            coll = getattr(db, coll_name)
            for row in coll.rows:
                if _FAKE_SECRET in repr(row):
                    mongo_hits.append((coll_name, row))

        log_hits = [
            rec.getMessage() for rec in caplog.records
            if _FAKE_SECRET in rec.getMessage()
        ]

        assert mongo_hits == [], (
            f"C3 CONFIRMED — the fake secret leaked into Mongo: {mongo_hits!r}"
        )
        assert log_hits == [], (
            f"C3 CONFIRMED — the fake secret leaked into a log line: {log_hits!r}"
        )
        # Sanity check the fix itself actually landed (the secret IS
        # gone from the file on the batch branch) — a passing leak
        # check on a job that silently did nothing would be
        # meaningless.
        assert len(db.finding_fixes.rows) == 1
        final_branch = db.finding_fixes.rows[0]["branch"]
        assert _FAKE_SECRET not in branch_content[final_branch]["config.py"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
