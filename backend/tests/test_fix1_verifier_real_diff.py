"""
FIX 1 (founder-ordered, 2026-10-03) — the verifier must see the real
change.

Before this fix, services/task_independent_verifier.py sent only the
first 2,500 characters of each NEW file's content and called it "the
diff" — the original/old content was never shown at all, and anything
past char 2,500 was invisible. Proven failure mode (see chat history):
a correct one-line fix placed past char 2,500 of a 10,000-char file
was rejected "fail" 5/5 times, because the verifier simply never saw
it.

This test proves the fix: a real unified diff (old vs new, hunks with
context) is sent instead, so a tiny one-line change is fully visible
regardless of how large the surrounding file is — and separately
proves the fix did not weaken the verifier: a diff that genuinely does
NOT satisfy the request (wrong fix, or a README-only change for a
code request) must still fail.

Real LLM integration test — no monkeypatches on the verifier call
itself. Opt-in gate so `pytest` in CI stays free:
    RUN_LLM_INTEGRATION=1 pytest backend/tests/test_fix1_verifier_real_diff.py -v -s
"""
from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv("/app/backend/.env")

from services.task_independent_verifier import (          # noqa: E402
    _format_diff, verify_task_completion,
)

_SKIP_REASON = (
    "Real LLM integration test — set RUN_LLM_INTEGRATION=1 and "
    "OPENROUTER_API_KEY to run."
)
_SHOULD_RUN = (
    os.getenv("RUN_LLM_INTEGRATION") == "1"
    and bool(os.getenv("OPENROUTER_API_KEY", "").strip())
)


@pytest_asyncio.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    d = client[os.environ["DB_NAME"]]
    yield d
    client.close()


def _build_10k_file_with_fix_near_end() -> tuple:
    """Same construction as the manual proof run: a ~10,000-char file,
    header + filler, with the ONE correct line-fix in the final ~80
    chars — i.e. entirely outside the old 2,500-char truncation
    window, but trivially visible in a real diff's hunk regardless of
    total file size. Padding is whole filler LINES only (never a
    partial line), so the fix line always starts cleanly on its own
    line — a prerequisite for a correct line-based diff."""
    filler_line = "# filler line to pad file length, no semantic content here\n"
    header = "def calculate_total(items):\n    total = 0\n"
    old_bug_line = "    for i in range(1, len(items)):\n        total += items[i]\n    return total\n"
    new_fix_line = "    for i in range(0, len(items)):\n        total += items[i]\n    return total\n"

    target_len = 10_000
    pad_budget = target_len - len(header) - len(old_bug_line)
    whole_lines = pad_budget // len(filler_line)
    body_filler = filler_line * whole_lines

    old_content = header + body_filler + old_bug_line
    new_content = header + body_filler + new_fix_line
    assert len(old_content) >= 9_900, len(old_content)
    assert old_content.endswith(old_bug_line) and new_content.endswith(new_fix_line)
    return old_content, new_content


@pytest.mark.skipif(not _SHOULD_RUN, reason=_SKIP_REASON)
@pytest.mark.asyncio
async def test_large_file_one_line_fix_near_end_is_not_rejected(db):
    """FIX 1a/1c proof: the exact case that produced 5/5 false
    'fail' before this fix. Run 5 times; on today's fixed code every
    run must see the real diff hunk and return 'pass' — never
    'fail'."""
    old_content, new_content = _build_10k_file_with_fix_near_end()
    task_text = (
        "Fix the off-by-one bug in calculate_total(): it currently "
        "starts the loop at index 1 instead of 0, silently skipping "
        "items[0]. Change the loop to start at index 0 so every item "
        "is included in the total."
    )
    files = {"app/utils.py": new_content}
    before_files = {"app/utils.py": old_content}

    decisions = []
    for i in range(5):
        task_id = f"fix1_largefile_{uuid.uuid4().hex[:8]}"
        result = await verify_task_completion(
            db, task_id=task_id, project_id="test_proj", user_id="test_user",
            task_text=task_text, files=files, before_files=before_files,
            executed_by="worker_api:deepseek", origin="test_fix1",
        )
        decisions.append(result["decision"])
        print(f"[run {i+1}] decision={result['decision']!r} reason={result['reason']!r}")

    print(f"[fix1/largefile] 5 decisions: {decisions}")
    assert "fail" not in decisions, (
        f"FIX 1 REGRESSION: verifier still rejected a correct change "
        f"it could not have failed to see. decisions={decisions}")
    assert decisions.count("pass") == 5, (
        f"Expected all 5 runs to see the (now tiny, hunk-sized) real "
        f"diff and PASS outright; got {decisions}. A real diff makes "
        f"this change trivially visible regardless of file size, so "
        f"anything other than 5x pass means the diff format still "
        f"isn't reaching the model correctly.")


@pytest.mark.skipif(not _SHOULD_RUN, reason=_SKIP_REASON)
@pytest.mark.asyncio
async def test_wrong_fix_still_fails(db):
    """FIX 1e proof #2: a diff that genuinely does NOT implement the
    request must still fail — the fix must not have made the
    verifier permissive."""
    task_text = (
        "Fix the off-by-one bug in calculate_total(): it currently "
        "starts the loop at index 1 instead of 0, silently skipping "
        "items[0]. Change the loop to start at index 0 so every item "
        "is included in the total."
    )
    old_content = (
        "def calculate_total(items):\n"
        "    total = 0\n"
        "    for i in range(1, len(items)):\n"
        "        total += items[i]\n"
        "    return total\n"
    )
    # Does NOT fix the bug — only renames a loop variable.
    new_content = (
        "def calculate_total(items):\n"
        "    total = 0\n"
        "    for idx in range(1, len(items)):\n"
        "        total += items[idx]\n"
        "    return total\n"
    )
    task_id = f"fix1_wrongfix_{uuid.uuid4().hex[:8]}"
    result = await verify_task_completion(
        db, task_id=task_id, project_id="test_proj", user_id="test_user",
        task_text=task_text, files={"app/utils.py": new_content},
        before_files={"app/utils.py": old_content},
        executed_by="worker_api:deepseek", origin="test_fix1",
    )
    print(f"[fix1/wrongfix] decision={result['decision']!r} reason={result['reason']!r}")
    assert result["decision"] == "fail", (
        f"Expected 'fail' for a diff that doesn't fix the bug, got "
        f"{result['decision']!r} ({result['reason']!r})")


@pytest.mark.skipif(not _SHOULD_RUN, reason=_SKIP_REASON)
@pytest.mark.asyncio
async def test_readme_only_change_for_code_request_still_fails(db):
    """FIX 1e proof #3: a README-only diff for a code-fix request
    must still fail."""
    task_text = (
        "Fix the off-by-one bug in calculate_total() in app/utils.py: "
        "it currently starts the loop at index 1 instead of 0, "
        "silently skipping items[0]. Change the loop to start at "
        "index 0."
    )
    old_readme = "# My App\n\nA simple app.\n"
    new_readme = "# My App\n\nA simple app. Now with bug fixes!\n"
    task_id = f"fix1_readmeonly_{uuid.uuid4().hex[:8]}"
    result = await verify_task_completion(
        db, task_id=task_id, project_id="test_proj", user_id="test_user",
        task_text=task_text, files={"README.md": new_readme},
        before_files={"README.md": old_readme},
        executed_by="worker_api:deepseek", origin="test_fix1",
    )
    print(f"[fix1/readmeonly] decision={result['decision']!r} reason={result['reason']!r}")
    assert result["decision"] == "fail", (
        f"Expected 'fail' for a README-only diff answering a code "
        f"request, got {result['decision']!r} ({result['reason']!r})")


# ─────────────────────────────────────────────────────────────────────
# Deterministic tests below — NO LLM key, NO network call, run in
# plain `pytest` with no env flags. These are the ones CI actually
# runs on every push.
# ─────────────────────────────────────────────────────────────────────

def test_format_diff_shows_the_changed_line_and_hunk_header_on_10k_file():
    """1a — deterministic, pure function. On the exact 10,000-char
    case, the real-diff output must contain the changed line itself
    and a '@@' hunk header — i.e. the change is actually IN the text
    sent to the model, not just conceptually "visible in theory"."""
    old_content, new_content = _build_10k_file_with_fix_near_end()
    diff_text, any_omitted = _format_diff(
        {"app/utils.py": old_content}, {"app/utils.py": new_content},
    )
    print(f"[1a] diff_text ({len(diff_text)} chars):\n{diff_text}")
    print(f"[1a] any_omitted={any_omitted}")
    assert "@@" in diff_text, "no hunk header in the diff output at all"
    assert "for i in range(0, len(items)):" in diff_text, (
        "the actual fixed line is not present in the diff text sent "
        "to the verifier — this is the exact bug FIX 1 was meant to "
        "close")
    assert "+    for i in range(0, len(items)):" in diff_text, (
        "the fixed line is present but not marked as an added ('+') "
        "line — not a real diff")
    assert any_omitted is False, (
        "a single one-line change in one file should never need to "
        "omit anything, regardless of the surrounding file's size")


@pytest.mark.asyncio
async def test_omitted_diff_with_fake_fail_becomes_inconclusive(db, monkeypatch):
    """1b — deterministic: force `any_omitted=True` with 13 tiny
    files (one over `_MAX_FILES_SHOWN=12`, so the 13th is reported as
    "not shown" — no need for a huge file to trigger this), fake the
    LLM reply to 'fail', and assert the code-level override in rule
    (c) fires: decision becomes 'inconclusive' with the
    `fail_overridden_incomplete_view:` prefix."""
    files = {f"file_{i}.py": f"content {i}\n" for i in range(13)}
    before_files = {f"file_{i}.py": f"old content {i}\n" for i in range(13)}

    async def _fake_one_shot(**kwargs):
        return '{"verdict":"fail","reason":"fake fail for test"}', {}, None

    monkeypatch.setattr(
        "services.task_independent_verifier._one_shot", _fake_one_shot)

    task_id = f"fix1_omitted_fakefail_{uuid.uuid4().hex[:8]}"
    result = await verify_task_completion(
        db, task_id=task_id, project_id="test_proj", user_id="test_user",
        task_text="do something", files=files, before_files=before_files,
        executed_by="worker_api:deepseek", origin="test_fix1_deterministic",
    )
    print(f"[1b] decision={result['decision']!r} reason={result['reason']!r}")
    assert result["decision"] == "inconclusive", (
        f"expected 'inconclusive' (fail must be overridden when "
        f"any_omitted=True), got {result['decision']!r}")
    assert result["reason"].startswith("fail_overridden_incomplete_view:"), (
        f"expected the override reason prefix, got {result['reason']!r}")


@pytest.mark.asyncio
async def test_non_omitted_diff_with_fake_fail_stays_fail(db, monkeypatch):
    """1c — deterministic: a tiny single-file diff (nothing omitted),
    fake the LLM reply to 'fail', and assert the decision stays
    'fail' — the override must NOT fire when nothing was actually
    left out."""
    files = {"a.py": "line1\nCHANGED\nline3\n"}
    before_files = {"a.py": "line1\nline2\nline3\n"}

    async def _fake_one_shot(**kwargs):
        return '{"verdict":"fail","reason":"fake fail for test"}', {}, None

    monkeypatch.setattr(
        "services.task_independent_verifier._one_shot", _fake_one_shot)

    task_id = f"fix1_notomitted_fakefail_{uuid.uuid4().hex[:8]}"
    result = await verify_task_completion(
        db, task_id=task_id, project_id="test_proj", user_id="test_user",
        task_text="do something", files=files, before_files=before_files,
        executed_by="worker_api:deepseek", origin="test_fix1_deterministic",
    )
    print(f"[1c] decision={result['decision']!r} reason={result['reason']!r}")
    assert result["decision"] == "fail", (
        f"expected 'fail' to stay 'fail' when nothing was omitted, "
        f"got {result['decision']!r} ({result['reason']!r})")
    assert not result["reason"].startswith("fail_overridden_incomplete_view:"), (
        "override prefix must not appear when any_omitted=False")
