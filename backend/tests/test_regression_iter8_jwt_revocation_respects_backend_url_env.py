"""
test_regression_iter8_jwt_revocation_respects_backend_url_env.py

Second real bug found diagnosing quality-gate.yml's "Fitness-function
invariants" job on branch conflict_280926_2109 (2026-09-29), same file
as iter4's fix: `test_jwt_revocation.py` hardcoded
`API = "http://localhost:8001/api/aurem-dev"` and
`httpx.get("http://localhost:8001/health", ...)`, unlike every other
live-HTTP test file in this suite (test_loop_gate_chat_stream_2026_01.py,
test_iter_restore_drill_live_verify.py, etc.), which all read
`REACT_APP_BACKEND_URL`.

That only "worked" by coincidence whenever whatever backend happened
to be listening on :8001 was ALSO the one holding the ci-test
JWT_SECRET. Confirmed live: booting the CI backend on a different
port (:8091, to dodge a stale process already bound to :8001)
made `test_same_token_rejected_after_logout` sign a token against
one backend's secret and decode it against a different backend's
real `backend/.env` secret, raising `jwt.exceptions.InvalidSignatureError`
— the exact failure class iter4's `override=True` fix was written to
prevent, but from a different cause.

Fix: `API`/`_backend_reachable()` now derive from
`REACT_APP_BACKEND_URL` (falling back to `http://localhost:8001` only
when that env var is unset, preserving old behaviour for anyone
running this file with nothing configured).
"""
import importlib


def test_regression_iter8_api_url_derives_from_backend_url_env(monkeypatch):
    monkeypatch.setenv("REACT_APP_BACKEND_URL", "http://localhost:9999")
    import tests.test_jwt_revocation as mod
    importlib.reload(mod)
    try:
        assert mod.API == "http://localhost:9999/api/aurem-dev", (
            f"API did not pick up REACT_APP_BACKEND_URL: {mod.API!r}"
        )
        assert mod._BACKEND_URL == "http://localhost:9999"
    finally:
        monkeypatch.delenv("REACT_APP_BACKEND_URL", raising=False)
        importlib.reload(mod)  # restore module state for later tests


def test_regression_iter8_api_url_falls_back_when_env_unset(monkeypatch):
    monkeypatch.delenv("REACT_APP_BACKEND_URL", raising=False)
    import tests.test_jwt_revocation as mod
    importlib.reload(mod)
    assert mod.API == "http://localhost:8001/api/aurem-dev"
