"""
services/security_text_scanner.py — Iter arch-2a boundary-violation fix
+ 2026-11-27 Vanguard 28 single-scanner refactor.

`_scan_text` is a pure regex-rule matcher used by both the security-scan
router AND `services/loop_engine.py` (ship-time inline scan + diff-only
scan). loop_engine previously imported it FROM the router — an inverted
(service→router) dependency flagged by `services/architecture_health.py`'s
boundary scan. Moving the rule table + matcher here fixes that once and
for all (no more "restore the missing _scan_text import" incidents).

2026-11-27 — the rule table is no longer duplicated here. Vanguard's
`services/vanguard_scanner.py` is the SINGLE SOURCE OF TRUTH: this
module derives the legacy `_RULES` view from the vanguard catalog
(R1 + deep defs) and `_scan_text` adapts vanguard findings back to the
legacy shape ({rule_id, vuln, severity, file, line, snippet, desc})
that loop_engine and the security-scan router consume. The legacy
rule_ids (secret_aws_access_key, sql_string_format, ...) are kept so
existing callers and tests keep working; new vanguard rules surface
under their own vanguard rule name. Snippets are always masked.
"""
from __future__ import annotations

import re

from services.secret_masking import redact_secrets_in_text
from services.vanguard_scanner import (
    _DANGEROUS_PATTERN_DEFS,
    _DEEP_PATTERN_DEFS,
    _SECRET_PATTERN_DEFS,
    _detect_chains,
    _file_has_body_limit,
)
from services.vanguard_scanner import scan_text as _vanguard_scan_text

# ─── Legacy identity map ──────────────────────────────────────────────
# Legacy rule id → vanguard rule that detects it + vuln class. The
# DETECTION pattern/severity/description come from the vanguard
# catalog (looked up below); this table only carries the legacy
# identity + vuln class, so no regex is duplicated here.
_LEGACY_MAP: list[dict] = [
    {"id": "secret_aws_access_key", "vuln": "secret_leak",
     "vanguard": "secret_aws_access_key_deep"},
    {"id": "secret_openai_key", "vuln": "secret_leak",
     "vanguard": "secret_openai_key_deep"},
    {"id": "secret_github_pat", "vuln": "secret_leak",
     "vanguard": "secret_github_pat_deep"},
    {"id": "secret_stripe_live", "vuln": "secret_leak",
     "vanguard": "secret_stripe_live_deep"},
    {"id": "secret_private_key", "vuln": "secret_leak",
     "vanguard": "private_key"},
    {"id": "ssti_jinja_user_render", "vuln": "ssti",
     "vanguard": "ssti_jinja_user_render"},
    {"id": "sql_string_format", "vuln": "sql_injection",
     "vanguard": "sql_string_format_deep"},
    {"id": "sql_percent_format", "vuln": "sql_injection",
     "vanguard": "sql_percent_format_deep"},
    {"id": "nosql_where_operator", "vuln": "nosql_injection",
     "vanguard": "nosql_where_operator_deep"},
    {"id": "nosql_raw_body_query", "vuln": "nosql_injection",
     "vanguard": "nosql_raw_body_query_deep"},
    {"id": "redos_nested_quantifier", "vuln": "redos",
     "vanguard": "redos_nested_quantifier_deep"},
    {"id": "lpdos_no_body_limit_fastapi", "vuln": "lpdos",
     "vanguard": "lpdos_no_body_limit_deep", "max_per_file": 1},
    {"id": "clipboard_external_paste", "vuln": "clipboard",
     "vanguard": "clipboard_external_paste_deep"},
    {"id": "replay_jwt_no_jti", "vuln": "replay",
     "vanguard": "replay_jwt_no_jti_deep"},
]

# Display strings for R1 rules (the R1 defs carry no desc — the deep
# defs do). Detection still comes from vanguard; these are UI text.
_R1_DESCS: dict[str, str] = {
    "generic_api_key": "Hardcoded generic API key",
    "aws_access_key": "Hardcoded AWS access key id",
    "aws_secret_key": "Hardcoded AWS secret access key",
    "password_assignment": "Hardcoded password assignment",
    "token_assignment": "Hardcoded auth/bearer token",
    "private_key": "Embedded private key block",
    "github_token": "Hardcoded GitHub token",
    "slack_token": "Hardcoded Slack token",
    "generic_secret": "Hardcoded generic secret",
    "db_connection_string": "Database connection string with credentials",
    "stripe_live_key": "Hardcoded Stripe LIVE key",
    "stripe_test_key": "Hardcoded Stripe TEST key",
    "google_api_key": "Hardcoded Google API key",
    "sendgrid_key": "Hardcoded SendGrid API key",
    "openai_key": "Hardcoded OpenAI API key",
    "anthropic_key": "Hardcoded Anthropic API key",
    "openai_project": "Hardcoded OpenAI project key",
    "openrouter_key": "Hardcoded OpenRouter API key",
    "groq_key": "Hardcoded Groq API key",
    "xai_key": "Hardcoded xAI API key",
    "huggingface": "Hardcoded Hugging Face token",
    "telegram_bot": "Hardcoded Telegram bot token",
    "discord_bot": "Hardcoded Discord bot token",
    "npm_token": "Hardcoded npm token",
    "pypi_token": "Hardcoded PyPI token",
    "stripe_whsec": "Hardcoded Stripe webhook secret",
    "twilio_sid_key": "Hardcoded Twilio API key",
    "resend_key": "Hardcoded Resend API key",
    "jwt_compact": "Hardcoded compact JWT (bearer token)",
    "eval_usage": "Dynamic code execution via eval()",
    "exec_usage": "Dynamic code execution via exec()",
    "subprocess_shell_true": "Subprocess with shell=True",
    "os_system": "Shell command via os.system()",
    "shutil_rmtree": "Recursive delete via shutil.rmtree()",
    "os_remove": "File deletion via os.remove()",
    "os_unlink": "File deletion via os.unlink()",
    "pickle_loads": "Unsafe deserialisation via pickle.loads()",
    "yaml_unsafe_load": "Unsafe YAML load without a Loader",
    "requests_no_verify": "Outbound request with TLS verification off",
    "sql_string_format": "f-string SQL query — use parameterised cursors",
    "innerHTML_assignment": "Direct .innerHTML assignment (XSS sink)",
    "dangerously_set_html": "dangerouslySetInnerHTML (XSS sink)",
    "ssrf_user_url": "User-controlled URL passed to an HTTP client (SSRF)",
    "xxe_xml_parse": "XML parse without entity hardening (XXE)",
    "unsafe_redirect": "Redirect to a user-controlled URL (open redirect)",
    "cors_star": "CORS allows every origin (*)",
    "debug_true": "Debug mode enabled (DEBUG=True / debug=True)",
    "jwt_alg_none": "JWT accepted with alg=none",
    "crypto_md5_pass": "MD5 used on a password",
    "django_raw": "Raw SQL via Django .raw()/.extra()/RawSQL",
    "command_popen": "Shell command via os.popen()",
    "child_exec": "Shell command via child_process.exec()",
    "graphql_no_depth": "GraphQL endpoint without a query-depth limit",
    "express_no_limit": "Express JSON parser without a body limit",
    "path_traversal": "User-controlled value in a file path (traversal)",
    "cloud_metadata_ip": "Cloud metadata endpoint IP in code (SSRF target)",
    "python_syntax_error": "Python syntax error",
}

# Vuln class for R1 rules that have no legacy id (pass-2 findings).
_R1_VULN: dict[str, str] = {
    "eval_usage": "code_injection", "exec_usage": "code_injection",
    "subprocess_shell_true": "command_injection",
    "os_system": "command_injection",
    "shutil_rmtree": "destructive", "os_remove": "destructive",
    "os_unlink": "destructive",
    "pickle_loads": "deserialization",
    "yaml_unsafe_load": "deserialization",
    "requests_no_verify": "tls",
    "sql_string_format": "sql_injection",
    "innerHTML_assignment": "xss", "dangerously_set_html": "xss",
    "ssrf_user_url": "ssrf", "xxe_xml_parse": "xxe",
    "unsafe_redirect": "open_redirect", "cors_star": "cors",
    "debug_true": "misconfiguration", "jwt_alg_none": "jwt",
    "crypto_md5_pass": "weak_crypto", "django_raw": "sql_injection",
    "command_popen": "command_injection",
    "child_exec": "command_injection",
    "graphql_no_depth": "dos", "express_no_limit": "dos",
    "path_traversal": "path_traversal",
    "cloud_metadata_ip": "ssrf",
    "python_syntax_error": "syntax",
}

# Vanguard R1 rule name → legacy rule id (pass-2 translation). Rules
# without a legacy twin keep their vanguard name as the rule_id.
_VANGUARD_TO_LEGACY: dict[str, str] = {
    "aws_access_key": "secret_aws_access_key",
    "private_key": "secret_private_key",
    "github_token": "secret_github_pat",
    "stripe_live_key": "secret_stripe_live",
    "openai_key": "secret_openai_key",
    "sql_string_format": "sql_string_format",
}
# Legacy rule id → vanguard rule name (chain detection input).
_LEGACY_TO_VANGUARD: dict[str, str] = {
    e["id"]: e["vanguard"] for e in _LEGACY_MAP
}


def _build_rules() -> list[dict]:
    """Derive the legacy _RULES view from the vanguard catalog."""
    by_name: dict[str, dict] = {}
    for rid, rx, sev, desc in _DEEP_PATTERN_DEFS:
        by_name[rid] = {"pattern": re.compile(rx),
                        "severity": sev.lower(), "desc": desc}
    for name, rx, sev in _SECRET_PATTERN_DEFS + _DANGEROUS_PATTERN_DEFS:
        by_name[name] = {"pattern": re.compile(rx),
                         "severity": sev.lower(),
                         "desc": _R1_DESCS.get(name, name)}
    rules: list[dict] = []
    for entry in _LEGACY_MAP:
        src = by_name[entry["vanguard"]]
        rule: dict = {
            "id": entry["id"], "vuln": entry["vuln"],
            "severity": src["severity"], "pattern": src["pattern"],
            "desc": src["desc"],
        }
        if "max_per_file" in entry:
            rule["max_per_file"] = entry["max_per_file"]
        rules.append(rule)
    # Post-filters (receive the full LINE, not just the match):
    #  - sql_percent_format: LIKE/ILIKE is a pattern, not a sink.
    #  - replay_jwt_no_jti: a payload that already carries a jti is
    #    not a replay risk (the deep rule enforces the same skip).
    for rule in rules:
        if rule["id"] == "sql_percent_format":
            rule["post_filter"] = (
                lambda line: not re.search(r"(?i)\b(like|ilike)\b", line)
            )
        elif rule["id"] == "replay_jwt_no_jti":
            rule["post_filter"] = (
                lambda line: '"jti"' not in line and "'jti'" not in line
            )
    return rules


# Compatibility view, derived from the vanguard catalog at import.
# Kept importable: tests iterate it (test_rules_compile) and callers
# rely on the legacy rule ids. The DETECTION source of truth is
# services/vanguard_scanner — never add a regex here.
_RULES: list[dict] = _build_rules()


def _scan_text(path: str, text: str) -> list[dict]:
    """Run all rules over one file's content; return findings list.

    Two passes, one source of truth (vanguard):
    1. Legacy per-line loop over the derived _RULES — the exact old
       behaviour: first matching rule wins per line, max_per_file
       caps, vanguard: ignore skips, post_filters. LPDoS is skipped
       file-wide when a body-size limit is mounted.
    2. Full vanguard R1 sweep (scan_text) translated to the legacy
       shape — this is how the new provider/OWASP rules surface here,
       and how multi-line rules (private_key) fire. Findings that
       duplicate a pass-1 hit on the same (line, rule_id) are dropped.
    Chain findings are appended from _detect_chains over the combined
    hits (a file needs >=2 contributing rules for a chain).
    Snippets are always masked via secret_masking.
    """
    findings: list[dict] = []
    per_file_count: dict[str, int] = {}
    lines = text.splitlines()
    skip_lpdos = _file_has_body_limit(text)
    for line_idx, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        # Skip lines flagged with vanguard:ignore (same convention as
        # the commit-time scanner).
        if "vanguard: ignore" in line or "security-scan: ignore" in line:
            continue
        for rule in _RULES:
            if rule["id"] == "lpdos_no_body_limit_fastapi" and skip_lpdos:
                continue
            m = rule["pattern"].search(line)
            if not m:
                continue
            pf = rule.get("post_filter")
            if pf and not pf(line):
                continue
            cap = rule.get("max_per_file")
            if cap:
                seen = per_file_count.get(rule["id"], 0)
                if seen >= cap:
                    break
                per_file_count[rule["id"]] = seen + 1
            findings.append({
                "rule_id":  rule["id"],
                "vuln":     rule["vuln"],
                "severity": rule["severity"],
                "file":     path,
                "line":     line_idx,
                "snippet":  redact_secrets_in_text(line.strip()[:200]),
                "desc":     rule["desc"],
            })
            break
    # Pass 2 — full R1 sweep (new rules + multi-line rules).
    vg_hits: list[dict] = [
        {"name": _LEGACY_TO_VANGUARD.get(f["rule_id"], f["rule_id"]),
         "line": f["line"]}
        for f in findings
    ]
    if text:
        try:
            vg = _vanguard_scan_text(text, filepath=path)
        # Compatibility adapter: a vanguard sweep failure must degrade
        # to the legacy pass-1 hits, never crash the caller's scan.
        except Exception:  # noqa: BLE001
            vg = []
        seen_keys = {(f["line"], f["rule_id"]) for f in findings}
        for f in vg:
            rid = _VANGUARD_TO_LEGACY.get(f["name"], f["name"])
            key = (f["line"], rid)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            findings.append({
                "rule_id":  rid,
                "vuln":     _R1_VULN.get(
                    f["name"],
                    "secret_leak" if f.get("source") == "vanguard_007_secrets"
                    else "vanguard"),
                "severity": (f["severity"] or "").lower(),
                "file":     path,
                "line":     f["line"],
                "snippet":  redact_secrets_in_text(f.get("snippet") or ""),
                "desc":     _R1_DESCS.get(f["name"], f["name"]),
            })
            vg_hits.append({"name": f["name"], "line": f["line"]})
    # Chains over the combined hits for this file.
    try:
        chains = _detect_chains({path: vg_hits})
    # Same fallback as above: no chains on error.
    except Exception:  # noqa: BLE001
        chains = []
    for c in chains:
        findings.append({
            "rule_id":  c["name"],
            "vuln":     "chain",
            "severity": (c["severity"] or "").lower(),
            "file":     path,
            "line":     c["line"],
            "snippet":  c.get("snippet") or "",
            "desc":     c.get("desc") or c["name"],
        })
    return findings
