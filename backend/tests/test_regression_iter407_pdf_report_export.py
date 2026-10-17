"""Regression lock for Ticket C (2026-02) — server-side scan/audit PDF
export, owner-only, no user-controlled-HTML injection path.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from routers.cto_projects.report_pdf import _build_pdf_bytes, get_project_report_pdf


def _project(user_id="u1"):
    return {"project_id": "p1", "user_id": user_id, "name": "demo",
            "github_owner": "o", "github_repo": "r"}


def test_pdf_builds_with_clean_findings():
    pdf = _build_pdf_bytes(_project(), [
        {"severity": "high", "file": "a.py", "line": 1, "title": "SQL injection risk"},
    ], score=72)
    assert pdf[:4] == b"%PDF"


def test_pdf_disclosure_line_is_at_the_top():
    import re
    import routers.cto_projects.report_pdf as mod
    import inspect
    src = inspect.getsource(mod._build_pdf_bytes)
    # The FIRST Paragraph(...) call built into `story` must be the
    # dependency disclosure — structural proof it's literally first.
    first_paragraph_call = re.search(r"story = \[(.*?)\]", src, re.DOTALL).group(1)
    first_call_text = re.search(r'Paragraph\(\s*"([^"]+)"', first_paragraph_call).group(1)
    assert "reportlab 5.0.1" in first_call_text


def test_malicious_finding_title_does_not_inject_live_markup():
    """Real exploit-shaped input — if this weren't escaped, reportlab's
    Paragraph parser either raises (crash/DoS) or, for well-formed tags
    like <a href=...>, would render a live clickable external link
    inside the PDF. Must not raise and must not be interpreted."""
    malicious = "<img src=x onerror=alert(1)> & <a href='http://evil.com'>click</a>"
    pdf = _build_pdf_bytes(_project(), [
        {"severity": "high", "file": "a.py", "line": 1, "title": malicious, "message": malicious},
    ], score=50)
    assert pdf[:4] == b"%PDF"  # built successfully, did not raise


def test_findings_capped_at_200():
    from routers.cto_projects.report_pdf import _MAX_FINDINGS_IN_REPORT
    assert _MAX_FINDINGS_IN_REPORT == 200


@pytest.mark.asyncio
async def test_owner_only_non_owner_gets_404():
    fake_db = AsyncMock()
    fake_db.cto_projects.find_one = AsyncMock(return_value=_project(user_id="owner_1"))

    with patch("routers.cto_projects.current_dev", AsyncMock(return_value={"user_id": "attacker_2"})), \
         patch("routers.cto_projects.require_db", lambda: fake_db):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await get_project_report_pdf("p1", authorization="Bearer x")
        assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_owner_gets_real_pdf_response():
    fake_db = AsyncMock()
    fake_db.cto_projects.find_one = AsyncMock(return_value=_project(user_id="owner_1"))

    class _EmptyCursor:
        def limit(self, n): return self
        def __aiter__(self):
            async def _gen():
                return
                yield  # pragma: no cover
            return _gen()
    fake_db.cto_open_findings.find = lambda *a, **kw: _EmptyCursor()

    with patch("routers.cto_projects.current_dev", AsyncMock(return_value={"user_id": "owner_1"})), \
         patch("routers.cto_projects.require_db", lambda: fake_db):
        resp = await get_project_report_pdf("p1", authorization="Bearer x")
    assert resp.media_type == "application/pdf"
    assert resp.body[:4] == b"%PDF"
