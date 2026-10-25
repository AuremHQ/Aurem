"""
services/generation_rules_triggers.py — one-line trigger descriptions
per scanner rule id, used by services/generation_rules.py's condensed
manifest builder.

Extracted from services/generation_rules.py (2026-08-27, mechanical
split — no behaviour change) to keep that module under the platform's
file-size guard.
"""
from __future__ import annotations

# One-line trigger descriptions per rule id. Hand-written because the
# regex source is neither readable nor safe to embed in a prompt (a
# raw regex would encourage the model to try to defeat the pattern
# rather than internalise the intent).
#
# Rule ids MUST match those emitted by the scanners. If an id is
# missing here the manifest falls back to the scanner's own message.
TRIGGER_ONELINE: dict[str, str] = {
    # ── Vanguard secrets ──
    "generic_api_key":         "any assignment like `api_key='<value>'` with 8+ non-whitespace chars",
    "aws_access_key":          "literals matching AKIA/AGPA/AIDA/AROA + 16 uppercase alphanumerics",
    "aws_secret_key":          "`aws_secret_access_key='...'` with a 40-char literal",
    "password_assignment":     "`password='...'` / `passwd='...'` with 4+ chars",
    "token_assignment":        "`bearer/auth_token/access_token/refresh_token='...'` 16+ chars",
    "private_key":             "`-----BEGIN [RSA|DSA|EC|OPENSSH|PGP] PRIVATE KEY-----` in source",
    "github_token":            "literals matching `gh[pousr]_[36+ chars]`",
    "slack_token":             "literals matching `xox[bpors]-<digits>-<...>`",
    "generic_secret":          "`secret/signing_key/encryption_key='...'` 16+ chars",
    "db_connection_string":    "`postgres://user:pass@` / `mongodb://user:pass@` style URI in source",
    "stripe_live_key":         "literals `sk_live_ / pk_live_ / rk_live_[20+ chars]`",
    google_api_key`:          literals AIza[0-9A-Za-z_-]{35}`",
sendgrid_key`:            literals SG.<22 chars>.<43 chars>``,
openai_key`:              literals sk-<32+ alphanumerics> excluding sk-test-/sk-aurem-

      ── Vanguard dangerous code ──``
usage`:              any call eval(``,
usage`:              any call exec(``,
plus_secret`:           eval(`/exec(` co‑located with hardcoded api‑key/password/token/secret literal`,
subprocess_shell_true`:   subprocess.*(... shell=True ...)``,
system`:               any call system(``,
loads`:            any call pickle.load(`/pickle.loads(``,
unsafe_load`:        yaml.load(...) without explicit Loader=``,
no_verify`:      requests/httpx/urllib .* verify=False``
string_format`:       cursor.execute(f\"…{var}…\")— f-string SQL``
assignment`: `.innerHTML = ... JS/TSSS,
dangerously_set_html React dangerouslySetInnerHTML prop,

      Bug Hunt secrets tighter fingerprints on top Vanguard AWS access key ID literal starting AKIA+16 uppercase alphanumerics long-term temp token literals starting ASIA AGPA AIDA AROA AIPA ANPA ANVA STS GCP AIza[35 bordered non-key Stripe live secret sk_live_[24 publishable pk_live_[24 SendGrid API SG.<22>.<43 Slack bot xox[baprs]-<10..72 workspace GitHub PAT ghp_[20 OAuth gho_[20 App/server ghs_/ghu_[20 JWT signing jwt/JWT_SECRET='<6+ private RSA BEGIN … Azure storage AccountKey='<60 base64 Twilio SK<32 hex env var [UPPERCASE_NAME]='<16+

      Bug Hunt vulnerability patterns log4shell jndi occurrence ${jndi string,
with_request eval(...request/input argv body params query...)``
with_request exec(...request/input argv body params query...)``
loads_untrusted pickle.load/pickle.loads all treat