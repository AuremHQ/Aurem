"""
services/checkup_secret_patterns.py — what a leaked credential looks like.

Only well-structured formats are matched (a prefix plus a long random
body); a vague "looks like a password" rule would cry wolf, and a false
alarm costs more trust than a miss (C3).  Secret VALUES are never kept:
callers get the credential *type* and an offset, nothing else (S8).
"""
from __future__ import annotations

import re

# type -> (regex, plain label)
SECRET_PATTERNS: dict[str, tuple[re.Pattern, str]] = {
    "stripe_live_key": (re.compile(r"\b[sr]k_live_[0-9A-Za-z]{20,}"), "live Stripe key"),
    "aws_access_key": (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key"),
    "github_token": (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), "GitHub access token"),
    "slack_token": (re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{20,}"), "Slack token"),
    "anthropic_key": (re.compile(r"\bsk-ant-[A-Za-z0-9_-]{30,}"), "Anthropic API key"),
    "openai_key": (re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{40,}"), "OpenAI API key"),
    "google_api_key": (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "Google API key"),
    # header + real base64 body + footer: a bare "-----BEGIN PRIVATE KEY-----" in a form label,
    # doc or regex is not a key
    "private_key_block": (re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY(?: BLOCK)?-----"
                                     r"[A-Za-z0-9+/=\s\\n]{100,}-----END"), "private key"),
}

_FAKE_BODY = re.compile(r"example|your[_-]|placeholder|xxxx|0000|1234|test|dummy|sample|changeme", re.I)


def _looks_real(match_text: str) -> bool:
    """Reject documentation placeholders such as sk_live_xxxxxxxxxxxxxxxxxxxx."""
    if match_text.startswith("-----BEGIN"):
        return True                          # the body length is already enforced by the pattern
    return len(set(match_text)) >= 10 and not _FAKE_BODY.search(match_text)


def find_secrets(text: str) -> list[tuple[str, int]]:
    """[(type, offset)] — never the secret itself."""
    out = []
    for kind, (rx, _label) in SECRET_PATTERNS.items():
        for m in rx.finditer(text):
            if _looks_real(m.group(0)):
                out.append((kind, m.start()))
    return out


def label_of(kind: str) -> str:
    return SECRET_PATTERNS[kind][1] if kind in SECRET_PATTERNS else "secret"


# ── .env files ────────────────────────────────────────────────────────
_ENV_PATH = re.compile(r"(?:^|/)\.env(?:\.[A-Za-z0-9_-]+)?$")
_TEMPLATE_ENDS = (".example", ".sample", ".template", ".dist", ".defaults", ".tpl")
_ASSIGN = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")
_SECRET_NAME = re.compile(r"KEY|SECRET|TOKEN|PASSWORD|PASSWD|PRIVATE|CREDENTIAL|DSN|DATABASE_URL|MONGO_URL|MONGODB_URI", re.I)
_PUBLIC_NAME = re.compile(r"PUBLIC|ANON|PUBLISHABLE", re.I)
_PLACEHOLDER = re.compile(r"^(?:|<.*>|\$\{.*\}|your[_-].*|.*change[_-]?(?:me|this|it).*|.*replace.*|xxx+.*|todo|null|none|\*+|example.*)$", re.I)


def is_env_file(path: str) -> bool:
    return bool(_ENV_PATH.search(path)) and not path.lower().endswith(_TEMPLATE_ENDS)


def secret_env_names(text: str) -> list[str]:
    """Names (never values) of variables in an env file that hold a real
    secret.  Public/anon keys, empty values, placeholders and local-only
    URLs don't count."""
    names = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        m = _ASSIGN.match(line)
        if not m:
            continue
        name, value = m.group(1), m.group(2).strip("\"'")
        if _PLACEHOLDER.match(value) or "localhost" in value or "127.0.0.1" in value:
            continue
        if _PUBLIC_NAME.search(name):
            continue
        if _SECRET_NAME.search(name) or find_secrets(value):
            names.append(name)
    return names
