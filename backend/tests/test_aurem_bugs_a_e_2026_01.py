"""Backend regression tests for Aurem/ORA bugs A-E (Jan 2026).

Bugs covered:
- A: Scan-complete message durably persisted to `chat_sessions`
     (readable by GET /chat/history), NOT to disconnected
     `ora_chat_sessions`.
- B: (frontend, code-review only here)
- C: chat scan-followup wiring — mode_d_debugger helpers present +
     worker dispatches _scan_followup_check BEFORE mode classification.
- D: `persist_findings_to_backlog` called from /codebase-health/scan
     with source='codebase_health_manual' — advisor findings.total
     reflects real cto_open_findings rows.
- E: chat message body_lines includes ALL scanned categories (even
     0-finding ones), matching header's `across N categories`.

All assertions verify real data (mongo rows, API response bodies) —
no mocks. Uses live test@aurem.dev preview account + project
p_64987820ec (repo polarisbuiltinc-wq/Aurem-1).
"""
import os
import time
import uuid
import inspect
import pytest
import requests

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api/aurem-dev"

TEST_EMAIL = "test@aurem.dev"
TEST_PASSWORD = "AuremTest2026!"
PROJECT_ID = "p_64987820ec"


# ─────────────────────── fixtures ───────────────────────
@pytest.fixture(scope="session")
def auth():
    r = requests.post(
        f"{API}/auth/login",
        json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
        timeout=30,
    )
    if r.status_code != 200:
        pytest.skip(f"Login failed {r.status_code}: {r.text[:200]}")
    data = r.json()
    if data.get("mfa_required"):
        pytest.skip("MFA required — cannot run headless")
    return {
        "token": data["token"],
        "user_id": data["user_id"],
        "headers": {"Authorization": f"Bearer {data['token']}"},
    }


@pytest.fixture(scope="session")
def db():
    """Direct mongo handle for verifying persistence."""
    try:
        from motor.motor_asyncio import AsyncIOMotorClient  # noqa: F401
    except Exception:
        pytest.skip("motor not available")
    from pymongo import MongoClient
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        pytest.skip("MONGO_URL/DB_NAME not set in env")
    client = MongoClient(mongo_url, serverSelectionTimeoutMS=3000)
    return client[db_name]


# ─────────────────────── Bug A + E ───────────────────────
class TestBugAScanChatPersistence:
    """Bug A: scan-complete msg lands in `chat_sessions`.
    Bug E: body lists ALL 4 categories."""

    def test_scan_persists_to_chat_sessions_with_all_categories(
        self, auth, db,
    ):
        session_id = f"TEST_sess_{uuid.uuid4().hex[:12]}"
        categories = ["performance", "code_quality",
                      "database", "dependencies"]
        r = requests.post(
            f"{API}/codebase-health/scan",
            headers=auth["headers"],
            json={
                "project_id": PROJECT_ID,
                "categories": categories,
                "session_id": session_id,
            },
            timeout=180,
        )
        assert r.status_code == 200, f"scan failed: {r.status_code} {r.text[:300]}"
        payload = r.json()
        assert payload.get("ok") is True
        assert "across 4 categories" in payload.get("summary", ""), \
            f"summary should mention 4 cats: {payload.get('summary')}"

        # Bug A: durable persistence — GET /chat/history returns it
        hist = requests.get(
            f"{API}/chat/history",
            headers=auth["headers"],
            params={"session_id": session_id},
            timeout=20,
        )
        assert hist.status_code == 200, hist.text[:200]
        msgs = hist.json().get("messages", [])
        scan_msgs = [
            m for m in msgs
            if m.get("kind") == "scan_result"
            or "Scan complete" in (m.get("content") or "")
        ]
        assert scan_msgs, (
            f"Bug A regression: no scan-complete message in "
            f"chat_sessions.turns for {session_id}. Got {len(msgs)} turns."
        )
        content = scan_msgs[-1]["content"]
        assert "Scan complete" in content

        # Bug E: all 4 category lines must be present
        missing = [c for c in categories if f"`{c}`" not in content]
        assert not missing, (
            f"Bug E regression: missing category lines {missing} in "
            f"body:\n{content}"
        )
        # header/body agreement
        assert "across 4 categories" in content

    def test_scan_not_written_to_disconnected_ora_chat_sessions(
        self, auth, db,
    ):
        """Regression guard: the OLD path wrote to `ora_chat_sessions`
        with no upsert (silent no-op). Ensure the new path writes into
        the collection GET /chat/history actually reads."""
        session_id = f"TEST_sess_{uuid.uuid4().hex[:12]}"
        r = requests.post(
            f"{API}/codebase-health/scan",
            headers=auth["headers"],
            json={
                "project_id": PROJECT_ID,
                "categories": ["code_quality"],
                "session_id": session_id,
            },
            timeout=180,
        )
        assert r.status_code == 200
        doc = db.chat_sessions.find_one(
            {"session_id": session_id, "user_id": auth["user_id"]}
        )
        assert doc is not None, "chat_sessions doc not created"
        turns = doc.get("turns") or []
        assert any(
            t.get("kind") == "scan_result" for t in turns
        ), f"scan_result turn not in chat_sessions.turns: {turns}"


# ─────────────────────── Bug D ───────────────────────
class TestBugDAdvisorFindingsCounter:
    """Bug D: /codebase-health/scan now writes into cto_open_findings
    via persist_findings_to_backlog with source='codebase_health_manual'
    so the Advisor counter (findings.total via GET /advisor/context)
    reflects reality."""

    def test_advisor_context_reachable(self, auth):
        r = requests.get(
            f"{API}/advisor/context",
            headers=auth["headers"],
            params={"project_id": PROJECT_ID},
            timeout=30,
        )
        assert r.status_code == 200, r.text[:200]
        data = r.json()
        assert "findings" in data
        assert isinstance(data["findings"].get("total"), int)

    def test_manual_scan_persists_to_cto_open_findings(self, auth, db):
        # trigger a scan with categories most likely to produce
        # critical/high findings on this real project.
        r = requests.post(
            f"{API}/codebase-health/scan",
            headers=auth["headers"],
            json={
                "project_id": PROJECT_ID,
                "categories": ["code_quality", "database"],
                "session_id": f"TEST_sess_{uuid.uuid4().hex[:12]}",
            },
            timeout=180,
        )
        assert r.status_code == 200, r.text[:300]
        payload = r.json()
        # Give the persist a beat if it were async fire-and-forget
        time.sleep(1)
        # Any rows from source='codebase_health_manual' — presence
        # means the wiring in codebase_health.py fires. Upsert may not
        # create new rows if identical findings already existed;
        # that's fine — count of rows with THIS source is what we
        # assert (must be >=0, and doc must exist if any crit/high
        # findings were returned).
        n_manual = db.cto_open_findings.count_documents({
            "user_id": auth["user_id"],
            "project_id": PROJECT_ID,
            "source": "codebase_health_manual",
        })
        # If the scan returned crit/high findings, we expect at least
        # one row with this source (upsert either creates or updates).
        crit_high = sum(
            1 for f in (payload.get("breakdown") or {}).values()
            for _ in range(f.get("counts", {}).get("critical", 0)
                           + f.get("counts", {}).get("high", 0))
        )
        if crit_high > 0:
            assert n_manual > 0, (
                f"Bug D regression: scan returned {crit_high} crit/high "
                f"findings but 0 cto_open_findings rows have "
                f"source='codebase_health_manual'"
            )


# ─────────────────────── Bug C wiring ───────────────────────
class TestBugCScanFollowupWiring:
    """Bug C: verify the code-level wiring is present. Fully live
    testing requires a running chat worker + real GitHub commits
    (20-60s each) — deliberately NOT invoked here to avoid PR
    spam. Wiring correctness is the sufficient regression guard."""

    def test_mode_d_debugger_exports(self):
        from services import mode_d_debugger as m
        assert hasattr(m, "has_scan_followup_signal")
        assert hasattr(m, "try_scan_followup_reply")
        assert hasattr(m, "_recent_project_scan")
        # signature: on_fix_progress kwarg
        sig = inspect.signature(m.try_scan_followup_reply)
        assert "on_fix_progress" in sig.parameters, \
            "on_fix_progress kwarg missing"

    def test_has_scan_followup_signal_matches_expected_phrases(self):
        from services.mode_d_debugger import has_scan_followup_signal
        assert has_scan_followup_signal(
            "list the bug hunt findings from the scan you just ran"
        )
        assert has_scan_followup_signal(
            "fix the high severity findings from that scan"
        )
        # a plain new debug question with no scan reference:
        assert not has_scan_followup_signal(
            "why is my login button not clickable on mobile"
        )

    def test_worker_dispatches_scan_followup_before_mode_broadcast(self):
        src = open("/app/backend/routers/chat/worker.py").read()
        # Locate the run_worker body and check dispatch order within it.
        rw_idx = src.find("async def run_worker(")
        assert rw_idx > 0, "run_worker not found"
        body = src[rw_idx:]
        idx_check = body.find("await _scan_followup_check(state)")
        idx_mode = body.find("await _mode_broadcast(state)")
        assert idx_check > 0, "no await _scan_followup_check(state) in run_worker"
        assert idx_mode > 0, "no await _mode_broadcast(state) in run_worker"
        assert idx_check < idx_mode, (
            "Bug C regression: _scan_followup_check must be dispatched "
            "BEFORE _mode_broadcast inside run_worker"
        )


# ─────────────────────── Bug B (frontend code-review only) ───────
class TestBugBFrontendGateRemoved:
    """Bug B: CodebaseHealth.jsx should NOT gate on isAdminOrFounder;
    ChatPanel.jsx onReviewFindings must use useNavigate (not
    window.location.assign) and pass ?project_id="""

    def test_codebase_health_page_no_admin_gate(self):
        src = open(
            "/app/frontend/src/pages/CodebaseHealth.jsx"
        ).read()
        # gate removed — allow the words to appear only inside
        # comments, but not as an executed guard.
        # Cheap heuristic: no active early-return that redirects to
        # /dashboard based on isAdminOrFounder.
        assert "isAdminOrFounder()" not in src or "redirect" not in src.lower(), \
            "Bug B regression: isAdminOrFounder gate may still be active"
        # reads ?project_id= from URL
        assert (
            "URLSearchParams" in src
            or "searchParams" in src
            or "useSearchParams" in src
        ), "CodebaseHealth.jsx must read project_id from URL query"

    def test_chatpanel_uses_navigate_with_project_id(self):
        src = open(
            "/app/frontend/src/components/ChatPanel.jsx"
        ).read()
        # Should NOT hard-reload
        assert (
            "window.location.assign('/codebase-health')" not in src
            and 'window.location.assign("/codebase-health")' not in src
        ), "Bug B regression: hard reload still present"
        # Should use react-router navigate with project_id query
        assert "useNavigate" in src, "useNavigate must be imported/used"
        assert (
            "/codebase-health?project_id=" in src
            or "codebase-health" in src and "project_id" in src
        ), "onReviewFindings must pass project_id"
