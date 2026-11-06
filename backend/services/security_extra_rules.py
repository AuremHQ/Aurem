"""
services/security_extra_rules.py — seven extra static-scan rules (Phase 3)

Wired into services.security_text_scanner._scan_text, so the 1-click scan,
the loop-engine diff scan and anything else that calls it get them.
The eighth rule of this batch, slop_squat_unknown_package, needs the network
and lives in services/slop_squat_check.py (deep scan only).

  prompt_injection_user_input    HIGH    request data interpolated into an LLM
                                         prompt without delimiting, or raw LLM
                                         output fed to eval/exec/shell
  missing_rls_or_ownership_check HIGH    SQL table without row level security,
                                         or a by-id route that reads/writes a
                                         record with no owner/auth signal
  mass_assignment_request_body   HIGH    whole request body written to the DB /
                                         spread into a model, or a privilege
                                         field copied straight from the body
  open_redirect_user_param       MEDIUM  redirect to a user-supplied URL with no
                                         same-site / allowlist check
  missing_audit_logging          MEDIUM  login / password / role-change route
                                         that logs nothing
  sri_missing_cdn_resource       MEDIUM  third-party <script>/<link stylesheet>
                                         without integrity=
  public_bucket_acl              HIGH    public-read ACL / public bucket policy

The detectors live in services/security_extra_detect_input.py (content
patterns) and services/security_extra_detect_routes.py (route/window logic);
shared helpers are in services/security_extra_common.py.

These are heuristics: every detector looks at a small window, has explicit
negative cases, honours the `vanguard: ignore` / `security-scan: ignore`
line markers, and the repo's own source is scanned in
tests/test_regression_iter2026_65_new_scan_rules.py to keep false positives
at zero.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterator

from services.security_extra_detect_input import (
    detect_mass_assignment,
    detect_prompt_injection,
    detect_public_bucket,
    detect_sri,
)
from services.security_extra_detect_routes import (
    detect_audit_logging,
    detect_open_redirect,
    detect_rls_ownership,
)

logger = logging.getLogger(__name__)

EXTRA_RULE_META: dict[str, dict[str, str]] = {
    "prompt_injection_user_input": {
        "vuln": "prompt_injection", "severity": "high",
        "desc": "User-controlled text is placed in an LLM prompt without delimiting, or raw LLM output is executed",
        "fix_hint": "Wrap user text in delimiters (<user_input>…</user_input>), tell the model to treat it as data, "
                    "and never pass raw model output to eval/exec/shell.",
        "trigger": "request/user text f-string'd or concatenated into an LLM prompt with no delimiter, or LLM output passed to eval/exec/os.system",
    },
    "missing_rls_or_ownership_check": {
        "vuln": "access_control", "severity": "high",
        "desc": "Table without row level security, or a by-id route that touches a record with no owner/auth check",
        "fix_hint": "Enable RLS and add a policy, or filter the lookup by the authenticated user's id (IDOR).",
        "trigger": "SQL CREATE TABLE without ENABLE ROW LEVEL SECURITY, or GET/PUT/DELETE /x/{id} that reads by id without an owner/user filter",
    },
    "mass_assignment_request_body": {
        "vuln": "mass_assignment", "severity": "high",
        "desc": "Whole request body is written to the database / spread into a model, or a privilege field comes from the body",
        "fix_hint": "Copy an explicit allowlist of fields; set role/is_admin/plan server-side only.",
        "trigger": "`$set: body`, `Model(**request.json)`, `Model.create(req.body)`, `{...req.body}`, or `is_admin/role` taken from the body",
    },
    "open_redirect_user_param": {
        "vuln": "open_redirect", "severity": "medium",
        "desc": "Redirect to a user-supplied URL without a same-site or allowlist check",
        "fix_hint": "Only redirect to relative paths (start with one '/', not '//') or to an exact-match allowlist.",
        "trigger": "RedirectResponse/res.redirect/window.location set from `next`/`redirect`/`url` params with no startswith('/') or allowlist check",
    },
    "missing_audit_logging": {
        "vuln": "logging", "severity": "medium",
        "desc": "Login / password / role-change route writes no log or audit record",
        "fix_hint": "Log who did what (user id, action, outcome, IP) for auth and permission changes; never log secrets.",
        "trigger": "login/register/password/role/permission route handlers with no logger/audit call",
    },
    "sri_missing_cdn_resource": {
        "vuln": "supply_chain", "severity": "medium",
        "desc": "Third-party script/stylesheet loaded without a Subresource Integrity hash",
        "fix_hint": 'Add integrity="sha384-…" and crossorigin="anonymous", or self-host the file.',
        "trigger": "<script src>/<link rel=stylesheet> from a CDN host without integrity= (dynamic vendor scripts excepted)",
    },
    "public_bucket_acl": {
        "vuln": "misconfiguration", "severity": "high",
        "desc": "Storage bucket or object is made world-readable/writable",
        "fix_hint": "Use private ACLs, keep block-public-access on, and serve files through signed URLs.",
        "trigger": "ACL public-read, block_public_acls=false, Principal \"*\", allUsers, createBucket public:true, `allow read: if true`",
    },
    "slop_squat_unknown_package": {
        "vuln": "supply_chain", "severity": "high",
        "desc": "Dependency name does not exist on npm/PyPI (AI-invented names can be registered by an attacker)",
        "fix_hint": "Replace it with the real package; verify every AI-suggested dependency on the registry.",
        "trigger": "package.json / requirements.txt dependency that returns 404 on npm/PyPI (deep scan only)",
    },
}

_MAX_PER_RULE = 25
_MAX_TEXT = 2_000_000

_DETECTORS: list[tuple[str, Callable[[str, list[str], str], Iterator[tuple[int, str]]]]] = [
    ("prompt_injection_user_input", detect_prompt_injection),
    ("missing_rls_or_ownership_check", detect_rls_ownership),
    ("mass_assignment_request_body", detect_mass_assignment),
    ("open_redirect_user_param", detect_open_redirect),
    ("missing_audit_logging", detect_audit_logging),
    ("sri_missing_cdn_resource", detect_sri),
    ("public_bucket_acl", detect_public_bucket),
]


def _finding(rid: str, path: str, line: int, snippet: str) -> dict:
    meta = EXTRA_RULE_META[rid]
    return {
        "rule_id": rid, "vuln": meta["vuln"], "severity": meta["severity"],
        "file": path, "line": line, "snippet": snippet.strip()[:200],
        "desc": meta["desc"], "message": meta["desc"], "fix_hint": meta["fix_hint"],
    }


def scan_extra_rules(path: str, text: str) -> list[dict]:
    """Findings from the seven static rules for one file. Never raises."""
    if not isinstance(text, str) or not text or len(text) > _MAX_TEXT or not isinstance(path, str):
        return []
    lines = text.splitlines()
    out: list[dict] = []
    for rid, detector in _DETECTORS:
        try:
            for count, (line_no, snippet) in enumerate(detector(path, lines, text), start=1):
                out.append(_finding(rid, path, line_no, snippet))
                if count >= _MAX_PER_RULE:
                    break
        except Exception as exc:  # noqa: BLE001 - a heuristic must never break the scan
            logger.debug("extra rule %s failed on %s: %r", rid, path, exc)
    return out
