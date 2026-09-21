"""Live E2E API tests for admin failure-patterns & self-bugs endpoints - audit 2026-09-21."""
import os
import pytest
import requests

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://bin-context-pat.preview.emergentagent.com').rstrip('/')
API = f"{BASE_URL}/api"
ADMIN_EMAIL = "test@aurem.dev"
ADMIN_PASSWORD = "AuremTest2026!"


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{API}/aurem-dev/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=30)
    if r.status_code != 200:
        pytest.skip(f"login failed {r.status_code}: {r.text[:200]}")
    # try token or cookies
    tok = None
    try:
        tok = r.json().get("token") or r.json().get("access_token")
    except Exception:
        pass
    if tok:
        s.headers.update({"Authorization": f"Bearer {tok}"})
    return s


def test_failure_patterns_shape(admin_session):
    r = admin_session.get(f"{API}/aurem-dev/admin/failure-patterns?days=30", timeout=30)
    assert r.status_code == 200, r.text[:300]
    d = r.json()
    # Part 1.1
    assert "infra_failures" in d, "missing infra_failures"
    inf = d["infra_failures"]
    assert "total" in inf and "by_reason" in inf
    assert isinstance(inf["by_reason"], list)
    for row in inf["by_reason"]:
        assert "reason" in row and "count" in row
        assert "first_seen" in row and "last_seen" in row
    # Part 3 - rows have first_seen/last_seen/status
    for section in ("top_failed_task_types", "top_failed_prompt_patterns", "tool_sequence_rates"):
        assert section in d, f"missing {section}"
        for row in d[section]:
            assert "first_seen" in row, f"{section} row missing first_seen: {row}"
            assert "last_seen" in row, f"{section} row missing last_seen: {row}"
            assert "status" in row, f"{section} row missing status: {row}"
    # Part 1.3
    assert "low_confidence_patterns" in d
    assert isinstance(d["low_confidence_patterns"], list)
    for lc in d["low_confidence_patterns"]:
        assert "note" in lc
    if "suggestions" in d:
        for sug in d["suggestions"]:
            # samples/total gating
            n = sug.get("total") or sug.get("samples") or sug.get("count") or 0
            assert n >= 20, f"suggestion below threshold: {sug}"


def test_failure_patterns_mark_fixed_round_trip(admin_session):
    r = admin_session.get(f"{API}/aurem-dev/admin/failure-patterns?days=90", timeout=30)
    assert r.status_code == 200
    d = r.json()
    row = None
    for section, kind in [("top_failed_task_types", "task_type"),
                          ("top_failed_prompt_patterns", "prompt_pattern"),
                          ("tool_sequence_rates", "tool_sequence")]:
        if d.get(section):
            row = (section, kind, d[section][0])
            break
    if not row:
        pytest.skip("no rows available to mark fixed")
    section, kind, r0 = row
    value = r0.get("task_type") or r0.get("prompt_pattern") or r0.get("value") or r0.get("tool_sequence") or r0.get("name") or r0.get("key")
    assert value, f"cannot determine value from row: {r0}"
    # mark fixed
    resp = admin_session.post(f"{API}/aurem-dev/admin/failure-patterns/mark-fixed",
                              json={"kind": kind, "value": value, "status": "fixed"}, timeout=30)
    assert resp.status_code == 200, resp.text[:300]
    # revert
    resp2 = admin_session.post(f"{API}/aurem-dev/admin/failure-patterns/mark-fixed",
                               json={"kind": kind, "value": value, "status": "open"}, timeout=30)
    assert resp2.status_code == 200, resp2.text[:300]


def test_self_bugs_list_shape(admin_session):
    r = admin_session.get(f"{API}/aurem-dev/admin/self-bugs/list", timeout=30)
    assert r.status_code == 200, r.text[:300]
    d = r.json()
    rows = d.get("bugs") or d.get("items") or d.get("rows") or (d if isinstance(d, list) else [])
    if isinstance(d, dict) and not rows:
        # find any list
        for v in d.values():
            if isinstance(v, list):
                rows = v; break
    assert isinstance(rows, list)
    if not rows:
        pytest.skip("no self-bugs rows")
    for row in rows:
        assert "first_seen" in row, f"row missing first_seen: {list(row.keys())}"
        assert "status" in row
        assert "fixed_on" in row
        assert "fixed_by" in row
    # Part 2.1 — distinct dead_end_leak evidence => distinct times_seen (not universally 17)
    dead = [r for r in rows if r.get("bug_type") == "dead_end_leak" or "dead_end" in str(r.get("kind", "")).lower() or "dead_end" in str(r.get("signature", "")).lower()]
    if len(dead) >= 2:
        # collect distinct what_user_saw and times_seen
        evi_to_count = {}
        for r0 in dead:
            evi = r0.get("what_user_saw") or r0.get("evidence") or ""
            ts = r0.get("times_seen", 0)
            evi_to_count.setdefault(evi, set()).add(ts)
        counts = [r.get("times_seen", 0) for r in dead]
        # If there are >= 2 distinct evidences, we expect at least 2 distinct times_seen values overall
        if len(evi_to_count) >= 2:
            assert len(set(counts)) >= 2, f"distinct evidences but identical times_seen (original bug): {evi_to_count}"


def test_self_bugs_mark_fixed_round_trip(admin_session):
    r = admin_session.get(f"{API}/aurem-dev/admin/self-bugs/list", timeout=30)
    d = r.json()
    rows = d.get("bugs") or d.get("items") or d.get("rows") or []
    if isinstance(d, dict) and not rows:
        for v in d.values():
            if isinstance(v, list):
                rows = v; break
    if not rows:
        pytest.skip("no self-bugs rows")
    sig = rows[0].get("signature")
    assert sig, "row missing signature"
    resp = admin_session.post(f"{API}/aurem-dev/admin/self-bugs/mark-fixed",
                              json={"signature": sig, "status": "fixed"}, timeout=30)
    assert resp.status_code == 200, resp.text[:300]
    resp2 = admin_session.post(f"{API}/aurem-dev/admin/self-bugs/mark-fixed",
                               json={"signature": sig, "status": "open"}, timeout=30)
    assert resp2.status_code == 200, resp2.text[:300]


def test_self_bugs_review_proposal_if_available(admin_session):
    r = admin_session.get(f"{API}/aurem-dev/admin/self-bugs/list", timeout=30)
    d = r.json()
    rows = d.get("bugs") or d.get("items") or d.get("rows") or []
    if isinstance(d, dict) and not rows:
        for v in d.values():
            if isinstance(v, list):
                rows = v; break
    if not rows:
        pytest.skip("no rows")
    candidate = None
    for r0 in rows:
        if r0.get("proposed_fix") and (r0.get("times_seen", 0) >= 5) and r0.get("status") == "open":
            candidate = r0; break
    if not candidate:
        pytest.skip("no row with proposed_fix meets threshold - not yet triggerable")
    sig = candidate["signature"]
    resp = admin_session.post(f"{API}/aurem-dev/admin/self-bugs/review-proposal",
                              json={"signature": sig, "decision": "approve"}, timeout=30)
    assert resp.status_code == 200, resp.text[:300]
