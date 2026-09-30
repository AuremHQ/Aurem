"""
test_regression_iter402_execute_bash_blocks_write_flags.py
==============================================================

Medium finding (code-read security audit, 2026-09-30): `execute_bash`
(services/local_tools.py) is documented as a "read-only local pod
filesystem" tool and gates on a binary allowlist
(`_BASH_ALLOWED = {..., "sed", "find", ...}`), but never actually
checked the FLAGS used with `sed`/`find` — both binaries can write or
delete files despite being in a "read-only" allowlist:

  - `sed -i 's/x/y/' /app/backend/main.py`  → rewrites the file in place
  - `find /app -name '*.py' -delete`        → deletes every match
  - `find /app -exec rm {} \\;`              → arbitrary command exec per match

This closed the gap the earlier "PR #192" fix mentioned only closing
for the propose-only agent flow; a direct execute_bash call (any other
flow) was still fully open to both. Fix: block the specific write/
delete/exec-capable flags for sed and find only — every other sed/find
invocation (the vast majority: filtering, printing, searching) is
completely unaffected.
"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_sed_inplace_short_flag_blocked():
    from services.local_tools import execute_bash
    r = await execute_bash(
        {"is_founder": True, "user_id": "founder"},
        {"command": "sed -i s/foo/bar/ ./iter402_nonexistent.txt"},
    )
    assert r["ok"] is False
    assert r["error_class"] == "write_flag_blocked"


@pytest.mark.asyncio
async def test_sed_inplace_with_backup_suffix_blocked():
    """`-i.bak` is `-i` with an attached backup suffix — still in-place."""
    from services.local_tools import execute_bash
    r = await execute_bash(
        {"is_founder": True, "user_id": "founder"},
        {"command": "sed -i.bak s/foo/bar/ ./iter402_nonexistent.txt"},
    )
    assert r["ok"] is False
    assert r["error_class"] == "write_flag_blocked"


@pytest.mark.asyncio
async def test_sed_inplace_combined_short_flags_blocked():
    """GNU sed lets short options combine (`-ni` == `-n -i`) — the
    combined-cluster form must be caught too, not just a bare `-i`."""
    from services.local_tools import execute_bash
    r = await execute_bash(
        {"is_founder": True, "user_id": "founder"},
        {"command": "sed -ni s/foo/bar/p ./iter402_nonexistent.txt"},
    )
    assert r["ok"] is False
    assert r["error_class"] == "write_flag_blocked"


@pytest.mark.asyncio
async def test_sed_long_form_inplace_blocked():
    from services.local_tools import execute_bash
    r = await execute_bash(
        {"is_founder": True, "user_id": "founder"},
        {"command": "sed --in-place s/foo/bar/ ./iter402_nonexistent.txt"},
    )
    assert r["ok"] is False
    assert r["error_class"] == "write_flag_blocked"


@pytest.mark.asyncio
async def test_sed_without_inplace_still_works():
    """Regression: plain, non-mutating sed (prints to stdout) must
    stay fully functional — this fix must not over-block."""
    from services.local_tools import execute_bash
    r = await execute_bash(
        {"is_founder": True, "user_id": "founder"},
        {"command": "sed -n 1p ./requirements.txt"},
    )
    assert r["ok"] is True
    assert r["ok"] is True


@pytest.mark.asyncio
async def test_find_delete_blocked():
    from services.local_tools import execute_bash
    r = await execute_bash(
        {"is_founder": True, "user_id": "founder"},
        {"command": "find ./iter402_nonexistent_dir -name *.txt -delete"},
    )
    assert r["ok"] is False
    assert r["error_class"] == "write_flag_blocked"


@pytest.mark.asyncio
async def test_find_exec_blocked():
    from services.local_tools import execute_bash
    r = await execute_bash(
        {"is_founder": True, "user_id": "founder"},
        {"command": "find ./iter402_nonexistent_dir -name *.txt -exec rm {} +"},
    )
    assert r["ok"] is False
    assert r["error_class"] == "write_flag_blocked"


@pytest.mark.asyncio
async def test_find_ok_and_execdir_blocked():
    from services.local_tools import execute_bash
    r1 = await execute_bash(
        {"is_founder": True, "user_id": "founder"},
        {"command": "find ./iter402_nonexistent_dir -ok rm {} +"},
    )
    r2 = await execute_bash(
        {"is_founder": True, "user_id": "founder"},
        {"command": "find ./iter402_nonexistent_dir -execdir rm {} +"},
    )
    assert r1["ok"] is False and r1["error_class"] == "write_flag_blocked"
    assert r2["ok"] is False and r2["error_class"] == "write_flag_blocked"


@pytest.mark.asyncio
async def test_find_without_write_flags_still_works():
    """Regression: plain find (search/list) must stay fully functional."""
    from services.local_tools import execute_bash
    r = await execute_bash(
        {"is_founder": True, "user_id": "founder"},
        {"command": "find . -maxdepth 1 -name iter402_placeholder_xyz"},
    )
    assert r["ok"] is True
