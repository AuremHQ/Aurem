"""
test_regression_iter4_jwt_test_helper_env_load_no_override.py

Real bug found while diagnosing quality-gate.yml's "Fitness-function
invariants" job (2026-09-29): tests/test_jwt_revocation.py called
`load_dotenv("/app/backend/.env", override=True)` at import time.
`override=True` unconditionally replaces ANY already-set environment
variable — including JWT_SECRET explicitly passed to the pytest step
by the CI job's own env block ("ci-test-secret-32-chars-minimum!!").
The CI-booted backend signs tokens with THAT CI secret, but the test
process then clobbered its own JWT_SECRET back to backend/.env's
value before decoding — so every manual `jwt.decode(...)` in that
file raised `jwt.exceptions.InvalidSignatureError`, always, in that
job specifically (confirmed live).

Fix: `override=False` (i.e. no `override` kwarg — that's the
python-dotenv default), which only fills in variables NOT already
present in os.environ. Local dev (nothing pre-set) still gets
JWT_SECRET from .env; CI (JWT_SECRET pre-set by the workflow) keeps
its own value, matching what the CI-booted backend actually signed
tokens with.
"""
from __future__ import annotations

import os
import subprocess
import sys


def test_regression_iter4_jwt_test_module_does_not_force_override_env():
    """Source-level lock: the load_dotenv call in test_jwt_revocation.py
    must not pass override=True, or a pre-set CI env var (JWT_SECRET)
    would always be clobbered by backend/.env's value."""
    src = open("/app/backend/tests/test_jwt_revocation.py").read()
    idx = src.find("load_dotenv(")
    assert idx > -1, "test_jwt_revocation.py must load backend/.env"
    call = src[idx: src.find(")", idx) + 1]
    assert "override=True" not in call, (
        f"load_dotenv call must not force override=True (clobbers a "
        f"CI-provided JWT_SECRET): {call!r}"
    )


def test_regression_iter4_pre_set_env_var_survives_module_import():
    """Behavioural: with JWT_SECRET pre-set in the environment (as the
    CI job's 'Run regression + invariant tests' step does), importing
    test_jwt_revocation.py must NOT change it — proving override=False
    actually holds at runtime, not just in the source text."""
    sentinel = "pre-set-by-this-regression-test-32chars!!"
    code = (
        "import os, sys\n"
        "sys.path.insert(0, '/app/backend')\n"
        "sys.path.insert(0, '/app/backend/tests')\n"
        "os.environ['JWT_SECRET'] = " + repr(sentinel) + "\n"
        "import test_jwt_revocation\n"
        "assert os.environ['JWT_SECRET'] == " + repr(sentinel) + ", (\n"
        "    'JWT_SECRET was clobbered by importing test_jwt_revocation: '\n"
        "    + os.environ['JWT_SECRET']\n"
        ")\n"
        "print('OK')\n"
    )
    env = dict(os.environ)
    env["MONGO_URL"] = env.get("MONGO_URL", "mongodb://localhost:27017")
    env["DB_NAME"] = env.get("DB_NAME", "aurem_dev")
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=15, env=env,
        cwd="/app/backend",
    )
    assert proc.returncode == 0, (
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "OK" in proc.stdout
