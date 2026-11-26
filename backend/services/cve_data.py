"""
services/cve_data.py — SINGLE canonical source of truth for the static
(fallback) CVE data used by the Codebase Health scanners.

PROMPT 3 item 4 (2026-11-26): the same CVE facts used to live in TWO
places that drifted apart — ``_CVE_DB`` in
``services/codebase_health_core.py`` (7 packages, vulnerable-version
*prefix* match) and ``_DEP_CVES`` in ``services/bug_hunt_rules.py``
(11 entries, first-*fixed*-version range match). Both files now import
their view from here; neither carries its own map anymore.

One entry per (package, CVE). Each entry records BOTH query shapes
because the two consumers genuinely need different ones:

  * ``fixed_in`` — first version NOT vulnerable (exclusive upper
    bound). Bug Hunt flags a declared version iff ``ver < fixed_in``
    (see ``bug_hunt_rules._vercmp``). ``None`` means no fixed version
    was ever recorded for that CVE — the entry then has no range form
    and is left OUT of the Bug Hunt view (it still appears in the
    health view via ``vulnerable_prefixes``).
  * ``vulnerable_prefixes`` — vulnerable release lines, for the
    prefix-matching health scanner (``_scan_dependencies`` flags iff
    the declared version startswith one of these). Empty means the
    entry has no prefix form (range-only entry).

The two derived views below are pure projections of ``CVE_ENTRIES`` —
no CVE id, severity, or message is written twice. Backwards-compatible
aliases: ``codebase_health_core._CVE_DB`` and
``bug_hunt_rules._DEP_CVES`` are the same objects imported from here
(existing tests and ``services/generation_rules.py`` import those
names).

PRECEDENCE — live OSV is PRIMARY. The static map in this file is a
FALLBACK only: ``_scan_dependencies`` uses it solely for a package
with no live OSV cache entry (live lookup timed out, failed, or
returned nothing for that package). Whenever live OSV data exists for
a package — including a live-confirmed-clean empty list — the live
result wins and the static map is not consulted. See
``codebase_health_core.resolve_live_cves``.

REFRESH (2026-11-26, verified against on-machine sources only):
  * Repo pins: backend/requirements.txt (requests==2.34.2,
    fastapi==0.115.0, PyJWT==2.13.0, pillow==12.3.0, urllib3==2.7.0,
    Jinja2==3.1.6, PyYAML==6.0.3, cryptography==50.0.0);
    frontend/package.json (axios ^1.18.0, vite ^6.4.3).
  * Installed (leak venv): requests 2.34.2, fastapi 0.165.0,
    PyJWT 2.15.1, pillow 13.0.0, urllib3 2.8.0, PyYAML 6.0.4,
    Jinja2 3.1.6, cryptography 50.0.2. flask/django are NOT installed
    and NOT in requirements.txt — their entries could not be verified
    on-machine and are left exactly as they were.
  * urllib3 CVE-2023-43804: fixed_in corrected 2.0.0 -> 2.0.7.
    The old boundary under-flagged: 2.0.0-2.0.6 are still vulnerable
    to CVE-2023-43804 (fixed in the 2.0.7 release; release exists —
    verified via ``pip index versions urllib3`` on this machine;
    installed 2.8.0 / repo pin 2.7.0 are both above it).
  * requests CVE-2023-32681: health prefixes extended "2.28." ->
    "2.28."/"2.29."/"2.30.". The CVE is fixed in 2.31.0 (already
    recorded in the old _DEP_CVES entry), so every 2.x line below
    2.31.0 is vulnerable — the old single prefix missed 2.29.x/2.30.x.
  * Everything else left untouched: each remaining boundary is a
    correct historical fact (fixed_in is the known fix release for
    its CVE; prefixes name genuinely vulnerable lines). The
    "outdated" entries (fastapi 0.95.x, vite 4.x) have no CVE/fix
    version behind them, so no new threshold was invented for them.
"""
from __future__ import annotations

# One entry per (package, CVE). Keys:
#   package: lowercase package name (PyPI or npm)
#   cve: CVE id, "multi-cves" (several CVEs share one boundary), or
#        "outdated" (no specific CVE — the line is just ancient)
#   severity: CRITICAL | HIGH | MEDIUM | LOW
#   fixed_in: first NOT-vulnerable version, or None if no fixed
#        version was ever recorded (entry is prefix-only)
#   vulnerable_prefixes: vulnerable release lines for the
#        prefix-matching health scanner (may be empty)
#   message: human-readable description (Bug Hunt finding text)
CVE_ENTRIES: list[dict] = [
    {
        "package": "requests",
        "cve": "CVE-2023-32681",
        "severity": "HIGH",
        "fixed_in": "2.31.0",
        "vulnerable_prefixes": ["2.28.", "2.29.", "2.30."],
        "message": "requests < 2.31.0 — Proxy-Authorization leak via cross-origin redirect.",
    },
    {
        "package": "flask",
        "cve": "multi-cves",
        "severity": "HIGH",
        "fixed_in": "2.3.0",
        "vulnerable_prefixes": [],
        "message": "flask < 2.3.0 — session-cookie + open-redirect CVEs.",
    },
    {
        "package": "django",
        "cve": "multi-cves",
        "severity": "CRITICAL",
        "fixed_in": "4.2.0",
        "vulnerable_prefixes": [],
        "message": "django < 4.2.0 — SQL injection + DoS CVEs.",
    },
    {
        "package": "pillow",
        "cve": "CVE-2023-44271",
        "severity": "HIGH",
        "fixed_in": "10.0.0",
        "vulnerable_prefixes": [],
        "message": "pillow < 10.0.0 — buffer overflow in JPEG/PNG parsing.",
    },
    {
        "package": "cryptography",
        "cve": "multi-cves",
        "severity": "HIGH",
        "fixed_in": "41.0.0",
        "vulnerable_prefixes": [],
        "message": "cryptography < 41.0.0 — multiple openssl-bound vulns.",
    },
    {
        # REFRESHED 2026-11-26: fixed_in was "2.0.0"; CVE-2023-43804
        # is fixed in 2.0.7, so 2.0.0-2.0.6 were wrongly unflagged.
        "package": "urllib3",
        "cve": "CVE-2023-43804",
        "severity": "MEDIUM",
        "fixed_in": "2.0.7",
        "vulnerable_prefixes": [],
        "message": "urllib3 < 2.0.7 — cookie & auth header leak on cross-origin redirect.",
    },
    {
        "package": "pyyaml",
        "cve": "CVE-2020-14343",
        "severity": "HIGH",
        "fixed_in": "6.0",
        "vulnerable_prefixes": [],
        "message": "pyyaml < 6.0 — yaml.load() arbitrary code execution.",
    },
    {
        "package": "jinja2",
        "cve": "CVE-2024-22195",
        "severity": "MEDIUM",
        "fixed_in": "3.1.3",
        "vulnerable_prefixes": [],
        "message": "jinja2 < 3.1.3 — xmlattr filter XSS.",
    },
    {
        "package": "axios",
        "cve": "CVE-2023-45857",
        "severity": "HIGH",
        "fixed_in": "1.6.0",
        "vulnerable_prefixes": [],
        "message": "axios < 1.6.0 — CSRF token leak across redirects.",
    },
    {
        "package": "lodash",
        "cve": "CVE-2021-23337",
        "severity": "HIGH",
        "fixed_in": "4.17.21",
        "vulnerable_prefixes": ["4.17.20"],
        "message": "lodash < 4.17.21 — prototype pollution via _.template.",
    },
    {
        "package": "next",
        "cve": "CVE-2024-46982",
        "severity": "CRITICAL",
        "fixed_in": "14.2.10",
        "vulnerable_prefixes": ["13.4."],
        "message": "next < 14.2.10 — cache poisoning + SSRF chain.",
    },
    # ── Prefix-only entries (no fixed version ever recorded) ──────────
    # These existed only in the old _CVE_DB. They have no range form,
    # so they do NOT appear in DEP_CVES — Bug Hunt never covered them
    # and adding a fixed_in here would mean inventing a fix release.
    {
        "package": "fastapi",
        "cve": "outdated",
        "severity": "MEDIUM",
        "fixed_in": None,
        "vulnerable_prefixes": ["0.95."],
        "message": "fastapi 0.95.x is outdated — upgrade to a current release.",
    },
    {
        "package": "pyjwt",
        "cve": "CVE-2022-29217",
        "severity": "CRITICAL",
        "fixed_in": None,
        "vulnerable_prefixes": ["1."],
        "message": "pyjwt 1.x — key-confusion vulnerability (CVE-2022-29217); upgrade to pyjwt 2.x.",
    },
    {
        "package": "axios",
        "cve": "CVE-2021-3749",
        "severity": "HIGH",
        "fixed_in": None,
        "vulnerable_prefixes": ["0.21."],
        "message": "axios 0.21.x — CVE-2021-3749; upgrade to a current 1.x release.",
    },
    {
        "package": "vite",
        "cve": "outdated",
        "severity": "LOW",
        "fixed_in": None,
        "vulnerable_prefixes": ["4."],
        "message": "vite 4.x is outdated — upgrade to a current release.",
    },
]


def build_dep_cves() -> list[tuple[str, str, str, str, str]]:
    """Bug Hunt view: (package, first_fixed_version, cve, severity,
    message). Only entries with a recorded ``fixed_in`` have a range
    form. Order matches the old ``_DEP_CVES`` list."""
    return [
        (e["package"], e["fixed_in"], e["cve"], e["severity"], e["message"])
        for e in CVE_ENTRIES
        if e["fixed_in"] is not None
    ]


def build_cve_db() -> dict[str, list[tuple[str, str, str]]]:
    """Health view: {package: [(vulnerable_prefix, cve, severity)]}.
    Only entries with ``vulnerable_prefixes`` appear."""
    db: dict[str, list[tuple[str, str, str]]] = {}
    for e in CVE_ENTRIES:
        for prefix in e["vulnerable_prefixes"]:
            db.setdefault(e["package"], []).append(
                (prefix, e["cve"], e["severity"])
            )
    return db


# Derived views — built once at import. Both consumers import these
# (as _DEP_CVES / _CVE_DB) so there is exactly one copy of the data.
DEP_CVES: list[tuple[str, str, str, str, str]] = build_dep_cves()
CVE_DB: dict[str, list[tuple[str, str, str]]] = build_cve_db()
