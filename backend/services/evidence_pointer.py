"""
services/evidence_pointer.py — 2026-09-20

Shared "pointer + preview" pattern for long-running / autonomous
summaries.

Root problem (user-reported, twice, independently): both the
codebase-health scan-result chat message (Bug 6) and Loop Mode's
end-of-run chat turn compact a real stored record down to prose-only
totals. The reader has no way back to the underlying evidence — they
are left trusting the summary text itself, which is exactly the
failure mode "Prove It Works" exists to prevent. This module is the
ONE place both call sites build their message through, so the format
can't drift between them and a future third call site doesn't need to
reinvent it.

What this does NOT do: it does not touch the underlying raw record
(the full `codebase_health_scans` document, the full `loop_sessions`
document) — those are untouched, full-fidelity, already persisted.
This only changes the SHORT chat-facing text that points at them:
a headline + a capped, real-data preview + a stable `evidence_ref`
that a later reader (human or agent) can resolve back to the exact
document via GET /api/aurem-dev/evidence/{kind}/{record_id}.

`preview_items` must always be real lines pulled straight from the
underlying record (finding titles, changed file paths, etc.) — never
LLM-authored, never invented. Callers are responsible for that; this
module just caps/formats what it's handed.
"""
from __future__ import annotations

# Evidence kinds this pointer format supports, mapped to the Mongo
# collection that actually holds the full record. Adding a new kind
# is one line here — not a new message format.
EVIDENCE_COLLECTIONS: dict[str, str] = {
    "scan":     "codebase_health_scans",
    "loop_run": "loop_sessions",
}

# Preview is a preview, not a re-hosting of the record — capped hard
# regardless of how many items the caller passes in.
_MAX_PREVIEW_ITEMS = 5


def evidence_ref(kind: str, record_id: str) -> dict:
    """Stable, resolvable pointer to a real stored record.

    `record_id` must be the record's OWN primary key — the Mongo
    `_id` for `scan`, the `loop_id` for `loop_run` — never a
    synthetic or derived value, so the pointer always resolves to the
    exact document it claims to point at.
    """
    if kind not in EVIDENCE_COLLECTIONS:
        raise ValueError(f"unknown evidence kind: {kind!r}")
    if not record_id:
        raise ValueError("record_id is required for an evidence_ref")
    return {
        "kind":          kind,
        "collection":    EVIDENCE_COLLECTIONS[kind],
        "record_id":     str(record_id),
        "retrieve_path": f"/api/aurem-dev/evidence/{kind}/{record_id}",
    }


def build_evidence_message(
    *,
    kind: str,
    record_id: str,
    headline: str,
    body_lines: list[str] | None = None,
    preview_items: list[str] | None = None,
    preview_total: int | None = None,
    link_label: str | None = None,
    link_url: str | None = None,
) -> dict:
    """Builds the persisted chat-facing text + its evidence_ref.

    Returns {"text": <markdown str>, "evidence_ref": <dict>}. Callers
    persist BOTH — `text` as the message content, `evidence_ref`
    alongside it so a resolver can fetch the exact record without
    having to parse the text back out.
    """
    ref = evidence_ref(kind, record_id)
    lines: list[str] = [headline, ""]
    lines.extend(body_lines or [])
    if link_url:
        lines.append(f"\n[{link_label or 'View'}]({link_url})")
    shown = (preview_items or [])[:_MAX_PREVIEW_ITEMS]
    if shown:
        total = preview_total if preview_total is not None else len(shown)
        lines.append(f"\n_Preview (first {len(shown)} of {total}):_")
        lines.extend(f"- {item}" for item in shown)
    lines.append(
        f"\n\U0001F4CE Full record: `{kind}/{ref['record_id']}` — "
        f"fetch via `{ref['retrieve_path']}`"
    )
    return {"text": "\n".join(lines), "evidence_ref": ref}
