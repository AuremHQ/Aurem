"""Check Library v2 / Group 1 (L7.1, L7.2): each check fires on the bad
fixture and stays silent on the clean one.  Secret values must never appear
in an item."""
from __future__ import annotations

import json

import pytest

from services import checkup_meta as cm
from services.checkup_checks_secrets import check_env_files, check_secrets_in_history
from services.checkup_history import secrets_in_patch
from services.checkup_orchestrator import run_checkup
from services.checkup_secret_patterns import find_secrets, secret_env_names

STRIPE = "sk_live_" + "A1b2C3d4E5f6G7h8I9j0K1l2"
AWS = "AKIA" + "Q7ZP4XW2LM9N3RT5"
APP = {"src/db.js": "const url = process.env.DATABASE_URL;\n", "package.json": "{}"}


def _tc(paths, files=None, history=None, app=APP):
    return cm.with_meta(dict(app), paths, files or {}, history)


# ── L7.1 ──────────────────────────────────────────────────────────────
def test_committed_env_with_real_secret_fires_without_leaking_value():
    tc = _tc([".env", "src/db.js", ".gitignore"], {".env": f"STRIPE_SECRET_KEY={STRIPE}\nPORT=3000\n",
                                                   ".gitignore": ".env\n"})
    [item] = check_env_files(tc)
    assert item["rule_id"] == "env_file_committed" and item["severity"] == "critical"
    assert item["evidence"]["file"] == ".env"
    assert "STRIPE_SECRET_KEY" in item["evidence"]["detail"]
    assert STRIPE not in json.dumps(item)


def test_other_env_variants_are_checked_too():
    tc = _tc(["backend/.env.production"], {"backend/.env.production": "JWT_SECRET=9fQ2xLmZpR7vK1aT\n", ".gitignore": ".env\n"})
    assert [i["evidence"]["file"] for i in check_env_files(tc)] == ["backend/.env.production"]


@pytest.mark.parametrize("env_text", [
    "VITE_SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1NiJ9.abc.def\nNEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY=pk_live_abcdef\n",
    "API_KEY=\nSECRET=your_secret_here\nTOKEN=<paste token>\nDATABASE_URL=postgres://u:p@localhost/db\n",
    "PORT=3000\nNODE_ENV=production\n# STRIPE_SECRET_KEY=" + STRIPE + "\n",
    "SECRET_KEY=changethis\nFIRST_SUPERUSER_PASSWORD=change_me\nPOSTGRES_PASSWORD=changeit\n",
])
def test_committed_env_without_real_secrets_is_silent(env_text):
    tc = _tc([".env", "src/db.js"], {".env": env_text, ".gitignore": ".env\n"})
    assert check_env_files(tc) == []


def test_env_templates_are_not_flagged():
    tc = _tc([".env.example", ".env.sample"], {".gitignore": ".env\n"})
    assert check_env_files(tc) == []


def test_env_not_gitignored_fires_when_project_uses_env():
    [item] = check_env_files(_tc(["src/db.js"], {".gitignore": "node_modules\n"}))
    assert item["rule_id"] == "env_not_gitignored" and item["severity"] == "medium"
    [no_file] = check_env_files(_tc(["src/db.js"]))
    assert "no .gitignore" in no_file["evidence"]["detail"]


@pytest.mark.parametrize("gitignore", [".env\n", "/.env\n", ".env*\n", "*.env\n", "**/.env\n", "node_modules\n.env.local\n.env\n"])
def test_env_gitignored_variants_are_silent(gitignore):
    assert check_env_files(_tc(["src/db.js"], {".gitignore": gitignore})) == []


def test_negated_pattern_does_not_count_as_ignored():
    assert check_env_files(_tc(["src/db.js"], {".gitignore": "!.env\n"}))[0]["rule_id"] == "env_not_gitignored"


def test_project_without_env_usage_is_silent():
    assert check_env_files(_tc(["a.js"], {}, app={"a.js": "console.log(1)"})) == []


def test_vendored_env_files_ignored():
    tc = _tc(["node_modules/pkg/.env"], {".gitignore": ".env\n"})
    assert check_env_files(tc) == []


# ── L7.2 ──────────────────────────────────────────────────────────────
def test_history_secret_fires_with_type_and_commit_only():
    hist = [{"commit": "abc1234", "path": "src/pay.js", "type": "stripe_live_key"}]
    [item] = check_secrets_in_history(_tc([], history=hist))
    assert item["rule_id"] == "secret_in_git_history" and item["severity"] == "critical"
    assert "abc1234" in item["evidence"]["detail"] and "live Stripe key" in item["title_plain"]
    assert item["id"].startswith("secrets::src/pay.js:abc1234")


def test_clean_history_is_silent():
    assert check_secrets_in_history(_tc([], history=[])) == []


def test_history_not_scanned_is_unverified_not_clear():
    res = run_checkup(_tc([".gitignore"], {".gitignore": ".env\n"}), include_scanners=False)
    assert "secrets_in_git_history" not in {i["rule_id"] for i in res["items"]}
    assert "secrets_in_history" in {u["check"] for u in res["unverified"]}


def test_patch_scan_reads_added_lines_only_and_skips_vendored():
    files = [
        {"filename": "src/pay.js", "patch": f'@@ -0,0 +1 @@\n+const k = "{STRIPE}";\n-const old = "{AWS}";'},
        {"filename": "node_modules/x/index.js", "patch": f"+{STRIPE}"},
        {"filename": "yarn.lock", "patch": f"+{STRIPE}"},
        {"filename": "README.md", "patch": "+use sk_live_xxxxxxxxxxxxxxxxxxxxxxxx in prod"},
    ]
    assert secrets_in_patch(files) == [("src/pay.js", "stripe_live_key")]


# ── detectors ─────────────────────────────────────────────────────────
def test_detectors_fire_on_real_shapes_and_not_on_placeholders():
    assert [k for k, _ in find_secrets(f"x={AWS}")] == ["aws_access_key"]
    real = "-----BEGIN RSA PRIVATE KEY-----\n" + "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7VJTUt9Us8cKj\n" * 3 + "-----END RSA PRIVATE KEY-----"
    assert [k for k, _ in find_secrets(real)] == ["private_key_block"]
    assert find_secrets("sk_live_xxxxxxxxxxxxxxxxxxxxxxxx AKIAIOSFODNN7EXAMPLE sk_test_" + "a1B2c3D4e5F6g7H8i9J0k1L2") == []


def test_secret_env_names_never_include_values():
    assert secret_env_names(f"A_TOKEN={STRIPE}\nB=plain\n") == ["A_TOKEN"]


PEM_BODY = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7VJTUt9Us8cKj\n" * 3


def test_private_key_needs_a_real_body_not_just_a_header():
    assert find_secrets("label = '-----BEGIN PRIVATE KEY-----'") == []
    assert find_secrets("const re = /-----BEGIN RSA PRIVATE KEY-----/;") == []
    full = f"-----BEGIN RSA PRIVATE KEY-----\n{PEM_BODY}-----END RSA PRIVATE KEY-----"
    assert [k for k, _ in find_secrets(full)] == ["private_key_block"]


def test_history_scan_skips_test_fixtures_and_typings():
    body = "".join(f"+{ln}\n" for ln in PEM_BODY.splitlines())
    key = f"+-----BEGIN PRIVATE KEY-----\n{body}+-----END PRIVATE KEY-----"
    files = [{"filename": p, "patch": key} for p in
             ("packages/db/test/server.key", "src/auth.test.ts", "lib/deno.d.ts", "e2e/setup.ts", "config/keys.example")]
    assert secrets_in_patch(files) == []
    assert secrets_in_patch([{"filename": "config/prod.key", "patch": key}]) == [("config/prod.key", "private_key_block")]
