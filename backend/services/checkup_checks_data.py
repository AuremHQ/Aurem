"""
services/checkup_checks_data.py — Check Library v2, layer 3 (database and
storage): L3.3 money stored as a float, L3.5 uploaded files public by default.

Both prefer a miss over a false alarm (C3): a public bucket named
`avatars` is normal, so L3.5 only reports when the name or nearby code
says the files are private user content, and stays quiet-but-honest
("couldn't confirm") when it can't tell.
"""
from __future__ import annotations

import re

from services.checkup_common import _files, _item, _line_of

# ── L3.3 money as float ───────────────────────────────────────────────
_MONEY = re.compile(r"(?:^|_)(?:price|amount|total|subtotal|cost|balance|fees?|salary|revenue|"
                    r"payments?|charges?|taxes|tax|discount|refund|payout|wage|budget)(?:s)?(?:_|$)")
_NOT_MONEY = re.compile(r"percent|pct|rate|ratio|count|qty|quantity|lat|lng|latitude|longitude|score|"
                        r"weight|duration|margin")
_FLOAT_SQL = re.compile(r"^\s*[\"`]?(\w+)[\"`]?\s+(?:float\d?|double\s+precision|real)\b", re.I | re.M)
_FLOAT_ORM = re.compile(
    r"\b(\w+)\s*(?::[^=\n]+)?[:=]\s*(?:\{\s*type\s*:\s*)?"
    r"(?:models\.FloatField|(?:db\.)?(?:Column|mapped_column)\(\s*(?:db\.)?Float\b|"
    r"(?:sequelize\.|DataTypes\.)(?:FLOAT|DOUBLE|REAL)\b)")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _is_money_name(name: str) -> bool:
    snake = _CAMEL.sub("_", name).lower()
    return bool(_MONEY.search(snake)) and not _NOT_MONEY.search(snake)


def _float_money_columns(path: str, text: str) -> list[tuple[str, int]]:
    rx = _FLOAT_SQL if path.endswith(".sql") else _FLOAT_ORM if path.endswith((".py", ".js", ".ts")) else None
    if rx is None:
        return []
    return [(m.group(1), _line_of(text, m.start(1))) for m in rx.finditer(text)
            if _is_money_name(m.group(1))]


def check_money_as_float(tc: dict[str, str]) -> list[dict]:
    out = []
    for path, text in _files(tc):
        for name, line in _float_money_columns(path, text):
            out.append(_item(
                scanner="data_safety", rule_id="money_stored_as_float", severity="high",
                title="Prices or balances are stored in a way that drifts by cents",
                impact="Decimal-point storage rounds badly: totals, invoices and refunds can end up a few cents off, and you'd be charged for the disputes.",
                file=path, line=line,
                detail=f"`{name}` is a floating-point column; money should be whole cents or an exact decimal type",
                fix_hint="Store the amount as whole cents (integer) or an exact decimal; plan the data migration first."))
    return out


# ── L3.5 public uploads ───────────────────────────────────────────────
_PRIVATE_HINT = re.compile(r"invoice|document|contract|receipt|kyc|passport|licen[cs]e|resume|\bcv\b|report|"
                           r"statement|attachment|upload|private|medical|\bid[-_ ]?(?:card|proof)|user[-_]?files?|files?\b", re.I)
_PUBLIC_OK_HINT = re.compile(r"avatar|logo|favicon|banner|thumb|static|asset|marketing|public[-_ ]?(?:site|web|assets?)|"
                             r"icons?|images?|cdn", re.I)
_SQL_BUCKET = re.compile(r"insert\s+into\s+storage\.buckets\s*\(([^)]*)\)\s*values\s*\(([^)]*)\)", re.I | re.S)
_JS_BUCKET = re.compile(r"createBucket\(\s*[\"']([\w.-]+)[\"']\s*,\s*\{[^}]*public\s*:\s*true", re.S)
_PY_BUCKET = re.compile(r"create_bucket\(\s*[\"']([\w.-]+)[\"'][^)]*[\"']public[\"']\s*:\s*True", re.S)
_ACL_PUBLIC = re.compile(r"""\bACL\b\s*[:=]\s*["']public-read(?:-write)?["']|\bacl\s*=\s*["']public-read(?:-write)?["']|"""
                         r"""\b(?:make_public|makePublic)\s*\(""", re.I)
_SKIP_PATH = re.compile(r"(?:^|/)(?:scripts?|deploy|ci|\.github|docs?)/|(?:^|/)test", re.I)


def _sql_public_buckets(text: str) -> list[tuple[str, int]]:
    out = []
    for m in _SQL_BUCKET.finditer(text):
        cols = [c.strip().strip('"').lower() for c in m.group(1).split(",")]
        vals = [v.strip().strip("'\"") for v in m.group(2).split(",")]
        if "public" not in cols or len(cols) != len(vals):
            continue
        if vals[cols.index("public")].lower() == "true":
            name = vals[cols.index("name")] if "name" in cols else vals[0]
            out.append((name, _line_of(text, m.start())))
    return out


def _code_public_buckets(text: str) -> list[tuple[str, int]]:
    return [(m.group(1), _line_of(text, m.start())) for rx in (_JS_BUCKET, _PY_BUCKET)
            for m in rx.finditer(text)]


def _verdict(hint_text: str) -> str | None:
    """'private' = report, 'normal' = stay silent, None = can't tell."""
    if _PRIVATE_HINT.search(hint_text) and not _PUBLIC_OK_HINT.search(hint_text):
        return "private"
    return "normal" if _PUBLIC_OK_HINT.search(hint_text) else None


def _public_upload_item(path: str, line: int, label: str, verdict: str | None) -> dict:
    return _item(
        scanner="data_safety", rule_id="uploads_public_by_default", severity="critical",
        title="Files your users upload can be opened by anyone with the link",
        impact="Invoices, IDs or documents stored this way are readable by strangers, which can mean a data-breach report and lost customers.",
        file=path, line=line,
        detail=f"{label} is public",
        confidence="high" if verdict == "private" else "unverified",
        fix_hint="Make the storage private and hand out short-lived download links instead.")


def _s3_acl_hits(path: str, text: str) -> list[dict]:
    out = []
    for m in _ACL_PUBLIC.finditer(text):
        around = text[max(0, m.start() - 300): m.end() + 300] + " " + path
        verdict = "private" if "write" in m.group(0).lower() else _verdict(around)
        if verdict != "normal":
            out.append(_public_upload_item(path, _line_of(text, m.start()),
                                           "an uploaded file is saved with a public-read setting", verdict))
    return out


def check_public_uploads(tc: dict[str, str]) -> list[dict]:
    out = []
    for path, text in _files(tc):
        if _SKIP_PATH.search(path):
            continue
        buckets = _sql_public_buckets(text) if path.endswith(".sql") else _code_public_buckets(text)
        for name, line in buckets:
            verdict = _verdict(name)
            if verdict != "normal":
                out.append(_public_upload_item(path, line, f"storage bucket `{name}`", verdict))
        if path.endswith((".py", ".js", ".ts", ".jsx", ".tsx")):
            out.extend(_s3_acl_hits(path, text))
    return out


CHECKS = [
    ("safe", "money_as_float", check_money_as_float),
    ("safe", "public_uploads", check_public_uploads),
]
