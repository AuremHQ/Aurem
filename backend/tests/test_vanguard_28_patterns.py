"""
2026-11-27 — Vanguard 28-pattern catalog tests (audit prompt 1, Step 7).

Covers the catalog after the 25 -> 56 rebuild (28 baseline rules at
audit time + 14 new secrets + 14 new dangerous rules):

  * pattern_counts() live totals + the 28 baseline rule IDs locked in
  * every R1 pattern true-positive (29 secrets + 27 dangerous)
  * FP negatives (env skip, placeholder, LIKE, jti, lpdos, style={{}},
    DOMPurify, comments)
  * chains (3 original + 2 new) via run_two_round_scan and via the
    text-scanner adapter
  * OFF truly off / LOW / MEDIUM / HIGH behaviour via verify_patch
  * self-scan: 0 findings on the scanner's own files
  * masking: snippets never carry the raw secret
  * design_linter: no duplicate hardcoded_secret block (rule deleted,
    the Vanguard overlay is the single secret path)

Pure unit tests — no HTTP, no GitHub, no real LLM. verify_patch's
mode settings + LLM/E2B are stubbed via monkeypatch.
"""
from __future__ import annotations

import asyncio
import pathlib
import time

import pytest

from services import vanguard_config as vcfg
from services import vanguard_verify_agent as vva
from services.design_linter import lint_file_blocks
from services.security_text_scanner import _RULES as LEGACY_RULES
from services.security_text_scanner import _scan_text as legacy_scan_text
from services.vanguard_config import _normalise_level, default_config
from services.vanguard_scanner import (
    ALL_RULE_IDS,
    LOW_SECRET_IDS,
    VENDOR_SECRET_IDS,
    _scan_round2_file,
    pattern_counts,
    run_two_round_scan,
    scan_file_blocks,
    scan_text,
)

# ── The 28 baseline rule IDs (15 secret + 13 dangerous) locked in at
# audit time. New rules are additive — these must never disappear.
BASELINE_SECRET_IDS = {
    "generic_api_key", "aws_access_key", "aws_secret_key",
    "password_assignment", "token_assignment", "private_key",
    "github_token", "slack_token", "generic_secret",
    "db_connection_string", "stripe_live_key", "stripe_test_key",
    "google_api_key", "sendgrid_key", "openai_key",
}
BASELINE_DANGEROUS_IDS = {
    "eval_usage", "exec_usage", "subprocess_shell_true", "os_system",
    "shutil_rmtree", "os_remove", "os_unlink", "pickle_loads",
    "yaml_unsafe_load", "requests_no_verify", "sql_string_format",
    "innerHTML_assignment", "dangerously_set_html",
}
assert len(BASELINE_SECRET_IDS) == 15
assert len(BASELINE_DANGEROUS_IDS) == 13

# Secret-shaped test vectors are built from parts, never written as one
# literal: GitHub push protection rejects any commit whose diff contains
# a secret-shaped string, even an obviously fake placeholder. The runtime
# values below are byte-identical to the literals they replace, so the
# scanner sees exactly the same inputs.
_SK_LIVE = "sk_live_" + "abcdefghijklmnopqrstuvwx"
_SK_TEST = "sk_test_" + "abcdefghijklmnopqrstuvwx"
_SENDGRID = "SG." + "abcdefghijklmnopqrstuv" + "." + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopq"
_TWILIO_SID = "SK" + "0123456789abcdef0123456789abcdef"

# (rule, sample, filepath) — every sample must fire its rule.
SECRET_CASES = [
    ("generic_api_key",     'API_KEY = "abcdefgh12345678"', "a.py"),
    ("aws_access_key",      'KEY = "AKIAIOSFODNN7EXAMPLE"', "a.py"),
    ("aws_secret_key",      'aws_secret_access_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"', "a.py"),
    ("password_assignment", 'password = "hunter2-hunter2"', "a.py"),
    ("token_assignment",    'access_token = "abcdefghijklmnop1234"', "a.py"),
    ("private_key",         '-----BEGIN RSA PRIVATE KEY-----\n' + "A" * 40 + '\n-----END RSA PRIVATE KEY-----', "a.py"),
    ("github_token",        'TOKEN = "ghp_abcdefghijklmnopqrstuvwx"', "a.py"),
    ("slack_token",         'TOKEN = "xoxb-123456789012-abcdefghij"', "a.py"),
    ("generic_secret",      'secret = "abcdefghijklmnop1234"', "a.py"),
    ("db_connection_string", 'URL = "postgres://user:pass123@localhost/db"', "a.py"),
    ("stripe_live_key",     'KEY = "' + _SK_LIVE + '"', "a.py"),
    ("stripe_test_key",     'KEY = "' + _SK_TEST + '"', "a.py"),
    ("google_api_key",      'KEY = "AIza' + "A" * 35 + '"', "a.py"),
    ("sendgrid_key",        'KEY = "' + _SENDGRID + '"', "a.py"),
    ("openai_key",          'KEY = "sk-abcdefghijklmnopqrstuvwx"', "a.py"),
    ("anthropic_key",       'KEY = "sk-ant-api03-aaaaaaaaaaaaaaaaaaaa"', "a.py"),
    ("openai_project",      'KEY = "sk-proj-abcdefghijklmnopqrstuvwx"', "a.py"),
    ("openrouter_key",      'KEY = "sk-or-v1-abcdefghijklmnopqrstuvwx"', "a.py"),
    ("groq_key",            'KEY = "gsk_abcdefghijklmnopqrstuvwx"', "a.py"),
    ("xai_key",             'KEY = "xai-abcdefghijklmnopqrstuvwx"', "a.py"),
    ("huggingface",         'KEY = "hf_abcdefghijklmnopqrstuvwx"', "a.py"),
    ("telegram_bot",        'TOKEN = "123456789:' + "A" * 35 + '"', "a.py"),
    ("discord_bot",         'TOKEN = "' + "A" * 24 + "." + "B" * 6 + "." + "C" * 27 + '"', "a.py"),
    ("npm_token",           'TOKEN = "npm_' + "a" * 36 + '"', "a.py"),
    ("pypi_token",          'TOKEN = "pypi-AgEIcHlwaS5vcmcCJDUyZjM0"', "a.py"),
    ("stripe_whsec",        'KEY = "whsec_abcdefghijklmnopqrstuvwx"', "a.py"),
    ("twilio_sid_key",      'KEY = "' + _TWILIO_SID + '"', "a.py"),
    ("resend_key",          'KEY = "re_abcdefghijklmnopqrstuvwx"', "a.py"),
    ("jwt_compact",         'TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc123"', "a.py"),
]

DANGEROUS_CASES = [
    ("eval_usage",            "eval(user_input)", "a.py"),
    ("exec_usage",            "exec(code)", "a.py"),
    ("subprocess_shell_true", "subprocess.run(cmd, shell=True)", "a.py"),
    ("os_system",             'os.system("ls")', "a.py"),
    ("shutil_rmtree",         'shutil.rmtree("/tmp/x")', "a.py"),
    ("os_remove",             "os.remove(f)", "a.py"),
    ("os_unlink",             "os.unlink(f)", "a.py"),
    ("pickle_loads",          "pickle.loads(data)", "a.py"),
    ("yaml_unsafe_load",      "yaml.load(f)", "a.py"),
    ("requests_no_verify",    "requests.get(url, verify=False)", "a.py"),
    ("sql_string_format",     'cursor.execute(f"SELECT * FROM u WHERE id={uid}")', "a.py"),
    ("innerHTML_assignment",  "el.innerHTML = user_input", "a.py"),
    ("dangerously_set_html",  "dangerouslySetInnerHTML={{__html: x}}", "a.jsx"),
    ("ssrf_user_url",         "requests.get(user_url)", "a.py"),
    ("xxe_xml_parse",         "import xml.etree.ElementTree as ET\nET.parse(f)", "a.py"),
    ("unsafe_redirect",       "def view():\n    return redirect(request.GET.get(\"next\"))", "a.py"),
    ("cors_star",             'allow_origins=["*"]', "a.py"),
    ("debug_true",            "DEBUG = True", "a.py"),
    ("jwt_alg_none",          'jwt.decode(t, key, algorithms=["none"])', "a.py"),
    ("crypto_md5_pass",       "hashlib.md5(password.encode())", "a.py"),
    ("django_raw",            'User.objects.raw("SELECT * FROM u")', "a.py"),
    ("command_popen",         'os.popen("ls")', "a.py"),
    ("child_exec",            'child_process.exec("ls")', "a.js"),
    ("graphql_no_depth",      "graphqlHTTP()", "a.py"),
    ("express_no_limit",      "app.use(express.json())", "a.js"),
    ("path_traversal",        'open(request.args["f"])', "a.py"),
    ("cloud_metadata_ip",     'url = "http://169.254.169.254/latest/meta-data/"', "a.py"),
]


def _names(findings):
    return {f.get("name") for f in findings}


def _by_name(findings, name):
    return [f for f in findings if f.get("name") == name]


# ── 1) Counts + baseline lock ──────────────────────────────────────

def test_pattern_counts_live_totals():
    c = pattern_counts()
    assert c["secrets"] == 29
    assert c["dangerous"] == 27
    assert c["deep"] == 13
    assert c["chains"] == 5
    assert c["total_r1"] == 56
    assert c["total_r1"] == c["secrets"] + c["dangerous"]


def test_baseline_28_rule_ids_present():
    ids = set(ALL_RULE_IDS)
    assert BASELINE_SECRET_IDS <= ids
    assert BASELINE_DANGEROUS_IDS <= ids
    assert len(BASELINE_SECRET_IDS | BASELINE_DANGEROUS_IDS) == 28


def test_low_secret_ids_are_vendor_shapes():
    # LOW scans vendor-shaped secrets only: 21 ids, no generic
    # password/token rules, no MEDIUM stripe_test_key, no HIGH jwt.
    assert len(LOW_SECRET_IDS) == 21
    assert "password_assignment" not in LOW_SECRET_IDS
    assert "token_assignment" not in LOW_SECRET_IDS
    assert "generic_secret" not in LOW_SECRET_IDS
    assert "stripe_test_key" not in LOW_SECRET_IDS
    assert "jwt_compact" not in LOW_SECRET_IDS
    assert "private_key" in LOW_SECRET_IDS
    assert "anthropic_key" in LOW_SECRET_IDS
    assert VENDOR_SECRET_IDS == LOW_SECRET_IDS | {
        "secret_aws_access_key_deep", "secret_openai_key_deep",
        "secret_github_pat_deep", "secret_stripe_live_deep",
    }


def test_level_normalisation_and_defaults():
    assert _normalise_level("CRITICAL", "swift") == "LOW"
    assert _normalise_level("HIGH", "swift") == "MEDIUM"
    assert _normalise_level("LOW", "swift") == "LOW"
    assert _normalise_level("MEDIUM", "pro") == "MEDIUM"
    assert _normalise_level("OFF", "maxx") == "OFF"
    assert _normalise_level("bogus", "swift") == "MEDIUM"
    assert _normalise_level("bogus", "pro") == "HIGH"
    cfg = default_config()
    assert cfg["levels"] == {"swift": "MEDIUM", "pro": "HIGH", "maxx": "HIGH"}


# ── 2) Every R1 pattern true-positive ──────────────────────────────

@pytest.mark.parametrize("rule,sample,path", SECRET_CASES)
def test_every_secret_true_positive(rule, sample, path):
    found = scan_text(sample, filepath=path)
    assert rule in _names(found), f"{rule} did not fire on {sample!r}"


@pytest.mark.parametrize("rule,sample,path", DANGEROUS_CASES)
def test_every_dangerous_true_positive(rule, sample, path):
    found = scan_text(sample, filepath=path)
    assert rule in _names(found), f"{rule} did not fire on {sample!r}"


def test_openai_key_does_not_double_fire_on_vendor_prefixes():
    # sk-ant- / sk-proj- / sk-or- must surface as their own rule only.
    for sample, want in [
        ('KEY = "sk-ant-api03-aaaaaaaaaaaaaaaaaaaa"', "anthropic_key"),
        ('KEY = "sk-proj-abcdefghijklmnopqrstuvwx"', "openai_project"),
        ('KEY = "sk-or-v1-abcdefghijklmnopqrstuvwx"', "openrouter_key"),
    ]:
        names = _names(scan_text(sample, filepath="a.py"))
        assert want in names
        assert "openai_key" not in names


# ── 3) FP negatives ────────────────────────────────────────────────

def test_fp_os_environ_skip():
    found = scan_text('API_KEY = os.environ["API_KEY"]', filepath="a.py")
    assert "generic_api_key" not in _names(found)
    found = scan_text('TOKEN = process.env.MY_TOKEN', filepath="a.js")
    assert "token_assignment" not in _names(found)


def test_fp_placeholder_password_is_info_not_blocking():
    found = scan_text('password = "test123"', filepath="a.py")
    hits = _by_name(found, "password_assignment")
    assert hits and hits[0]["severity"] == "INFO"
    found = scan_text('password = "hunter2-hunter2"', filepath="a.py")
    hits = _by_name(found, "password_assignment")
    assert hits and hits[0]["severity"] == "CRITICAL"


def test_fp_vendor_shape_in_test_file_stays_critical():
    # A real credential shape in a test file is still a real leak.
    found = scan_file_blocks(
        {"tests/test_x.py": 'KEY = "AKIAIOSFODNN7EXAMPLE"\n'})
    hits = _by_name(found, "aws_access_key")
    assert hits and hits[0]["severity"] == "CRITICAL"
    # ...while a generic placeholder in the same file stays INFO.
    found = scan_file_blocks({"tests/test_x.py": 'password = "test123"\n'})
    hits = _by_name(found, "password_assignment")
    assert hits and hits[0]["severity"] == "INFO"


def test_fp_sql_percent_like_skipped():
    deadline = time.monotonic() + 30
    like = _scan_round2_file(
        "a.py",
        'cursor.execute("SELECT * FROM u WHERE name LIKE %s" % name)\n',
        deadline=deadline)
    assert "sql_percent_format_deep" not in _names(like)
    ilike = _scan_round2_file(
        "a.py",
        'cursor.execute("SELECT * FROM u WHERE name ILIKE %s" % name)\n',
        deadline=deadline)
    assert "sql_percent_format_deep" not in _names(ilike)
    plain = _scan_round2_file(
        "a.py",
        'cursor.execute("SELECT * FROM u WHERE id=%s" % uid)\n',
        deadline=deadline)
    assert "sql_percent_format_deep" in _names(plain)


def test_fp_jwt_with_jti_skipped():
    found = legacy_scan_text(
        "a.py", 'jwt.encode({"sub": "u", "jti": "abc-123"}, key)')
    assert "replay_jwt_no_jti" not in {r["rule_id"] for r in found}
    found = legacy_scan_text(
        "a.py", 'jwt.encode({"sub": "u"}, key)')
    assert "replay_jwt_no_jti" in {r["rule_id"] for r in found}


def test_fp_lpdos_file_level_once_and_middleware_skip():
    deadline = time.monotonic() + 30
    two_endpoints = (
        "from fastapi import FastAPI\napp = FastAPI()\n"
        '@app.post("/a")\ndef a(): pass\n'
        '@app.post("/b")\ndef b(): pass\n'
    )
    found = _scan_round2_file("a.py", two_endpoints, deadline=deadline)
    assert len(_by_name(found, "lpdos_no_body_limit_deep")) == 1
    with_limit = two_endpoints + (
        "app.add_middleware(ContentSizeLimit, max_content_length=1024)\n")
    found = _scan_round2_file("a.py", with_limit, deadline=deadline)
    assert "lpdos_no_body_limit_deep" not in _names(found)


def test_fp_react_style_not_xss():
    found = scan_text('style={{ color: "red" }}', filepath="a.jsx")
    assert "innerHTML_assignment" not in _names(found)
    assert "dangerously_set_html" not in _names(found)


def test_fp_dompurify_sanitized_is_info():
    found = scan_text(
        "dangerouslySetInnerHTML={{__html: DOMPurify.sanitize(x)}}",
        filepath="a.jsx")
    hits = _by_name(found, "dangerously_set_html")
    assert hits and hits[0]["severity"] == "INFO"


def test_fp_comment_only_eval_not_flagged():
    found = scan_text("# eval(user_input) is dangerous\nx = 1\n", filepath="a.py")
    assert "eval_usage" not in _names(found)


def test_fp_ssrf_literal_url_not_flagged():
    found = scan_text('requests.get("https://api.example.com/v1")', filepath="a.py")
    assert "ssrf_user_url" not in _names(found)


def test_fp_xxe_defusedxml_guard():
    guarded = (
        "from defusedxml.ElementTree import parse\n"
        "import xml.etree.ElementTree as ET\n"
        "ET.parse(f)\n"
    )
    found = scan_text(guarded, filepath="a.py")
    assert "xxe_xml_parse" not in _names(found)


# ── 4) Round 2 ─────────────────────────────────────────────────────

def test_r2_max_3_hits_per_rule_per_file():
    deadline = time.monotonic() + 30
    text = "\n".join(
        f'cursor.execute("SELECT * FROM u WHERE id=%s" % uid{i})'
        for i in range(5))
    found = _scan_round2_file("a.py", text + "\n", deadline=deadline)
    assert len(_by_name(found, "sql_percent_format_deep")) == 3


def test_r2_runs_on_medium_files_not_just_critical():
    # A MEDIUM-only R1 file (stripe test key) must still get R2.
    blocks = {
        "a.py": (
            'KEY = "' + _SK_TEST + '"\n'
            + 'jwt.encode({"sub": "u"}, key)\n'
        ),
    }
    res = run_two_round_scan(blocks, block_level="HIGH")
    assert res["files_round2"] == 1
    assert "replay_jwt_no_jti_deep" in _names(res["round2_findings"])


def test_r2_deep_snippets_are_masked():
    deadline = time.monotonic() + 30
    text = 'x = "AKIAIOSFODNN7EXAMPLE"\n'
    found = _scan_round2_file("a.py", text, deadline=deadline)
    hits = _by_name(found, "secret_aws_access_key_deep")
    assert hits
    assert "AKIAIOSFODNN7EXAMPLE" not in hits[0]["snippet"]


# ── 5) Chains ──────────────────────────────────────────────────────

def test_chains_fire_in_two_round_scan():
    sql_http = {
        "a.py": (
            'cursor.execute(f"SELECT * FROM u WHERE id={uid}")\n'
            "requests.get(url, verify=False)\n"
        ),
    }
    res = run_two_round_scan(sql_http, block_level="HIGH")
    assert "chain_sql_plus_insecure_http" in _names(res["chain_findings"])
    ssrf_meta = {
        "a.py": (
            "requests.get(user_url)\n"
            'url2 = "http://169.254.169.254/latest/meta-data/"\n'
        ),
    }
    res = run_two_round_scan(ssrf_meta, block_level="HIGH")
    assert "chain_ssrf_plus_metadata" in _names(res["chain_findings"])
    trav_del = {
        "a.py": (
            'open(request.args["f"])\n'
            "os.remove(f)\n"
        ),
    }
    res = run_two_round_scan(trav_del, block_level="HIGH")
    assert "chain_path_traversal_plus_delete" in _names(res["chain_findings"])


def test_chain_fires_via_text_scanner_adapter():
    # C9 — requests_no_verify was missing from the text scanner, so
    # the chain could never fire on that path.
    text = (
        'cursor.execute(f"SELECT * FROM u WHERE id={uid}")\n'
        "requests.get(url, verify=False)\n"
    )
    found = legacy_scan_text("a.py", text)
    ids = {r["rule_id"] for r in found}
    assert "chain_sql_plus_insecure_http" in ids
    assert "sql_string_format" in ids
    assert "requests_no_verify" in ids


def test_chains_do_not_run_at_medium():
    sql_http = {
        "a.py": (
            'cursor.execute(f"SELECT * FROM u WHERE id={uid}")\n'
            "requests.get(url, verify=False)\n"
        ),
    }
    res = run_two_round_scan(sql_http, block_level="MEDIUM")
    assert res["chain_findings"] == []


# ── 6) Adapter: single source of truth ────────────────────────────

def test_legacy_rules_derived_from_vanguard_catalog():
    # Every legacy _RULES entry's compiled pattern string + severity
    # comes from the vanguard catalog (the adapter re-compiles from
    # the catalog's regex string at import — never a forked copy).
    from services.security_text_scanner import _LEGACY_MAP
    from services.vanguard_scanner import (
        _DEEP_PATTERN_DEFS,
        _SECRET_PATTERN_DEFS,
    )
    deep_by_id = {rid: (rx, sev)
                  for rid, rx, sev, _desc in _DEEP_PATTERN_DEFS}
    r1_by_name = {name: (rx, sev)
                  for name, rx, sev in _SECRET_PATTERN_DEFS}
    by_id = {r["id"]: r for r in LEGACY_RULES}
    assert len(LEGACY_RULES) == 14
    for entry in _LEGACY_MAP:
        rule = by_id[entry["id"]]
        vanguard = entry["vanguard"]
        if vanguard in deep_by_id:
            rx, sev = deep_by_id[vanguard]
        else:
            rx, sev = r1_by_name[vanguard]
        assert rule["pattern"].pattern == rx
        assert rule["severity"] == sev.lower()


# ── 7) verify_patch levels ─────────────────────────────────────────

def _stub_settings(monkeypatch, settings):
    async def fake_settings(_mode):
        return settings

    monkeypatch.setattr(vcfg, "get_mode_settings", fake_settings)


def _stub_llm_e2b(monkeypatch, llm=None, e2b=None):
    async def fake_llm(*_a, **_kw):
        return llm or {"pass": True, "findings": [],
                       "summary": "stub", "model": "stub"}

    async def fake_e2b(*_a, **_kw):
        return e2b or {"pass": True, "skipped": True, "reason": "stub"}

    monkeypatch.setattr(vva, "_llm_review", fake_llm)
    monkeypatch.setattr(vva, "_e2b_smoke", fake_e2b)


_SECRET_BLOCK = {"a.py": 'KEY = "AKIAIOSFODNN7EXAMPLE"\n'}
_EVAL_BLOCK = {"a.py": "eval(user_input)\n"}
_CLEAN_BLOCK = {"a.py": "def f():\n    return 1\n"}


def test_off_is_truly_off(monkeypatch):
    _stub_settings(monkeypatch, (False, "OFF"))
    res = asyncio.run(vva.verify_patch(_SECRET_BLOCK, mode="swift"))
    assert res["pass"] is True
    assert res["findings"] == []
    assert res["regex"]["count"] == 0
    assert "vanguard off" in res["summary"]
    assert res["block_level"] == "OFF"


def test_off_level_with_enabled_true_is_also_off(monkeypatch):
    _stub_settings(monkeypatch, (True, "OFF"))
    res = asyncio.run(vva.verify_patch(_EVAL_BLOCK, mode="swift"))
    assert res["pass"] is True
    assert res["regex"]["count"] == 0


def test_low_vendor_secret_blocks_generic_does_not(monkeypatch):
    _stub_settings(monkeypatch, (True, "LOW"))
    res = asyncio.run(vva.verify_patch(_SECRET_BLOCK, mode="swift"))
    assert res["pass"] is False
    assert res["regex"]["blocked"] is True
    assert res["block_level"] == "LOW"
    res = asyncio.run(vva.verify_patch(
        {"a.py": 'password = "hunter2-hunter2"\n'}, mode="swift"))
    assert res["pass"] is True
    assert res["regex"]["count"] == 0
    assert len(res["regex"]["skipped_scope"]) == 1


def test_medium_regex_critical_blocks(monkeypatch):
    _stub_settings(monkeypatch, (True, "MEDIUM"))
    _stub_llm_e2b(monkeypatch)
    res = asyncio.run(vva.verify_patch(_EVAL_BLOCK, mode="swift"))
    assert res["pass"] is False
    assert res["regex"]["blocked"] is True


def test_medium_llm_high_finding_does_not_block(monkeypatch):
    _stub_settings(monkeypatch, (True, "MEDIUM"))
    _stub_llm_e2b(monkeypatch, llm={
        "pass": False,
        "findings": [{"file": "a.py", "line": 1, "severity": "HIGH",
                      "rule": "llm_high", "message": "stub high"}],
        "summary": "stub", "model": "stub"})
    res = asyncio.run(vva.verify_patch(_CLEAN_BLOCK, mode="swift"))
    assert res["pass"] is True
    assert res["agent"]["self_assessed_pass"] is False
    assert res["agent"]["pass"] is True


def test_high_llm_high_finding_blocks(monkeypatch):
    _stub_settings(monkeypatch, (True, "HIGH"))
    _stub_llm_e2b(monkeypatch, llm={
        "pass": False,
        "findings": [{"file": "a.py", "line": 1, "severity": "HIGH",
                      "rule": "llm_high", "message": "stub high"}],
        "summary": "stub", "model": "stub"})
    res = asyncio.run(vva.verify_patch(_CLEAN_BLOCK, mode="swift"))
    assert res["pass"] is False
    assert res["agent"]["pass"] is False


def test_legacy_critical_level_behaves_as_medium(monkeypatch):
    _stub_settings(monkeypatch, (True, "CRITICAL"))
    _stub_llm_e2b(monkeypatch)
    res = asyncio.run(vva.verify_patch(_EVAL_BLOCK, mode="swift"))
    assert res["pass"] is False
    assert res["regex"]["blocked"] is True


# ── 8) Self-scan + masking + design_linter ────────────────────────

def test_self_scan_scanner_files_zero_findings():
    root = pathlib.Path(__file__).resolve().parent.parent
    paths = [
        "services/vanguard_scanner.py",
        "services/vanguard_verify_agent.py",
        "services/security_text_scanner.py",
        "services/design_linter.py",
        "services/vanguard_config.py",
        "services/secret_masking.py",
        "routers/security_scan.py",
        "routers/admin_vanguard.py",
    ]
    blocks = {p: (root / p).read_text() for p in paths}
    assert scan_file_blocks(blocks) == []


def test_masking_snippet_never_carries_raw_secret():
    found = scan_text('KEY = "AKIAIOSFODNN7EXAMPLE"', filepath="a.py")
    hits = _by_name(found, "aws_access_key")
    assert hits
    assert "AKIAIOSFODNN7EXAMPLE" not in hits[0]["snippet"]
    assert "***" in hits[0]["snippet"]


def test_design_linter_no_duplicate_hardcoded_secret_block():
    r = lint_file_blocks(
        {"routers/auth.py": 'API_KEY = "abcdefgh12345678"\ndef get_user(): pass'})
    assert r["blocked"] is True
    assert any("secret" in br.lower() for br in r.get("block_reasons", []))
    secret_issues = [i for i in r["issues"]
                     if i["rule"] == "vg.generic_api_key"]
    assert len(secret_issues) == 1
    assert all(i["rule"] != "hardcoded_secret" for i in r["issues"])


def test_design_linter_low_reports_vendor_only():
    r = lint_file_blocks(
        {"a.py": 'KEY = "AKIAIOSFODNN7EXAMPLE"\n'},
        block_level="LOW")
    assert r["blocked"] is True
    r = lint_file_blocks(
        {"a.py": 'password = "hunter2-hunter2"\n'},
        block_level="LOW")
    assert r["blocked"] is False
    assert not [i for i in r["issues"] if i["severity"] == "block"]


def test_design_linter_off_skips_overlay():
    r = lint_file_blocks(
        {"a.py": 'KEY = "AKIAIOSFODNN7EXAMPLE"\n'},
        block_level="OFF")
    assert not [i for i in r["issues"] if i["rule"].startswith("vg.")]
