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


_POLICY_CLAUSE = (("select", "using"), ("insert", "with check"),
                  ("update", "using"), ("delete", "using"))


def _head_statement(rule: str, table: str, ev: dict) -> Optional[str]:
    if rule == "rls_disabled":
        return f"alter table public.{table} enable row level security;"
    pol = ev.get("policy")
    if not pol or not re.match(r"^[A-Za-z0-9_ \-]+$", pol):
        return None
    return f'drop policy if exists "{pol}" on public.{table};'


def _owner_policies(table: str, owner_col: str) -> list[str]:
    lines = []
    for cmd, clause in _POLICY_CLAUSE:
        nm = f"aurem_owner_{cmd}_{table}"
        lines.append(f'drop policy if exists "{nm}" on public.{table};')
        lines.append(f'create policy "{nm}" on public.{table} for {cmd} to authenticated '
                     f"{clause} (auth.uid() = {owner_col});")
    return lines


def _lock_wording(table: str, has_owner: bool) -> tuple[str, list[str]]:
    nice = table.replace("_", " ")
    if has_owner:
        return (f"Only signed-in owners will be able to see and change their own {nice}.",
                ["Check that people can still see their own rows after this is applied."])
    return (f"{nice.capitalize()} will be locked to your server only.",
            [f"{nice.capitalize()} will only be reachable from your server after this "
             "(no row has a clear owner). If visitors should read it, tell ORA and we'll make it public-read instead."])


def _lockable(rule: Optional[str], table: Optional[str]) -> bool:
    return (rule in ("rls_disabled", "policy_open_write", "policy_open_read")
            and bool(table) and bool(_SAFE_IDENT.match(table)))


def _lock_sql(table: str, head: str, owner_col: Optional[str]) -> str:
    lines = [f"-- Added by AUREM: lock access to {table}.", "", head]
    if owner_col:
        lines += _owner_policies(table, owner_col)
    return "\n".join(lines) + "\n"


def lock_my_data(item: dict, tc: dict[str, str], clock=None) -> Optional[dict]:
    rule = item.get("rule_id")
    ev = item.get("evidence") or {}
    table = ev.get("table")
    if not _lockable(rule, table):
        return None
    info = next((t for t in build_permission_map(tc)["tables"] if t["name"] == table), None)
    head = _head_statement(rule, table, ev)
    if not info or head is None:
        return None
    owner_col = "user_id" if "user_id" in info["columns"] else None
    summary, manual = _lock_wording(table, bool(owner_col))
    folder = posixpath.dirname(info["file"]) or "supabase/migrations"
    path = f"{folder}/{_ts(clock)}_aurem_lock_{table}.sql"
    return {"pack": "lock_my_data", "files": {path: _lock_sql(table, head, owner_col)},
            "title": f"Lock access to {table.replace('_', ' ')}", "summary_plain": summary,
            "manual_steps": manual}


def _anon_key_ref(tc: dict[str, str]) -> Optional[str]:
    pkg = next((t for p, t in tc.items() if p.endswith("package.json")), "")
    if '"vite"' in pkg:
        return "import.meta.env.VITE_SUPABASE_ANON_KEY"
    return "process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY" if '"next"' in pkg else None


def _service_role_jwts(text: str) -> list[str]:
    from services.checkup_common import _JWT_RE, _jwt_role
    return [m.group(0) for m in _JWT_RE.finditer(text) if _jwt_role(m.group(1)) == "service_role"]


def _replace_literals(text: str, jwts: list[str], ref: str) -> Optional[str]:
    new = text
    for j in jwts:
        new = re.sub(r"""(["'`])""" + re.escape(j) + r"\1", ref, new)
    return None if new == text or any(j in new for j in jwts) else new


def hide_my_keys(item: dict, tc: dict[str, str], clock=None) -> Optional[dict]:
    if item.get("rule_id") != "service_role_key_in_client":
        return None
    path = (item.get("evidence") or {}).get("file") or ""
    text, ref = tc.get(path), _anon_key_ref(tc)
    jwts = _service_role_jwts(text) if text else []
    new = _replace_literals(text, jwts, ref) if (jwts and ref) else None
    if new is None:
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
