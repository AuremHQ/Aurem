"""Check Library v2 / Group 7 (L10.4, L10.6): fires on bad fixtures, silent on clean."""
from __future__ import annotations

import pytest

from services.checkup_checks_logs_cookies import check_insecure_cookies, check_sensitive_logs


# ── L10.4 ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("path,code", [
    ("api/login.js", "app.post('/login', (req, res) => {\n  console.log(password);\n});\n"),
    ("api/login.js", "console.log('login attempt', { email, password });\n"),
    ("api/login.js", "console.error(`bad login ${user.password}`);\n"),
    ("api/auth.ts", "logger.info('token issued', accessToken);\n"),
    ("api/auth.js", "console.log(req.headers);\n"),
    ("app/auth.py", "logger.info(f'login for {email} with {password}')\n"),
    ("app/auth.py", "print(api_key)\n"),
    ("app/auth.py", "logging.debug('auth header %s', authorization)\n"),
])
def test_sensitive_log_fires(path, code):
    [item] = check_sensitive_logs({path: code})
    assert item["rule_id"] == "sensitive_data_in_logs" and item["severity"] == "high"
    assert item["evidence"]["line"] >= 1


@pytest.mark.parametrize("path,code", [
    ("api/a.js", "console.log('password reset email sent to', email);\n"),
    ("api/a.js", "console.log(`Invalid password for ${email}`);\n"),
    ("api/a.js", "console.error(passwordError, passwordHash.length, passwordStrength);\n"),
    ("api/a.js", "console.log('next page', nextPageToken, csrfToken);\n"),
    ("api/a.js", "console.log(req.headers['user-agent']);\n"),
    ("api/a.js", "console.log('server started on', port);\n"),
    ("app/a.py", "logger.info('token refreshed for user %s', user_id)\n"),
    ("app/a.py", "print('missing api_key in config')\n"),
    ("tests/a.test.js", "console.log(password);\n"),
    ("node_modules/x/a.js", "console.log(password);\n"),
    ("app/a.py", "print(f\"found user has_password={bool(user.get('password'))}\")\n"),
    ("app/a.py", "logger.warning('missing args token=%s', bool(gh_token))\n"),
    ("app/a.py", "logger.info('password length %s', len(password))\n"),
    ("scripts/seed.py", "print(f'AUTH_TOKEN={token}')\n"),
    ("qa/seed.py", "print(f'AUTH_TOKEN={token}')\n"),
])
def test_sensitive_log_silent_on_clean(path, code):
    assert check_sensitive_logs({path: code}) == []


def test_log_wrappers_that_hide_the_value_are_still_flagged_when_value_leaks_elsewhere():
    assert check_sensitive_logs({"app/a.py": "print(bool(token), token)\n"})


# ── L10.6 ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("path,code,fragment", [
    ("server/a.js", "res.cookie('session', id);\n", "httponly"),
    ("server/a.js", "res.cookie('token', t, { maxAge: 1000, httpOnly: true });\n", "secure"),
    ("server/a.js", "const s = session({ secret: 'x', resave: false });\n", "secure"),
    ("src/a.js", "document.cookie = 'auth_token=' + t;\n", "HttpOnly"),
    ("app/a.py", "response.set_cookie('session_id', sid)\n", "httponly"),
    ("settings.py", "SESSION_COOKIE_SECURE = False\n", "SESSION_COOKIE_SECURE"),
])
def test_insecure_cookie_fires(path, code, fragment):
    [item] = check_insecure_cookies({path: code})
    assert item["rule_id"] == "insecure_session_cookie" and item["severity"] == "high"
    assert fragment in item["evidence"]["detail"]


@pytest.mark.parametrize("path,code", [
    ("server/a.js", "res.cookie('session', id, { httpOnly: true, secure: true, sameSite: 'lax' });\n"),
    ("server/a.js", "res.cookie('theme', 'dark');\nres.cookie('lang', 'en');\n"),
    ("server/a.js", "const s = session({ secret: 'x', cookie: { secure: process.env.NODE_ENV === 'production', httpOnly: true } });\n"),
    ("src/a.js", "document.cookie = 'theme=dark';\n"),
    ("app/a.py", "response.set_cookie('session_id', sid, httponly=True, secure=True, samesite='lax')\n"),
    ("settings.py", "SESSION_COOKIE_SECURE = True\nSESSION_COOKIE_HTTPONLY = True\n"),
    ("tests/a.test.js", "res.cookie('session', id);\n"),
    ("server/a.js", "res.cookie('session', mint(u), cookieOpts(ttl));\n"),
    ("app/a.py", "response.set_cookie('access_token', mint(u), **_cookie_kwargs(ttl))\n"),
    ("server/a.js", "res.cookie('session', mint(u), buildOptions);\n"),
])
def test_insecure_cookie_silent_on_clean(path, code):
    assert check_insecure_cookies({path: code}) == []
