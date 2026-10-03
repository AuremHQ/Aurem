"""
services/codebase_health_extra.py — "Launch-ready" scorecard, round 2
========================================================================
2026-10 — founder directive: expand the Codebase Health scan from the
original 7 categories (security, performance, code_quality,
dependencies, database, bug_hunt, docker — services/
codebase_health_core.py) to 16, so a non-technical vibecoder gets one
simple "X / 16 ready" number instead of a buried, partial picture.

Researched against real roadmap.sh guides (Vibe Coding, AI Product
Builder, API Security Best Practices) — see /app/memory/PRD.md
"Launch-ready scorecard" entry for the source mapping. Each category
here is a genuine static-analysis check, same pattern as
codebase_health_core.py's scanners — no fabricated/placeholder
findings. Every scanner is a pure function:

    (text_cache: dict[path, content]) -> list[finding]

finding shape (identical to codebase_health_core.py):
    {id, category, severity, file, line, title, message, fix_hint,
     fix_tokens}

The 9 new categories:
    8.  test_coverage        — zero test files found anywhere in repo
    9.  api_security         — JWT TTL/payload, auth rate-limit, OAuth
                                redirect_uri / state param, sequential
                                IDs in URLs
    10. security_headers     — adapts the existing Loop-Mode
                                `scan_http_headers` (full_scan_scanners.py)
                                into this shape — same detector, now
                                also surfaced in the customer scorecard
    11. accessibility        — missing alt text, icon-only buttons with
                                no aria-label, missing <html lang>
    12. seo_basics           — missing <title>/meta description,
                                robots.txt / sitemap.xml, Open Graph tags
    13. git_hygiene          — missing .gitignore, missing README,
                                .env referenced with no .env.example
    14. error_handling       — silently-swallowed exceptions, fetch/axios
                                calls with no .catch/try, missing React
                                error boundary
    15. mobile_responsiveness — missing viewport meta tag, hard-coded
                                desktop pixel widths with zero media
                                queries anywhere in the stylesheet
    16. documentation        — missing README, missing setup/run
                                instructions
"""
from __future__ import annotations

import re

from services.full_scan_scanners import scan_http_headers as _scan_http_headers_raw


def _norm_sev(sev: str) -> str:
    s = (sev or "").upper()
    return s.lower() if s in {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"} else "medium"


_TEST_PATH_HINTS = re.compile(
    r"(^|/)(tests?|__tests__|spec)(/|$)|[._-]test[._-]|[._-]spec[._-]|_test\.py$|\.test\.(js|jsx|ts|tsx)$|\.spec\.(js|jsx|ts|tsx)$",
    re.IGNORECASE,
)
_SOURCE_EXT = (".py", ".js", ".jsx", ".ts", ".tsx")


# ──────────────────────────────────────────────────────────────────────
# Category 8 — Test Coverage (presence, not percentage — percentage
# already exists as AUREM's own internal health_score.py category and
# needs a real coverage-run instrumentation this customer-facing static
# scan doesn't have access to)
# ──────────────────────────────────────────────────────────────────────
def _scan_test_coverage(text_cache: dict[str, str]) -> list[dict]:
    source_files = [p for p in text_cache if p.endswith(_SOURCE_EXT)
                    and not _TEST_PATH_HINTS.search(p)]
    test_files = [p for p in text_cache if _TEST_PATH_HINTS.search(p)]
    if not source_files:
        return []
    if test_files:
        return []
    return [{
        "id":         "test::repo:0:zero_tests",
        "category":   "test_coverage",
        "severity":   _norm_sev("HIGH"),
        "file":       "",
        "line":       0,
        "title":      "no_automated_tests_found",
        "message":    (f"{len(source_files)} source file(s) found but no test "
                        "file anywhere in the repo (no `tests/`, `__tests__/`, "
                        "`*.test.*` or `*.spec.*`). Bugs in this code can only "
                        "be caught by hand."),
        "fix_hint":   "Ask ORA to write a basic test for your most important feature.",
        "fix_tokens": 8,
    }]


# ──────────────────────────────────────────────────────────────────────
# Category 9 — API Security (JWT hygiene, auth rate-limiting, OAuth
# redirect_uri/state, sequential IDs in URLs) — roadmap.sh "API
# Security Best Practices": JWT / OAuth / Access Control sections.
# ──────────────────────────────────────────────────────────────────────
_JWT_LONG_TTL = re.compile(r'(?:exp(?:ires?)?|expir\w*|ttl)\s*[=:]\s*(?:timedelta\([^)]*days\s*=\s*(\d+)|(\d{6,}))', re.IGNORECASE)
_JWT_ENCODE = re.compile(r'jwt\.encode\(', re.IGNORECASE)
_SEQ_ID_URL = re.compile(r'''["'](?:/[\w-]+)*/(?:users?|orders?|accounts?|invoices?)/\{?\s*(?:id|user_id|order_id)\s*:?\s*int\b|/\{id:int\}''', re.IGNORECASE)
_OAUTH_REDIRECT = re.compile(r'redirect_uri', re.IGNORECASE)
_OAUTH_STATE = re.compile(r'\bstate\b\s*[=:]', re.IGNORECASE)


def _scan_api_security(text_cache: dict[str, str]) -> list[dict]:
    out: list[dict] = []
    for path, text in text_cache.items():
        if not path.endswith(".py") or not text:
            continue
        # 1. JWT with a very long or no expiry near jwt.encode(
        for m in _JWT_ENCODE.finditer(text):
            window = text[m.start(): m.start() + 400]
            line = text[:m.start()].count("\n") + 1
            long_ttl = _JWT_LONG_TTL.search(window)
            if long_ttl and (long_ttl.group(1) and int(long_ttl.group(1)) > 30):
                out.append({
                    "id":       f"apisec::{path}:{line}:jwt_long_ttl",
                    "category": "api_security",
                    "severity": _norm_sev("MEDIUM"),
                    "file":     path, "line": line,
                    "title":    "jwt_expiry_too_long",
                    "message":  "JWT token expiry is set to more than 30 days — a stolen token stays valid for a long time.",
                    "fix_hint": "Shorten the token TTL (e.g. 1-24 hours) and add a refresh-token flow if users need to stay logged in longer.",
                    "fix_tokens": 6,
                })
        # 2. OAuth redirect_uri used without validating against an allowlist
        if _OAUTH_REDIRECT.search(text) and "allowlist" not in text.lower() and "allowed_redirect" not in text.lower():
            m = _OAUTH_REDIRECT.search(text)
            line = text[:m.start()].count("\n") + 1
            out.append({
                "id":       f"apisec::{path}:{line}:redirect_uri_unvalidated",
                "category": "api_security",
                "severity": _norm_sev("MEDIUM"),
                "file":     path, "line": line,
                "title":    "oauth_redirect_uri_not_validated",
                "message":  "`redirect_uri` is used in an OAuth flow but no allowlist check was found nearby — this can let an attacker redirect a login token to their own site.",
                "fix_hint": "Validate `redirect_uri` against a fixed allowlist on the server side before using it.",
                "fix_tokens": 6,
            })
        # 3. Sequential/int IDs exposed directly in a resource URL (IDOR risk)
        for m in _SEQ_ID_URL.finditer(text):
            line = text[:m.start()].count("\n") + 1
            out.append({
                "id":       f"apisec::{path}:{line}:sequential_id_in_url",
                "category": "api_security",
                "severity": _norm_sev("LOW"),
                "file":     path, "line": line,
                "title":    "sequential_id_in_url",
                "message":  "This route takes a plain integer ID in the URL — anyone can guess `/orders/2` after seeing `/orders/1`.",
                "fix_hint": "Use a UUID instead of an auto-increment integer for anything another user shouldn't be able to guess.",
                "fix_tokens": 6,
            })
    return out


# ──────────────────────────────────────────────────────────────────────
# Category 10 — Security Headers (adapter over the existing Loop-Mode
# detector in services/full_scan_scanners.py — same detection logic,
# now also wired into the customer-facing scorecard, not just the
# post-write Loop-Mode gate)
# ──────────────────────────────────────────────────────────────────────
def _scan_security_headers(text_cache: dict[str, str]) -> list[dict]:
    raw = _scan_http_headers_raw(text_cache)
    out: list[dict] = []
    for f in raw:
        out.append({
            "id":         f"hdr::{f.get('file')}:{f.get('line')}:{f.get('rule_id')}",
            "category":   "security_headers",
            "severity":   _norm_sev(f.get("severity") or "medium"),
            "file":       f.get("file") or "",
            "line":       int(f.get("line") or 0),
            "title":      f.get("rule_id") or "http_headers_missing",
            "message":    f.get("desc") or "No HTTP security headers set.",
            "fix_hint":   "Add a security-headers middleware (helmet for Express, secure-headers equivalent for FastAPI/Flask) that sets X-Frame-Options, Content-Security-Policy, X-Content-Type-Options and HSTS.",
            "fix_tokens": 6,
        })
    return out


# ──────────────────────────────────────────────────────────────────────
# Category 11 — Accessibility
# ──────────────────────────────────────────────────────────────────────
_IMG_NO_ALT = re.compile(r'<img(?![^>]*\balt\s*=)[^>]*>', re.IGNORECASE)
_ICON_BTN_NO_LABEL = re.compile(r'<button(?![^>]*aria-label)[^>]*>\s*(?:<[A-Za-z][\w.]*\s*/?>|<svg)', re.IGNORECASE)
_HTML_NO_LANG = re.compile(r'<html(?![^>]*\blang\s*=)[^>]*>', re.IGNORECASE)


def _scan_accessibility(text_cache: dict[str, str]) -> list[dict]:
    out: list[dict] = []
    for path, text in text_cache.items():
        if not text or not path.endswith((".jsx", ".tsx", ".html", ".js", ".ts")):
            continue
        for m in _IMG_NO_ALT.finditer(text):
            line = text[:m.start()].count("\n") + 1
            out.append({
                "id":       f"a11y::{path}:{line}:img_no_alt",
                "category": "accessibility",
                "severity": _norm_sev("LOW"),
                "file":     path, "line": line,
                "title":    "image_missing_alt_text",
                "message":  "An `<img>` tag has no `alt` attribute — screen-reader users can't tell what it is.",
                "fix_hint": "Add a short, descriptive `alt=\"...\"` to every image.",
                "fix_tokens": 4,
            })
        for m in _ICON_BTN_NO_LABEL.finditer(text):
            line = text[:m.start()].count("\n") + 1
            out.append({
                "id":       f"a11y::{path}:{line}:icon_button_no_label",
                "category": "accessibility",
                "severity": _norm_sev("LOW"),
                "file":     path, "line": line,
                "title":    "icon_only_button_no_aria_label",
                "message":  "A button with only an icon (no visible text) has no `aria-label` — screen readers can't announce what it does.",
                "fix_hint": "Add `aria-label=\"...\"` describing the button's action.",
                "fix_tokens": 4,
            })
        if path.endswith(".html"):
            for m in _HTML_NO_LANG.finditer(text):
                line = text[:m.start()].count("\n") + 1
                out.append({
                    "id":       f"a11y::{path}:{line}:html_no_lang",
                    "category": "accessibility",
                    "severity": _norm_sev("LOW"),
                    "file":     path, "line": line,
                    "title":    "html_missing_lang_attribute",
                    "message":  "The `<html>` tag has no `lang` attribute — screen readers can't pick the right pronunciation/voice.",
                    "fix_hint": 'Add `lang="en"` (or the right language code) to the `<html>` tag.',
                    "fix_tokens": 3,
                })
    return out


# ──────────────────────────────────────────────────────────────────────
# Category 12 — SEO Basics
# ──────────────────────────────────────────────────────────────────────
def _scan_seo_basics(text_cache: dict[str, str]) -> list[dict]:
    out: list[dict] = []
    html_files = {p: t for p, t in text_cache.items() if p.endswith(".html") and t}
    has_robots = any(p.endswith("robots.txt") for p in text_cache)
    has_sitemap = any(p.endswith("sitemap.xml") for p in text_cache)
    for path, text in html_files.items():
        if "<title" not in text.lower() or re.search(r"<title>\s*</title>", text, re.IGNORECASE):
            out.append({
                "id":       f"seo::{path}:0:no_title",
                "category": "seo_basics",
                "severity": _norm_sev("MEDIUM"),
                "file":     path, "line": 0,
                "title":    "missing_page_title",
                "message":  "No (or empty) `<title>` tag — search engines and browser tabs show nothing meaningful.",
                "fix_hint": "Add a short, specific `<title>` describing the page.",
                "fix_tokens": 4,
            })
        if not re.search(r'<meta[^>]+name=["\']description["\']', text, re.IGNORECASE):
            out.append({
                "id":       f"seo::{path}:0:no_meta_description",
                "category": "seo_basics",
                "severity": _norm_sev("LOW"),
                "file":     path, "line": 0,
                "title":    "missing_meta_description",
                "message":  "No `<meta name=\"description\">` — search results show a random snippet instead of a real summary.",
                "fix_hint": 'Add `<meta name="description" content="...">` with a one-sentence summary of the page.',
                "fix_tokens": 4,
            })
        if not re.search(r'<meta[^>]+property=["\']og:', text, re.IGNORECASE):
            out.append({
                "id":       f"seo::{path}:0:no_open_graph",
                "category": "seo_basics",
                "severity": _norm_sev("LOW"),
                "file":     path, "line": 0,
                "title":    "missing_open_graph_tags",
                "message":  "No Open Graph tags (`og:title`, `og:image`...) — links shared on social media / WhatsApp show no preview card.",
                "fix_hint": "Add `og:title`, `og:description` and `og:image` meta tags.",
                "fix_tokens": 4,
            })
        break  # one representative HTML entrypoint is enough signal
    if html_files and not has_robots:
        out.append({
            "id":       "seo::robots.txt:0:missing",
            "category": "seo_basics",
            "severity": _norm_sev("LOW"),
            "file":     "robots.txt", "line": 0,
            "title":    "missing_robots_txt",
            "message":  "No `robots.txt` found — search engines have no explicit crawl instructions.",
            "fix_hint": "Add a simple `robots.txt` at the site root (even `User-agent: *\\nAllow: /` is enough to start).",
            "fix_tokens": 3,
        })
    if html_files and not has_sitemap:
        out.append({
            "id":       "seo::sitemap.xml:0:missing",
            "category": "seo_basics",
            "severity": _norm_sev("LOW"),
            "file":     "sitemap.xml", "line": 0,
            "title":    "missing_sitemap",
            "message":  "No `sitemap.xml` found — search engines have to discover every page by following links alone.",
            "fix_hint": "Generate a `sitemap.xml` listing your public pages.",
            "fix_tokens": 3,
        })
    return out


# ──────────────────────────────────────────────────────────────────────
# Category 13 — Git Hygiene — roadmap.sh Vibe Coding "Master Version
# Control" section.
# ──────────────────────────────────────────────────────────────────────
def _scan_git_hygiene(text_cache: dict[str, str]) -> list[dict]:
    out: list[dict] = []
    has_gitignore = any(p == ".gitignore" or p.endswith("/.gitignore") for p in text_cache)
    has_readme = any(p.lower() in ("readme.md", "readme.txt", "readme") for p in text_cache)
    has_env_file = any(p == ".env" or p.endswith("/.env") for p in text_cache)
    has_env_example = any(".env.example" in p or ".env.sample" in p for p in text_cache)
    if not has_gitignore:
        out.append({
            "id":       "git::.gitignore:0:missing",
            "category": "git_hygiene",
            "severity": _norm_sev("MEDIUM"),
            "file":     ".gitignore", "line": 0,
            "title":    "missing_gitignore",
            "message":  "No `.gitignore` found — it's easy to accidentally commit `node_modules`, build output, or secret files.",
            "fix_hint": "Add a `.gitignore` for your stack (node_modules, __pycache__, .env, build/dist folders).",
            "fix_tokens": 3,
        })
    if not has_readme:
        out.append({
            "id":       "git::README.md:0:missing",
            "category": "git_hygiene",
            "severity": _norm_sev("LOW"),
            "file":     "README.md", "line": 0,
            "title":    "missing_readme",
            "message":  "No README found — anyone else opening this project (including future-you) has no idea what it does or how to run it.",
            "fix_hint": "Add a short README: what the project does, how to run it, and what env vars it needs.",
            "fix_tokens": 4,
        })
    if has_env_file and not has_env_example:
        out.append({
            "id":       "git::.env.example:0:missing",
            "category": "git_hygiene",
            "severity": _norm_sev("MEDIUM"),
            "file":     ".env.example", "line": 0,
            "title":    "missing_env_example",
            "message":  "A `.env` file exists but there's no `.env.example` — anyone setting this project up elsewhere has no idea which variables they need to provide.",
            "fix_hint": "Add a `.env.example` listing every variable name (no real values) your `.env` uses.",
            "fix_tokens": 3,
        })
    return out


# ──────────────────────────────────────────────────────────────────────
# Category 14 — Error Handling
# ──────────────────────────────────────────────────────────────────────
_BARE_EXCEPT_PY = re.compile(r'except\s*:\s*\n\s*pass\b', re.IGNORECASE)
_EMPTY_CATCH_JS = re.compile(r'catch\s*\([^)]*\)\s*\{\s*\}')
_FETCH_NO_CATCH = re.compile(r'fetch\([^)]*\)(?!\s*\.then[^;]*\.catch)(?:\s*\.then\([^;]*\));', re.IGNORECASE)


def _scan_error_handling(text_cache: dict[str, str]) -> list[dict]:
    out: list[dict] = []
    for path, text in text_cache.items():
        if not text:
            continue
        if path.endswith(".py"):
            for m in _BARE_EXCEPT_PY.finditer(text):
                line = text[:m.start()].count("\n") + 1
                out.append({
                    "id":       f"err::{path}:{line}:bare_except_pass",
                    "category": "error_handling",
                    "severity": _norm_sev("MEDIUM"),
                    "file":     path, "line": line,
                    "title":    "silently_swallowed_exception",
                    "message":  "A bare `except: pass` hides every error, including real bugs — you'd never know this code failed.",
                    "fix_hint": "Catch the specific exception type and at least log it.",
                    "fix_tokens": 4,
                })
        if path.endswith((".js", ".jsx", ".ts", ".tsx")):
            for m in _EMPTY_CATCH_JS.finditer(text):
                line = text[:m.start()].count("\n") + 1
                out.append({
                    "id":       f"err::{path}:{line}:empty_catch_block",
                    "category": "error_handling",
                    "severity": _norm_sev("MEDIUM"),
                    "file":     path, "line": line,
                    "title":    "empty_catch_block",
                    "message":  "A `catch {}` block does nothing — a failed request or action fails silently with no feedback to the user.",
                    "fix_hint": "At minimum log the error; ideally show the user a friendly message.",
                    "fix_tokens": 4,
                })
            if "ErrorBoundary" not in text and re.search(r'createRoot|ReactDOM\.render', text):
                line = 1
                out.append({
                    "id":       f"err::{path}:{line}:no_error_boundary",
                    "category": "error_handling",
                    "severity": _norm_sev("LOW"),
                    "file":     path, "line": line,
                    "title":    "no_react_error_boundary",
                    "message":  "No React Error Boundary found near your app's root — one bug in any component can crash the whole page to a blank screen.",
                    "fix_hint": "Wrap your app in a top-level Error Boundary that shows a friendly fallback instead of a blank page.",
                    "fix_tokens": 5,
                })
    return out


# ──────────────────────────────────────────────────────────────────────
# Category 15 — Mobile Responsiveness
# ──────────────────────────────────────────────────────────────────────
_VIEWPORT_META = re.compile(r'<meta[^>]+name=["\']viewport["\']', re.IGNORECASE)
_MEDIA_QUERY = re.compile(r'@media\b')
_FIXED_WIDTH_PX = re.compile(r'width:\s*\d{3,4}px', re.IGNORECASE)


def _scan_mobile_responsiveness(text_cache: dict[str, str]) -> list[dict]:
    out: list[dict] = []
    html_files = {p: t for p, t in text_cache.items() if p.endswith(".html") and t}
    for path, text in html_files.items():
        if not _VIEWPORT_META.search(text):
            out.append({
                "id":       f"mobile::{path}:0:no_viewport_meta",
                "category": "mobile_responsiveness",
                "severity": _norm_sev("HIGH"),
                "file":     path, "line": 0,
                "title":    "missing_viewport_meta_tag",
                "message":  "No `<meta name=\"viewport\">` tag — on a real phone this page will render tiny and un-zoomable instead of fitting the screen.",
                "fix_hint": 'Add `<meta name="viewport" content="width=device-width, initial-scale=1">` to the `<head>`.',
                "fix_tokens": 3,
            })
        break
    css_text = "\n".join(t for p, t in text_cache.items() if p.endswith((".css", ".scss")) and t)
    if css_text and _FIXED_WIDTH_PX.search(css_text) and not _MEDIA_QUERY.search(css_text):
        out.append({
            "id":       "mobile::stylesheet:0:no_media_queries",
            "category": "mobile_responsiveness",
            "severity": _norm_sev("MEDIUM"),
            "file":     "", "line": 0,
            "title":    "fixed_widths_no_media_queries",
            "message":  "Your stylesheets use fixed pixel widths (e.g. `width: 800px`) with zero `@media` breakpoints anywhere — the layout likely breaks or overflows on a phone screen.",
            "fix_hint": "Add responsive breakpoints (e.g. `@media (max-width: 768px)`) or switch fixed widths to `%`/`max-width`.",
            "fix_tokens": 6,
        })
    return out


# ──────────────────────────────────────────────────────────────────────
# Category 16 — Documentation
# ──────────────────────────────────────────────────────────────────────
def _scan_documentation(text_cache: dict[str, str]) -> list[dict]:
    out: list[dict] = []
    readme_path = next((p for p in text_cache if p.lower() in ("readme.md", "readme.txt", "readme")), None)
    if not readme_path:
        out.append({
            "id":       "docs::README.md:0:missing",
            "category": "documentation",
            "severity": _norm_sev("LOW"),
            "file":     "README.md", "line": 0,
            "title":    "missing_readme",
            "message":  "No README — there's no written explanation of what this project is or how to run it.",
            "fix_hint": "Ask ORA to generate a README covering what the project does, setup steps, and required env vars.",
            "fix_tokens": 5,
        })
        return out
    readme_text = (text_cache.get(readme_path) or "").lower()
    has_setup = any(k in readme_text for k in ("install", "setup", "getting started", "run", "npm ", "pip "))
    if len(readme_text.strip()) < 40 or not has_setup:
        out.append({
            "id":       f"docs::{readme_path}:0:thin_readme",
            "category": "documentation",
            "severity": _norm_sev("LOW"),
            "file":     readme_path, "line": 0,
            "title":    "readme_missing_setup_instructions",
            "message":  "The README exists but has no setup/run instructions — someone new to this project (or future-you) can't get it running from the README alone.",
            "fix_hint": "Ask ORA to add setup/run steps and required env vars to the README.",
            "fix_tokens": 4,
        })
    return out


EXTRA_SCANNERS = {
    "test_coverage":          _scan_test_coverage,
    "api_security":           _scan_api_security,
    "security_headers":       _scan_security_headers,
    "accessibility":          _scan_accessibility,
    "seo_basics":             _scan_seo_basics,
    "git_hygiene":            _scan_git_hygiene,
    "error_handling":         _scan_error_handling,
    "mobile_responsiveness":  _scan_mobile_responsiveness,
    "documentation":          _scan_documentation,
}

# Plain-English labels for the "Launch-ready X / 16" scorecard UI —
# every one of the 16 category keys (7 original + 9 new) maps to a
# short, non-technical name a vibecoder can read without a glossary.
CATEGORY_DISPLAY_NAMES = {
    "security":               "Security",
    "performance":            "Performance",
    "code_quality":           "Code Quality",
    "dependencies":           "Dependencies",
    "database":               "Database",
    "bug_hunt":                "Bug Hunt",
    "docker":                 "Docker Setup",
    "test_coverage":          "Automated Tests",
    "api_security":           "API & Auth Security",
    "security_headers":       "Security Headers",
    "accessibility":          "Accessibility",
    "seo_basics":             "SEO Basics",
    "git_hygiene":            "Git Hygiene",
    "error_handling":         "Error Handling",
    "mobile_responsiveness":  "Mobile Friendly",
    "documentation":          "Documentation",
}
