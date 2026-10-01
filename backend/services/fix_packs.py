"""
services/fix_packs.py — deterministic patch builders ("Fix Packs").

Pure: `(item, text_cache) -> patch | None`.  No LLM writes these patches
(S12), so the same input always yields the same change and it can be
proven by re-running the check (S8).  `None` means "not auto-fixable" and
the UI must say so honestly (S9) rather than attempt a guess.

Packs:
  lock_my_data  — turn row-level security on / replace an always-true
                  policy with an owner-only one (needs a `user_id` column;
                  otherwise the table is locked to server-only access).
  hide_my_keys  — remove a hardcoded master key from client code.
"""
from __future__ import annotations

import posixpath
import re
from datetime import datetime, timezone
from typing import Callable, Optional

from services.permission_map import build_permission_map

_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _ts(clock: Optional[Callable[[], datetime]]) -> str:
    return (clock() if clock else datetime.now(timezone.utc)).strftime("%Y%m%d%H%M%S")


def lock_my_data(item: dict, tc: dict[str, str], clock=None) -> Optional[dict]:
    rule = item.get("rule_id")
    ev = item.get("evidence") or {}
    table = ev.get("table")
    if rule not in ("rls_disabled", "policy_open_write", "policy_open_read") or not table:
        return None
    if not _SAFE_IDENT.match(table):
        return None
    info = next((t for t in build_permission_map(tc)["tables"] if t["name"] == table), None)
    if not info:
        return None
    owner_col = "user_id" if "user_id" in info["columns"] else None
    lines = [f"-- Added by AUREM: lock access to {table}.", ""]
    if rule == "rls_disabled":
        lines.append(f"alter table public.{table} enable row level security;")
    else:
        pol = ev.get("policy")
        if not pol or not re.match(r"^[A-Za-z0-9_ \-]+$", pol):
            return None
        lines.append(f'drop policy if exists "{pol}" on public.{table};')
    if owner_col:
        for cmd, clause in (("select", "using"), ("insert", "with check"),
                            ("update", "using"), ("delete", "using")):
            nm = f"aurem_owner_{cmd}_{table}"
            lines.append(f'drop policy if exists "{nm}" on public.{table};')
            lines.append(f'create policy "{nm}" on public.{table} for {cmd} to authenticated '
                         f"{clause} (auth.uid() = {owner_col});")
        manual = ["Check that people can still see their own rows after this is applied."]
        summary = f"Only signed-in owners will be able to see and change their own {table.replace('_', ' ')}."
    else:
        manual = [f"{table.replace('_', ' ').capitalize()} will only be reachable from your server after this "
                  "(no row has a clear owner). If visitors should read it, tell ORA and we'll make it public-read instead."]
        summary = f"{table.replace('_', ' ').capitalize()} will be locked to your server only."
    folder = posixpath.dirname(info["file"]) or "supabase/migrations"
    path = f"{folder}/{_ts(clock)}_aurem_lock_{table}.sql"
    return {"pack": "lock_my_data", "files": {path: "\n".join(lines) + "\n"},
            "title": f"Lock access to {table.replace('_', ' ')}", "summary_plain": summary,
            "manual_steps": manual}


def hide_my_keys(item: dict, tc: dict[str, str], clock=None) -> Optional[dict]:
    if item.get("rule_id") != "service_role_key_in_client":
        return None
    path = (item.get("evidence") or {}).get("file") or ""
    text = tc.get(path)
    if not text:
        return None
    pkg = next((t for p, t in tc.items() if p.endswith("package.json")), "")
    if '"vite"' in pkg:
        ref = "import.meta.env.VITE_SUPABASE_ANON_KEY"
    elif '"next"' in pkg:
        ref = "process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY"
    else:
        return None
    from services.checkup_checks import _JWT_RE, _jwt_role
    jwts = [m.group(0) for m in _JWT_RE.finditer(text) if _jwt_role(m.group(1)) == "service_role"]
    if not jwts:
        return None
    new = text
    for j in jwts:
        new = re.sub(r"""(["'`])""" + re.escape(j) + r"\1", ref, new)
    if new == text or any(j in new for j in jwts):
        return None
    return {"pack": "hide_my_keys", "files": {path: new},
            "title": "Remove the master key from your website code",
            "summary_plain": "The public site will use your safe public key instead of the master key.",
            "manual_steps": [
                "Rotate (replace) the old master key in your Supabase dashboard — it was public, so treat it as leaked.",
                "Any feature that really needs the master key must move to server-side code."]}


PACKS = (lock_my_data, hide_my_keys)


def build_patch(item: dict, tc: dict[str, str], clock=None) -> Optional[dict]:
    for pack in PACKS:
        patch = pack(item, tc, clock)
        if patch:
            return patch
    return None
