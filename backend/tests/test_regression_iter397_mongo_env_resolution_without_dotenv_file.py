"""
test_regression_iter397_mongo_env_resolution_without_dotenv_file.py

CI-trustworthiness audit — "Fitness-function invariants" job, real
failure from CI run 36621460624 (branch main @ 5f97cc88):

    ERROR tests/test_project_connect_mongo_regression.py::
        test_regression_callback_then_explicit_add_persists_across_clients
        - KeyError: 'MONGO_URL'
    ERROR tests/test_project_connect_mongo_regression.py::
        test_regression_foreign_installation_cannot_create_a_project
        - KeyError: 'MONGO_URL'

Root cause: that file resolved MONGO_URL/DB_NAME with
`dotenv_values(Path(__file__).parents[1] / ".env")` alone — which
reads the physical `backend/.env` FILE on disk, not `os.environ`.
GitHub Actions sets these via the job's `env:` block (real
os.environ); it never writes an actual `.env` file, so
`dotenv_values()` returned `{}` and `env["MONGO_URL"]` raised
`KeyError` on every CI run. Locally (this sandbox, a committed
backend/.env) it always looked fine, which is exactly why this class
of bug survives review — it can only be reproduced with the file
absent, which this test does explicitly rather than relying on CI to
catch it.

Fix (tests/test_project_connect_mongo_regression.py): resolve as
`{**os.environ, **dotenv_values(...)}` — os.environ first (so CI's
real env always has a value), a real local .env layered on top
(so local/preview behaviour is unchanged when one exists).
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from dotenv import dotenv_values


def test_env_resolution_falls_back_to_os_environ_when_no_dotenv_file():
    """The exact merge used by test_project_connect_mongo_regression.py's
    `flow` fixture and its second-client check. Must resolve MONGO_URL/
    DB_NAME from os.environ even when no backend/.env file exists on
    disk — this is always true on a GitHub Actions runner."""
    missing_env_path = Path("/nonexistent-in-ci/.env")
    assert not missing_env_path.exists()

    fake_environ = {
        "MONGO_URL": "mongodb://localhost:27017",
        "DB_NAME": "aurem_ci_smoke",
        "PATH": os.environ.get("PATH", ""),
    }
    with patch.dict(os.environ, fake_environ, clear=True):
        resolved = {**os.environ, **dotenv_values(missing_env_path)}

    assert resolved["MONGO_URL"] == "mongodb://localhost:27017"
    assert resolved["DB_NAME"] == "aurem_ci_smoke"


def test_a_real_dotenv_file_still_wins_over_os_environ():
    """Local/preview parity check: when a real backend/.env DOES exist
    (every non-CI environment), its values must still take priority
    over whatever happens to be in the process's os.environ — this
    merge direction (os.environ first, file second) must not silently
    flip that priority."""
    import tempfile
    with tempfile.NamedTemporaryFile(
            mode="w", suffix=".env", delete=False) as fh:
        fh.write("MONGO_URL=mongodb://localhost:27017\n"
                 "DB_NAME=aurem_from_dotenv_file\n")
        dotenv_path = Path(fh.name)
    try:
        with patch.dict(os.environ,
                        {"DB_NAME": "aurem_from_os_environ"}):
            resolved = {**os.environ, **dotenv_values(dotenv_path)}
        assert resolved["DB_NAME"] == "aurem_from_dotenv_file"
    finally:
        dotenv_path.unlink()
