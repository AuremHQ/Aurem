"""
routers/cto_projects/report_pdf.py — Ticket C (2026-02): server-side
scan/audit report PDF export, downloadable by the project OWNER only.

Security (mission requirement — "no PDF from user-controlled HTML, no
SSRF/XSS path"):
  - Built entirely with reportlab's Platypus API (pure programmatic
    drawing) — there is no HTML/Markdown-to-PDF engine anywhere in
    this path, so there's no embedded browser/renderer that could
    fetch an external URL or execute script from finding text.
  - Every dynamic string (finding title/message/file path, project
    name/repo) is escaped with `xml.sax.saxutils.escape` before being
    handed to `Paragraph` — reportlab's Paragraph interprets a small
    HTML-like markup subset (<b>, <a href=...>, even <img src=...> in
    some configurations), so unescaped attacker-influenced text (e.g.
    a crafted finding title) could otherwise inject markup. Escaping
    closes that off entirely.
  - No external resources are loaded (no remote images/fonts).
  - Strict OWNER-only check: `project["user_id"] == me["user_id"]` —
    deliberately NOT the team-delegation `check_permission(...,
    effective_account_id)` path other endpoints use, per this
    ticket's explicit "owner only" requirement.
  - Findings are capped (first 200) so a pathological backlog can't
    produce an unbounded PDF (also bounds request latency/memory).

Dependency: reportlab==5.0.1, pre-approved exact pin (mission). No
other new dependency.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone
from xml.sax.saxutils import escape

from fastapi import Header, HTTPException, Response
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

import routers.cto_projects as _pkg
from . import router

_MAX_FINDINGS_IN_REPORT = 200
_SEV_ORDER = {"critical": 0, "high": 1, "unknown": 2, "medium": 3, "low": 4, "info": 5}
_SEV_COLOR_HEX = {
    "critical": "#b91c1c", "high": "#c2410c", "unknown": "#a16207",
    "medium": "#a16207", "low": "#15803d", "info": "#6b7280",
}


def _build_pdf_bytes(project: dict, findings: list[dict], score: int) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
    )
    styles = getSampleStyleSheet()
    disclosure_style = ParagraphStyle(
        "disclosure", parent=styles["Normal"], fontSize=8, textColor=colors.grey,
    )
    story = [
        # Mission requirement — dependency disclosure at the TOP of
        # the report, not buried in a footer.
        Paragraph("Generated with reportlab 5.0.1 (open-source PDF library).", disclosure_style),
        Spacer(1, 10),
        Paragraph(escape(f"AUREM Codebase Health Report — {project.get('name') or project.get('github_repo', '')}"),
                   styles["Title"]),
        Paragraph(escape(f"{project.get('github_owner', '')}/{project.get('github_repo', '')}"), styles["Normal"]),
        Paragraph(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", styles["Normal"]),
        Spacer(1, 14),
        Paragraph(f"<b>Health score: {score}/100</b>", styles["Heading2"]),
        Paragraph(f"Open findings shown: {len(findings)}"
                   + (f" (of more than {_MAX_FINDINGS_IN_REPORT} total — report capped)"
                      if len(findings) >= _MAX_FINDINGS_IN_REPORT else ""), styles["Normal"]),
        Spacer(1, 14),
    ]

    if findings:
        rows = [["Severity", "File", "Finding"]]
        for f in findings:
            sev = (f.get("severity") or "info").lower()
            file_loc = escape(f"{f.get('file', '')}:{f.get('line', '')}"[:60])
            title = escape((f.get("title") or f.get("message") or "")[:140])
            rows.append([
                Paragraph(f"<font color='{_SEV_COLOR_HEX.get(sev, '#6b7280')}'><b>{escape(sev)}</b></font>", styles["Normal"]),
                Paragraph(file_loc, styles["Normal"]),
                Paragraph(title, styles["Normal"]),
            ])
        table = Table(rows, colWidths=[0.9 * inch, 2.0 * inch, 3.6 * inch], repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d1d5db")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
        ]))
        story.append(table)
    else:
        story.append(Paragraph("No open findings.", styles["Normal"]))

    doc.build(story)
    return buf.getvalue()


@router.get("/projects/{project_id}/report.pdf")
async def get_project_report_pdf(
    project_id: str,
    authorization: str = Header(None),
) -> Response:
    me = await _pkg.current_dev(authorization)
    db = _pkg.require_db()

    project = await db.cto_projects.find_one({"project_id": project_id}, {"_id": 0})
    # Strict OWNER-only — ticket C's explicit requirement, not the
    # team-delegation path other project endpoints use.
    if not project or project.get("user_id") != me["user_id"]:
        raise HTTPException(404, "Project not found")

    cursor = db.cto_open_findings.find(
        {"project_id": project_id, "user_id": me["user_id"], "status": "open"},
        {"_id": 0},
    ).limit(_MAX_FINDINGS_IN_REPORT)
    findings = [f async for f in cursor]
    findings.sort(key=lambda f: _SEV_ORDER.get((f.get("severity") or "info").lower(), 5))

    from services.codebase_health_core import _score_for_findings
    score = _score_for_findings(findings)

    pdf_bytes = _build_pdf_bytes(project, findings, score)
    filename = f"aurem-report-{project.get('github_repo') or project_id}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
