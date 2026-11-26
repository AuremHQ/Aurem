"""routers/chat/session_guard.py — [project_id, session_id] ownership guard.

P1F (ORA audit P3, 2026-11-25) — cross-tenant session injection at the
chat router boundary.

VERIFY-FIRST findings at main 097697ba (all in the seeded tree):

- ``ChatBody.session_id`` is client-supplied, ``max_length=128``
  (``turn.py:53``) and is NEVER validated against ``project_id`` /
  ``user_id``. Project ownership IS enforced (``build_ora_context``,
  ``ora_context.py`` — 403 on the wrong user), session ownership is NOT.
- ``_persist_turn`` (``services/chat_helpers.py:925-956``) upserts by
  ``{session_id, user_id}`` with ``$setOnInsert: {project_id}`` — so a
  turn stamped with another project's ``session_id`` is APPENDED to
  that project's session (``project_id`` is only set on insert, never
  corrected), and the prior-turn / council-recall reads keyed by
  ``session_id`` (``turn.py`` / ``worker.py:802-804``) then bleed that
  project's history into the wrong project's turn. Same-user,
  cross-project contamination; cross-user is already blocked by the
  ``user_id`` filter on every ``chat_sessions`` query.
- Sessions are client-minted: the frontend generates the id with
  ``crypto.randomUUID()`` (``frontend/src/lib/api.js::newSessionId``)
  and there is NO server-side session-create endpoint — the first
  ``_persist_turn`` upsert creates the row. A ``session_id`` with no
  row is therefore the NORMAL fresh-chat path, not an attack, and
  must NOT be rejected.

Guard rule (``assert_session_belongs_to_project``):

- No db / no ``session_id`` / no ``user_id`` → allow. (When the db
  is down ``_persist_turn`` is a no-op anyway, so no write can leak.)
- No ``chat_sessions`` row for ``{session_id, user_id}`` → allow.
  The first ``_persist_turn`` upsert then stamps the row with the
  requesting ``project_id`` (``$setOnInsert``), binding it from birth.
- Row exists and its ``project_id`` AND the request's ``project_id``
  are both bound (non-empty, not ``"home"``) and differ → 403.
  403 matches the sibling ownership failure (``build_ora_context``
  raises 403 on a wrong-user project) and the message names no
  project, so it leaks nothing about the other project.
- Row exists but is legacy-unbound (no ``project_id`` field —
  pre-per-project sessions, listed under Home) → allow under any
  project; an unbound session belongs to no project, so it cannot
  leak one project's turns into another's. A bound row with a
  missing/``"home"`` request project → allow as well: the turn is
  appended to, and reads prior turns from, the session's own
  project only — no second project's context (``bin_ctx``) is ever
  loaded for a ``"home"`` request.

Call sites (both immediately after authentication, before the mock
short-circuit, the scope-resume branch, and the retry-replay branch,
so no downstream read/write can run on a mismatched pair):

- ``POST /chat/send`` (``turn.py::chat_send``)
- ``POST /chat/stream`` (``stream.py::chat_stream``) — this also
  covers the Ask Advisor panel (``ora_panel`` / ``agent="ora"``) and
  the scope-resume path, which are branches INSIDE ``chat_stream``,
  and the worker helpers (``worker.py`` has no routes of its own).

Deliberately NOT guarded here (checked, no pair to validate):

- ``GET /chat/history``, ``DELETE /chat/sessions/...`` (``history.py``)
  and ``/chat/turn/shipped``, ``/chat/turn/ship-cancelled``,
  ``/chat/feedback``, ``/chat/task-followup`` (``misc.py``): their
  bodies/params carry ``session_id`` but NO ``project_id``, and every
  query is already ``{session_id, user_id}``-scoped, so a caller can
  only ever touch their OWN sessions.
- ``routers/ora_chat.py`` (v2 ``/ora/message``, ``/ora/slash``):
  admin-only, sessions are owner-scoped via
  ``ora_session.get_session(session_id, user_id)`` with 404 on
  unknown, and the v2 session store has no ``project_id`` concept.
- ``routers/ask.py``, ``routers/chat_commits.py``: take no
  ``session_id`` at all.

Residual race (documented, accepted): two concurrent FIRST turns for
the same fresh ``session_id`` under different projects from the SAME
user both see "no row" and both pass; the first ``_persist_turn``
upsert wins the ``project_id`` binding and the second appends to it.
That needs one user deliberately racing two of their own tabs with
the same UUID — it cannot move turns between users, and the
``{session_id, user_id}`` upsert filter means it can never attach
turns to another user's session.
"""
from __future__ import annotations

from fastapi import HTTPException


def _norm_project(project_id) -> str | None:
    """Normalize a request/stored project id for comparison.

    ``None``, ``""`` and ``"home"`` all mean "no project bound"
    (the Home / casual-chat surface); anything else is a bound id.
    """
    pid = (project_id or "").strip() if isinstance(project_id, str) else ""
    if not pid or pid == "home":
        return None
    return pid


async def assert_session_belongs_to_project(
    db,
    *,
    session_id,
    project_id,
    user_id,
) -> None:
    """Reject a turn whose session belongs to a different project.

    Raises ``HTTPException(403)`` only when a ``chat_sessions`` row
    for ``{session_id, user_id}`` exists AND its bound ``project_id``
    contradicts the request's bound ``project_id``. Every other case
    (no db, no session, fresh/unbound session, unbound request
    project) passes through unchanged — see the module docstring for
    why each arm is safe. Never raises for a missing row: a missing
    row is a freshly client-minted session, not an error.
    """
    if db is None or not session_id or not user_id:
        return
    doc = await db.chat_sessions.find_one(
        {"session_id": session_id, "user_id": user_id},
        {"_id": 0, "project_id": 1},
    )
    if not doc:
        return
    doc_pid = _norm_project(doc.get("project_id"))
    req_pid = _norm_project(project_id)
    if doc_pid is not None and req_pid is not None and doc_pid != req_pid:
        raise HTTPException(
            403, "session does not belong to this project",
        )
