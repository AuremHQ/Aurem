"""test_2026_10_c3_secret_masking.py — C3 fix.

Founder report: ORA shows secret values in full. Reproducible via
`save_finding` (services/local_tools.py): when a user pastes code
containing a real secret and asks ORA to review it, the model's own
free-text description can embed the raw value verbatim, and that
text is what gets persisted — then later shown back via Fix List /
the mascot / chat history.

Fix: `services/secret_masking.redact_secrets_in_text` masks any
secret-shaped value to first 2 + last 2 characters, wired into (1)
`save_finding`'s title/message/fix_hint, (2) `vanguard_scanner`'s
`snippet` field (the raw-matched-source-line leak for REPO scans,
not just chat-pasted code), and (3) `apply_output_guards` (the one
shared pipeline every chat answer AND every persisted chat message
goes through).
"""
import time
import pytest

from services.secret_masking import mask_secret_value, redact_secrets_in_text


def test_mask_secret_value_first_2_last_2():
    assert mask_secret_value("critical-marker-round3-xyz") == "cr**********************yz"
    assert mask_secret_value("sk-abcdefgh12345") == "sk************45"
    assert mask_secret_value("abcd") == "****"       # <=4 chars: fully masked
    assert mask_secret_value("ab") == "**"


def test_redact_secrets_in_text_masks_keyword_assignment_not_surrounding_prose():
    text = 'The secret key SECRET_KEY = "critical-marker-round3-xyz" is hardcoded in auth.py:14.'
    out = redact_secrets_in_text(text)
    assert "critical-marker-round3-xyz" not in out
    assert "cr" in out and "yz" in out
    assert "auth.py:14" in out, "file/line context must survive untouched"
    assert "is hardcoded in" in out, "surrounding prose must survive untouched"


def test_redact_secrets_in_text_masks_bare_vendor_key_shapes():
    text = "Found a leaked OpenAI key: sk-abc123def456ghi789jklmno in requirements notes."
    out = redact_secrets_in_text(text)
    assert "sk-abc123def456ghi789jklmno" not in out


def test_redact_secrets_in_text_no_secret_is_a_pure_no_op():
    text = "No findings right now — you're all caught up."
    assert redact_secrets_in_text(text) == text


# ─── 2026-10 round 2 — founder's exact leaked shapes ────────────────
def test_name_password_assignment_shape_is_masked():
    """`NAME_PASSWORD = "<value>"` — a secret keyword used as a SUFFIX
    of a longer identifier (not a bare `password` on its own), the
    exact shape the founder's own test leaked."""
    text = 'DB_PASSWORD = "hunter2superSecretValue"'
    out = redact_secrets_in_text(text)
    assert "hunter2superSecretValue" not in out
    assert "DB_PASSWORD" in out, "the variable NAME must survive untouched"
    assert "hu" in out and "ue" in out


def test_os_environ_get_default_value_shape_is_masked():
    """`os.environ.get("..._KEY", "<value>")` — the secret-shaped
    indicator lives in the KEY-NAME argument; the value that must be
    masked is the DEFAULT (second) argument, a structurally different
    shape than a `KEYWORD = value` assignment."""
    text = 'os.environ.get("STRIPE_SECRET_KEY", "sk_live_abcdefgh12345")'
    out = redact_secrets_in_text(text)
    assert "sk_live_abcdefgh12345" not in out
    assert '"STRIPE_SECRET_KEY"' in out, "the env var NAME must survive untouched"
    assert "sk" in out and "45" in out


def test_os_getenv_default_value_shape_is_masked():
    text = "os.getenv('DB_PASSWORD', 'fallback-dev-password-value')"
    out = redact_secrets_in_text(text)
    assert "fallback-dev-password-value" not in out
    assert "'DB_PASSWORD'" in out


def test_masking_never_alters_code_ora_writes_into_the_repo():
    """The masker must only ever be applied to DISPLAY/stored finding
    text — never to the actual file content ORA generates/writes as
    a fix. This locks that `redact_secrets_in_text` is a pure, opt-in
    function call: nothing in `finding_fix_applier`'s write path may
    invoke it on the content it's about to commit."""
    import inspect
    from services import finding_fix_applier
    src = inspect.getsource(finding_fix_applier)
    assert "redact_secrets_in_text" not in src, (
        "masking must never touch the content ORA actually writes to the repo"
    )
    assert "mask_secret_value" not in src


def test_fix_pipeline_uses_live_file_content_not_the_stored_snippet():
    """Founder's concern: does `finding_fix_applier` use the stored
    (now-masked) `snippet` to LOCATE the line? It does not — it sends
    the LLM the full, freshly-fetched, REAL file content every time;
    `snippet` is only a short label in the prompt's finding summary.
    Proven here by inspecting the actual prompt built for a finding
    whose stored snippet IS masked: the real secret must still be
    present in the file-content section (so the LLM can locate/patch
    it), while the masked snippet is just inert context text."""
    import asyncio
    from unittest.mock import AsyncMock, patch
    from services.finding_fix_applier import _generate_patched_content

    real_secret = "sk-liveRealSecretValue99887"
    current_content = f'API_KEY = "{real_secret}"\n'
    masked_snippet = redact_secrets_in_text(f'API_KEY = "{real_secret}"')
    assert real_secret not in masked_snippet, "sanity: snippet really is masked"

    finding = {
        "rule_id": "generic_api_key", "severity": "critical", "line": 1,
        "title": "Hardcoded API key", "message": "API key hardcoded in source.",
        "snippet": masked_snippet,
    }

    captured_prompt = {}

    async def _fake_llm(**kwargs):
        captured_prompt["user"] = kwargs.get("user")
        return {"content": f'API_KEY = os.environ.get("API_KEY")\n'}

    with patch("services.llm.call_llm_with_meta", new=AsyncMock(side_effect=_fake_llm)):
        patched, err = asyncio.get_event_loop().run_until_complete(
            _generate_patched_content(
                path="config.py", current_content=current_content,
                finding=finding, user_id="pytest_user", db=None,
            )
        )
    assert err is None
    assert patched.strip() == 'API_KEY = os.environ.get("API_KEY")'
    # The real secret WAS in the prompt (via live file content) even
    # though the stored finding's snippet was masked — proving the
    # masked snippet never stood in the way of locating/fixing it.
    assert real_secret in captured_prompt["user"], (
        "fix pipeline must still see the REAL file content, not the masked snippet"
    )


def test_vanguard_scanner_snippet_is_masked_not_raw():
    from services.vanguard_scanner import scan_text
    code = 'API_KEY = "sk-abcdefghijklmnop1234"\n'
    findings = scan_text(code, "config.py", include_dangerous=False)
    assert findings, "scanner should have matched the generic_api_key pattern"
    for f in findings:
        assert "sk-abcdefghijklmnop1234" not in f["snippet"], (
            f"raw secret leaked in scan snippet: {f['snippet']!r}"
        )


@pytest.mark.asyncio
async def test_save_finding_masks_secret_in_description_before_persisting():
    from cto_services.db import get_db, set_db
    if get_db() is None:
        import os
        from motor.motor_asyncio import AsyncIOMotorClient
        set_db(AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "aurem_dev")])

    from services.bin_context import BINContext
    from services.local_tools import invoke_local_tool

    project_id = f"pytest_c3_mask_{int(time.time())}"
    bc = BINContext(bin_id="pytest_user", pid=project_id, repo_owner="octocat",
                     repo_name="Hello-World", branch="main", pat="fake", is_founder=False)
    ctx = {"user_id": "pytest_user", "bin_ctx": bc}

    raw_secret = "critical-marker-round3-xyz"
    r = await invoke_local_tool("save_finding", {
        "title": "Hardcoded SECRET_KEY in source code",
        "severity": "critical",
        "file": "config.py",
        "line": 7,
        "description": f'The secret key SECRET_KEY = "{raw_secret}" is hardcoded in source.',
        "fix_hint": "Move to an env var / secrets manager, never commit the raw value.",
    }, ctx)

    assert r["ok"] is True
    assert raw_secret not in r["message"], "tool response itself must not leak the raw value"

    db = get_db()
    doc = await db.cto_open_findings.find_one({"project_id": project_id, "user_id": "pytest_user"})
    assert doc is not None
    assert raw_secret not in doc["message"], "persisted finding must not store the raw value"
    assert raw_secret not in doc["title"]
    await db.cto_open_findings.delete_many({"project_id": project_id})


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
