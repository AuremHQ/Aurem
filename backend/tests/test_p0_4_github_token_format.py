"""
test_p0_4_github_token_format.py — 2026-09-27 P0-4

GitHub is migrating installation/App tokens to a new stateless format:
`ghs_<app_id>_<JWT>`, up to ~520 chars, where the JWT segment is
dot-separated base64url (`-`/`_`). Every regex in this codebase that
matches/redacts a GitHub token used to assume BOTH a fixed length
(`{36}`) AND a plain-alphanumeric charset (`[A-Za-z0-9]`) — either
assumption alone is enough to silently stop matching a real leaked
token in the new format. This guards every location that was fixed.
"""
from __future__ import annotations

import re
import secrets
import string


def _synthetic_new_format_token(prefix: str = "ghs") -> str:
    """Builds a token shaped exactly like GitHub's documented new
    format: `ghs_<app_id>_<3-part-dot-separated-base64url-JWT>`,
    landing right around the real ~520-char length."""
    charset = string.ascii_letters + string.digits + "-_"
    jwt = ".".join(
        "".join(secrets.choice(charset) for _ in range(170)) for _ in range(3)
    )
    return f"{prefix}_1234567_{jwt}"


def test_synthetic_token_matches_documented_shape():
    tok = _synthetic_new_format_token()
    assert 500 <= len(tok) <= 540, f"unrealistic synthetic token length: {len(tok)}"
    assert "." in tok and "-" in tok


def test_bug_hunt_rules_github_app_token():
    import services.bug_hunt_rules as bhr
    tok = _synthetic_new_format_token("ghs")
    pat = next(p for (n, p, s, d) in bhr._SECRET_RULES if n == "github_app_token")
    m = pat.search(f"leaked: {tok}")
    assert m and m.group(0) == tok


def test_bug_hunt_rules_github_pat_and_oauth():
    import services.bug_hunt_rules as bhr
    for prefix, rule_name in [("ghp", "github_pat"), ("gho", "github_oauth_token")]:
        tok = _synthetic_new_format_token(prefix)
        pat = next(p for (n, p, s, d) in bhr._SECRET_RULES if n == rule_name)
        m = pat.search(f"leaked: {tok}")
        assert m and m.group(0) == tok, f"{rule_name} failed to match {prefix}_ token"


def test_output_guard_redacts_full_token():
    import services.output_guard as og
    tok = _synthetic_new_format_token("ghs")
    clean, stripped = og.strip_machinery_leak(f"here: {tok}", universal_only=True)
    assert stripped and tok not in clean


def test_task_chain_logging_redacts_full_token():
    import services.task_chain_logging as tcl
    tok = _synthetic_new_format_token("ghs")
    redacted = tcl.redact_secrets(f"here: {tok}")
    assert tok not in redacted


def test_security_text_scanner_matches_full_token():
    import services.security_text_scanner as sts
    tok = _synthetic_new_format_token("ghs")
    rule = next(r for r in sts._RULES if r["id"] == "secret_github_pat")
    assert rule["pattern"].search(f"leaked: {tok}")


def test_upload_redactor_matches_all_prefixes():
    import services.ora_chat.upload_redactor as ur
    pat = next(p for (p, name, desc) in ur._PATTERNS if name == "github_pat")
    for prefix in ("ghp", "gho", "ghs", "ghu"):
        tok = _synthetic_new_format_token(prefix)
        assert pat.search(f"leaked: {tok}"), f"upload_redactor missed {prefix}_ token"


def test_post_task_scanner_matches_full_token():
    import services.post_task_scanner as pts
    tok = _synthetic_new_format_token("ghs")
    sample = f"leaked: {tok}"
    assert any(re.search(p, sample) for p, _desc in pts.SECRET_PATTERNS)


def test_vanguard_scanner_matches_full_token():
    import services.vanguard_scanner as vs
    tok = _synthetic_new_format_token("ghs")
    findings = vs.scan_text(f"leaked: {tok}", "demo/config.txt", include_dangerous=False)
    assert any(f["name"] == "github_token" for f in findings)


def test_deploy_scrub_redacts_full_token():
    import routers.deploy as dep
    tok = _synthetic_new_format_token("ghs")
    scrubbed = dep._scrub(f"leaked: {tok}")
    assert tok not in scrubbed


def test_g4_secret_scanner_matches_full_token():
    import scripts.g4_secret_scanner as g4
    tok = _synthetic_new_format_token("ghs")
    assert g4.PATTERNS["github_token"].search(f"leaked: {tok}")


def test_connect_body_accepts_full_length_token():
    """Pydantic max_length was 512 — a real ~520-char token would
    have been hard-rejected with a 422 before ever reaching GitHub."""
    from routers.github_deploy import ConnectBody
    tok = _synthetic_new_format_token("ghs")
    body = ConnectBody(token=tok)
    assert body.token == tok
