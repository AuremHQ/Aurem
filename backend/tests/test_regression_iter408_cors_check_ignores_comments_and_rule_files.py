"""Own-repo audit: `backend/main.py` was reported for open CORS with credentials because a
*comment* said `allow_origins=["*"]` and `allow_credentials=True` appears 35 lines later in a
locked-down config.  Rule-definition files and tests (which quote the pattern on purpose) were
reported too.  A real wildcard + credentials must still fire."""
from __future__ import annotations

from services.checkup_checks import check_cors_open_with_credentials

REAL_PY = ('app.add_middleware(\n    CORSMiddleware,\n    allow_origins=["*"],\n    allow_credentials=True,\n'
           '    allow_methods=["*"],\n)\n')
LOCKED_PY = ('# CORS lockdown. allow_origins=["*"] meant ANY website could hit the API.\n'
             '_ALLOWED = ["https://example.com"]\n' + "x = 1\n" * 30 +
             'app.add_middleware(CORSMiddleware, allow_origins=_ALLOWED, allow_credentials=True)\n')


def test_real_wildcard_with_credentials_fires_python_and_js():
    [py] = check_cors_open_with_credentials({"main.py": REAL_PY})
    assert py["rule_id"] == "cors_open_with_credentials" and py["evidence"]["line"] == 3
    js = "app.use(cors({ origin: '*', credentials: true }));\n"
    assert len(check_cors_open_with_credentials({"server.js": js})) == 1


def test_regression_iter408_comment_mentioning_wildcard_is_not_a_setting():
    assert check_cors_open_with_credentials({"main.py": LOCKED_PY}) == []
    js = "// was: origin: '*' with credentials: true\n/* origin: '*' */\napp.use(cors({ origin: allowed, credentials: true }));\n"
    assert check_cors_open_with_credentials({"server.js": js}) == []


def test_regression_iter408_wildcard_and_credentials_far_apart_are_not_one_setting():
    far = 'a = dict(allow_origins=["*"])\n' + "x = 1\n" * 400 + "b = dict(allow_credentials=True)\n"
    assert check_cors_open_with_credentials({"cfg.py": far}) == []


def test_regression_iter408_tests_and_scanner_rule_files_are_skipped():
    assert check_cors_open_with_credentials({"backend/tests/test_cors.py": REAL_PY,
                                             "web/cors.test.js": "cors({ origin: '*', credentials: true })"}) == []
    assert check_cors_open_with_credentials({"backend/services/bug_hunt_rules.py": REAL_PY}) == []


def test_line_numbers_survive_comment_blanking():
    code = "# note\n# more\n" + REAL_PY
    assert check_cors_open_with_credentials({"main.py": code})[0]["evidence"]["line"] == 5
