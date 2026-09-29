"""Interaction reliability contract regression suite (gate-listed file).

Behavioral coverage here focuses on shared mechanisms (routing, persistence,
rendering safety, tool-failure recurrence, and deploy-gate fail-closed shape).
"""
from __future__ import annotations

import inspect
import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from services.chat_helpers import classify_intent, run_rendering_pipeline, _persist_turn
from services.interaction_contract import finalize
from services.mode_b_council import is_council_request
from services.regression_backlog_cron import scan_and_flag_recurring_failure_reasons
from services.task_chain_logging import log_task_chain
from services.tool_executor import execute as execute_tool
from services.tool_failure_evidence import failure_signatures

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def interaction_flag(monkeypatch):
    monkeypatch.setenv("INTERACTION_CONTRACT_V1", "1")


# -------------------- Pipeline fidelity / substitution guards -----------------

@pytest.mark.asyncio
@pytest.mark.parametrize("plain_english_active", [True, False])
@pytest.mark.parametrize("ora_panel", [True, False])
async def test_rendering_pipeline_preserves_substantive_design_reply_not_default_fragment(
    plain_english_active,
    ora_panel,
):
    prompt = "Design a new color palette for the site"
    content = (
        "The existing contrast ratio is 2.1:1. "
        "Choose charcoal text on white to meet WCAG AA."
    )
    out, trace = await run_rendering_pipeline(
        user_message=prompt,
        content=content,
        prior_fix_signal=False,
        retrieved_context="",
        skip=False,
        tool_calls_run=0,
        session_id="s_interaction_audit",
        user_id="u_interaction_audit",
        plain_english_active=plain_english_active,
        ora_panel=ora_panel,
    )
    assert "contrast ratio is 2.1:1" in out
    assert "DEFAULT_DESIGN_REPLY" not in out
    assert isinstance(trace.get("passes"), list)


@pytest.mark.asyncio
@pytest.mark.parametrize("plain_english_active", [True, False])
@pytest.mark.parametrize("ora_panel", [True, False])
async def test_rendering_pipeline_preserves_path_line_code_bytes_and_redacts_secret(
    plain_english_active,
    ora_panel,
):
    prompt = "Please explain what this means and try again with more detail."
    raw_secret = "sk-live-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
    content = (
        "Check src/header.py:12 and inline `repo_id` before changing code.\n"
        "Code string: 'Mode D'\n"
        "URL: https://example.com/docs\n"
        f"Leaked token sample: {raw_secret}\n"
    )
    out, _trace = await run_rendering_pipeline(
        user_message=prompt,
        content=content,
        prior_fix_signal=False,
        retrieved_context="",
        skip=False,
        tool_calls_run=0,
        session_id="s_interaction_audit",
        user_id="u_interaction_audit",
        plain_english_active=plain_english_active,
        ora_panel=ora_panel,
    )
    assert "src/header.py:12" in out
    assert "`repo_id`" in out
    assert "Mode D" in out
    assert "https://example.com/docs" in out
    assert raw_secret not in out, "secret redaction must stay active"


@pytest.mark.asyncio
async def test_rendering_pipeline_preserves_unicode_literals_with_multi_secret_redaction_priority():
    raw_secret_a = "sk-live-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
    raw_secret_b = "sk_live_abcDEF1234567890ZYXWVUTSRQ"
    content = (
        "Path: src/नमस्ते.py:42\n"
        "Literal URL: https://пример.рф/путь\n"
        "Literal command text: `git commit --amend`\n"
        f"secret1={raw_secret_a}\n"
        f"secret2={raw_secret_b}\n"
    )
    out, _trace = await run_rendering_pipeline(
        user_message="Summarize exactly, do not alter literals.",
        content=content,
        prior_fix_signal=False,
        retrieved_context="",
        skip=False,
        tool_calls_run=0,
        session_id="s_unicode_literals",
        user_id="u_unicode_literals",
        plain_english_active=False,
        ora_panel=False,
    )
    assert "src/नमस्ते.py:42" in out
    assert "https://пример.рф/путь" in out
    assert "`git commit --amend`" in out
    assert raw_secret_a not in out and raw_secret_b not in out
    assert out.count("[REDACTED]") >= 2


@pytest.mark.asyncio
async def test_quote_try_again_inside_real_technical_reply_must_not_inject_stock_template():
    prompt = "Can you explain this stack trace?"
    content = (
        "In this stack trace, try again is part of a retry policy note, "
        "not a support escalation request."
    )
    out, _trace = await run_rendering_pipeline(
        user_message=prompt,
        content=content,
        prior_fix_signal=False,
        retrieved_context="",
        skip=False,
        tool_calls_run=0,
        session_id="s_interaction_audit",
        user_id="u_interaction_audit",
        plain_english_active=True,
        ora_panel=True,
    )
    assert "update the text, images, or layout on your site" not in out


# -------------------- Persistence + turn-state interaction roundtrip ----------

def test_persist_turn_signature_exposes_optional_interaction_object():
    sig = inspect.signature(_persist_turn)
    assert "interaction" in sig.parameters, (
        "_persist_turn must accept optional interaction={version,turn_state,...}"
    )


@pytest.mark.asyncio
async def test_persist_turn_roundtrip_stores_interaction_and_interrupted_state(audit_db, monkeypatch):
    from services import chat_helpers

    monkeypatch.setattr(chat_helpers, "get_db", lambda: audit_db)
    interaction = {
        "version": 1,
        "request_id": "req_persist_roundtrip",
        "session_id": "s_persist_roundtrip",
        "request_hash": "h_req",
        "content_hash": "h_content",
        "turn_state": "partial",
        "completion_basis": "partial",
        "continue_available": True,
        "failure_mode": "network_drop_mid_stream",
        "recovery_action": "continue",
        "review_evidence": None,
        "suggested_actions": [],
        "verified_paths": ["README.md"],
    }
    await _persist_turn(
        user_id="u_persist_roundtrip",
        session_id="s_persist_roundtrip",
        user_prompt="continue previous response",
        assistant_reply="partial chunk",
        provider="mode-a",
        interaction=interaction,
        interrupted=True,
    )
    row = await audit_db.chat_sessions.find_one(
        {"session_id": "s_persist_roundtrip", "user_id": "u_persist_roundtrip"},
        {"_id": 0, "turns": 1},
    )
    assert row is not None
    turns = row.get("turns") or []
    assert len(turns) == 2
    assistant_turn = turns[-1]
    assert assistant_turn.get("interrupted") is True
    assert assistant_turn.get("interaction", {}).get("request_id") == "req_persist_roundtrip"
    assert assistant_turn.get("interaction", {}).get("turn_state") == "partial"


@pytest.mark.parametrize(
    "result,content,low_confidence,expected_state",
    [
        ({"turn_state": "success"}, "Delivered factual answer", False, "success"),
        ({"turn_state": "success"}, "", False, "error"),
        ({"turn_state": "success", "ok": False}, "tool failed", False, "error"),
        ({"turn_state": "success"}, "That was my next planned step only", False, "partial"),
        ({"turn_state": "success"}, "I couldn't validate this response against the current request.", True, "blocked"),
    ],
)
def test_finalize_turn_state_matrix(result, content, low_confidence, expected_state):
    envelope = finalize(
        result,
        content,
        prompt="What is current status?",
        session_id="s_finalize",
        request_id="r_finalize",
        low_confidence=low_confidence,
    )
    assert envelope is not None
    assert envelope["turn_state"] == expected_state


def test_finalize_hashes_are_stable_for_identical_inputs():
    r = {"turn_state": "success", "verified_paths": ["README.md"]}
    a = finalize(r, "answer", prompt="status?", session_id="s_hash", request_id="req_hash")
    b = finalize(r, "answer", prompt="status?", session_id="s_hash", request_id="req_hash")
    assert a is not None and b is not None
    assert a["request_hash"] == b["request_hash"]
    assert a["content_hash"] == b["content_hash"]


def test_finalize_default_flag_off_returns_none(monkeypatch):
    monkeypatch.setenv("INTERACTION_CONTRACT_V1", "0")
    out = finalize({"turn_state": "success"}, "ok", prompt="p", session_id="s", request_id="r")
    assert out is None


def test_shared_bail_cannot_invent_a_website_task():
    from services.bail_reason import classify_bail
    out = classify_bail("Report search tool failures, not website edits")
    assert "where on your site" not in out["message"]
    assert "No completion is confirmed" in out["message"]


def test_style_compliance_cannot_replace_substantive_design_answer():
    from services.design_refusal_guard import strip_design_refusal
    response = "The contrast ratio is 2.1:1. Use charcoal text on white."
    assert strip_design_refusal(response) == response


# -------------------- Routing boundaries: council + intent --------------------

def test_interaction_flag_on_status_question_about_council_is_not_council_request(monkeypatch):
    monkeypatch.setenv("INTERACTION_CONTRACT_V1", "1")
    assert is_council_request("What is the status of the decision council?", "B") is False


def test_interaction_flag_on_genuine_build_or_buy_remains_council_request(monkeypatch):
    monkeypatch.setenv("INTERACTION_CONTRACT_V1", "1")
    assert is_council_request("Should I build or buy?", "B") is True


def test_classify_intent_numbered_factual_status_list_must_not_route_to_d_e_b(monkeypatch):
    monkeypatch.setenv("INTERACTION_CONTRACT_V1", "1")
    message = (
        "1) What is audit status today?\n"
        "2) Which error bucket is highest?\n"
        "3) Council queue size now?"
    )
    mode = classify_intent(message, f12_payload=None, project_id="p_test")
    assert mode not in {"D", "E", "B"}


def test_classify_intent_explicit_audit_request_stays_e(monkeypatch):
    monkeypatch.setenv("INTERACTION_CONTRACT_V1", "1")
    mode = classify_intent("Please audit this file for vulnerabilities", None, "p_test")
    assert mode == "E"


@pytest.mark.asyncio
async def test_chat_send_conflict_via_asgi_blocks_before_mock_path(monkeypatch):
    from main import app
    from routers.chat import turn as turn_router
    from services.ora_chat_v2 import llm_client

    async def _fake_user(_authorization=None):
        return {"user_id": "u_conflict", "tier": "founder", "is_admin": True, "is_unlimited": True}

    hit = {"mock": 0}

    def _bomb_mock():
        hit["mock"] += 1
        raise AssertionError("mock path should not run on conflict")

    monkeypatch.setattr(turn_router, "current_dev", _fake_user)
    monkeypatch.setattr(llm_client, "is_mock", _bomb_mock)

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/aurem-dev/chat/send",
            json={
                "prompt": "Do not modify any code, but update README now.",
                "session_id": "s_conflict_send",
            },
        )
    assert resp.status_code == 409
    assert hit["mock"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("prohibition", ["Never modify any files", "Never modify files"])
async def test_chat_stream_conflict_via_asgi_blocks_before_worker_path(monkeypatch, prohibition):
    from main import app
    from routers.chat import stream as stream_router
    from services.ora_chat_v2 import llm_client

    async def _fake_user(_authorization=None):
        return {"user_id": "u_conflict_stream", "tier": "founder", "is_admin": True, "is_unlimited": True}

    hit = {"mock": 0}

    def _bomb_mock():
        hit["mock"] += 1
        raise AssertionError("mock path should not run on conflict")

    monkeypatch.setattr(stream_router, "current_dev", _fake_user)
    monkeypatch.setattr(llm_client, "is_mock", _bomb_mock)

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/aurem-dev/chat/stream",
            json={
                "prompt": f"{prohibition}, and patch index.js now.",
                "session_id": "s_conflict_stream",
            },
        )
    assert resp.status_code == 409
    assert hit["mock"] == 0


@pytest.mark.asyncio
async def test_quoted_no_edit_phrase_is_negative_control_not_conflict(monkeypatch):
    from main import app
    from routers.chat import turn as turn_router
    from services.ora_chat_v2 import llm_client
    from services.user_response_sanitizer import sanitize_provider_for_user

    async def _fake_user(_authorization=None):
        return {"user_id": "u_negative_control", "tier": "founder", "is_admin": True, "is_unlimited": True}

    monkeypatch.setattr(turn_router, "current_dev", _fake_user)
    monkeypatch.setattr(llm_client, "is_mock", lambda: True)

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/aurem-dev/chat/send",
            json={
                "prompt": 'Say "do not modify any code" in your answer and list status.',
                "session_id": "s_negative_control",
            },
        )
    assert resp.status_code == 200
    payload = resp.json()
    # provider is user-facing, so it is branded ("ORA"), never the raw "mock";
    # the canned content is what proves the mock path ran.
    assert payload.get("provider") == sanitize_provider_for_user("mock")
    assert payload.get("content") == turn_router.MOCK_SEND_TEXT


# -------------------- Contradictory instruction entry guard wiring ------------

def test_chat_send_source_contains_pre_llm_conflict_guard():
    with (ROOT / "backend/routers/chat/turn.py").open("r", encoding="utf-8") as f:
        src = f.read()
    assert "request_conflict" in src, "chat_send must call interaction_contract.request_conflict"
    assert "services.interaction_contract" in src or "interaction_contract" in src
    # guard must run before mock/provider/tool execution branch
    assert src.find("request_conflict") < src.find("_mock_llm_on")


# -------------------- Rule-of-three: tool failures independent of success -----

@pytest.fixture
async def audit_db():
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        pytest.fail("MONGO_URL/DB_NAME required: interaction gate cannot silently skip database checks")
    client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=3000)
    db = client[f"{db_name}_interaction_{uuid4().hex}"]
    try:
        yield db
    finally:
        await client.drop_database(db.name)
        client.close()


@pytest.mark.asyncio
async def test_recurring_failed_tool_signatures_flag_even_when_turn_success(audit_db):
    """Expected new contract: failed tool signatures are logged separately from
    turn success/failure, deduped per-turn, and recurrence scan can flag them."""
    from services.tool_failure_evidence import turn_tool_calls
    base = f"interaction_tool_failure_{int(time.time() * 1000)}"
    # cleanup
    await audit_db.task_chains.delete_many({"session_id": {"$regex": f"^{base}"}})

    try:
        # failure appears at tool-call position 11+ (outside old first-10 window)
        for i in range(3):
            calls = [{"tool": f"ok_{n}", "ok": True} for n in range(10)]
            calls.append({"tool": "write_repo_file", "ok": False, "error": "Permission denied"})
            await log_task_chain(
                audit_db,
                user_id="u_interaction",
                session_id=f"{base}_{i}",
                project_id="p_interaction",
                user_prompt="apply patch",
                tool_calls=turn_tool_calls({"tool_invocations": calls}),
                final_response="Done",
                turn_state="success",
                failure_mode=None,
                sequential_thoughts=None,
            )

        sample = await audit_db.task_chains.find_one(
            {"session_id": f"{base}_0"}, {"_id": 0, "success": 1, "tool_failure_signatures": 1}
        )
        assert sample is not None and sample.get("success") is True
        assert "tool:write_repo_file:tool_failed" in (sample.get("tool_failure_signatures") or [])

        result = await scan_and_flag_recurring_failure_reasons(audit_db, lookback_days=7)
        assert result.get("flagged", 0) >= 1, (
            "recurring failed tool signatures should open review backlog even on success turns"
        )
    finally:
        await audit_db.task_chains.delete_many({"session_id": {"$regex": f"^{base}"}})


@pytest.mark.asyncio
async def test_repeated_failures_within_one_turn_do_not_count_as_three_turns(audit_db):
    sid = f"single_turn_repeat_{uuid4().hex[:8]}"
    calls = [
        {"tool": "write_repo_file", "ok": False, "error_class": "auth", "error": "password=secret"}
        for _ in range(14)
    ]
    await log_task_chain(
        audit_db,
        user_id="u_repeat",
        session_id=sid,
        project_id="p_repeat",
        user_prompt="patch file",
        tool_calls=calls,
        final_response="Completed with fallback",
        turn_state="success",
        failure_mode=None,
        sequential_thoughts=None,
    )
    out = await scan_and_flag_recurring_failure_reasons(audit_db, lookback_days=7)
    assert out.get("flagged", 0) == 0


@pytest.mark.asyncio
async def test_invoke_local_tool_enforces_read_only_scope_and_allows_single_authorized_edit(monkeypatch):
    from services import local_tools

    called = {"count": 0}

    async def _stub_write(_ctx, _args):
        called["count"] += 1
        return {"ok": True, "path": (_args or {}).get("path")}

    monkeypatch.setitem(local_tools.LOCAL_TOOLS, "write_repo_file", _stub_write)

    ctx_ro = {"user_prompt": "Do not modify any code. Just report status.", "tool_calls": []}
    ro = await local_tools.invoke_local_tool(
        "write_repo_file", {"path": "README.md", "content": "x"}, ctx_ro
    )
    assert ro.get("ok") is False
    assert ro.get("error_class") == "read_only_request"
    assert called["count"] == 0

    ctx_scope = {
        "user_prompt": "Please apply that edit now.",
        "tool_calls": [],
        "scope_files": {"src/app.py"},
    }
    blocked = await local_tools.invoke_local_tool(
        "write_repo_file", {"path": "README.md", "content": "x"}, ctx_scope
    )
    assert blocked.get("ok") is False
    assert blocked.get("error_class") == "scope_blocked"

    ctx_ok = {
        "user_prompt": "Please update README.md with one line.",
        "tool_calls": [],
        "scope_files": {"README.md"},
    }
    ok = await local_tools.invoke_local_tool(
        "write_repo_file", {"path": "README.md", "content": "x"}, ctx_ok
    )
    assert ok.get("ok") is True
    assert called["count"] == 1


@pytest.mark.asyncio
async def test_tool_executor_captures_exception_and_nested_failed_result_without_raw_leakage():
    async def _timeout_runner():
        raise TimeoutError("token=sk-live-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456")

    exc_out = await execute_tool("list_repo_files", _timeout_runner)
    assert exc_out["ok"] is False
    assert exc_out["llm_facing"] == "Tool list_repo_files could not complete."

    async def _nested_failure_runner():
        return {
            "ok": False,
            "error": "password=super-secret",
            "error_class": "auth",
            "system_signal": "github_auth_failed",
        }

    nested = await execute_tool("read_repo_file", _nested_failure_runner)
    assert nested["ok"] is False
    assert nested["error_class"] == "auth"
    assert nested["llm_facing"] == "Tool read_repo_file could not complete."


def test_failure_signature_generation_is_sanitized_and_idempotent():
    calls = [
        {"tool": "write_repo_file", "ok": False, "error": "token=sk-live-ABC", "error_class": "auth"},
        {"tool": "write_repo_file", "ok": False, "error": "token=sk-live-ABC", "error_class": "auth"},
    ]
    sigs = failure_signatures(calls)
    assert sigs == ["tool:write_repo_file:auth"]
    assert "sk-live" not in " ".join(sigs)


# -------------------- Gate wiring: blocking predeploy + reusable workflow -----

def test_interaction_gate_script_exists_and_is_blocking_in_predeploy():
    gate_path = ROOT / "scripts/interaction_gate.sh"
    assert os.path.exists(gate_path), "missing scripts/interaction_gate.sh"

    predeploy = (ROOT / "scripts/predeploy_gate.sh").read_text()
    assert "interaction_gate.sh" in predeploy, "predeploy gate must call interaction_gate.sh"
    # blocking: no graceful '|| echo' fallback on the interaction gate invocation line
    interaction_line = next((ln for ln in predeploy.splitlines() if "interaction_gate.sh" in ln), "")
    assert "||" not in interaction_line, "interaction gate must be blocking"


def test_interaction_reliability_workflow_exists_and_is_required_by_deploy_workflows():
    wf = ROOT / ".github/workflows/interaction-reliability.yml"
    assert os.path.exists(wf), "missing reusable workflow .github/workflows/interaction-reliability.yml"

    auto_deploy = (ROOT / ".github/workflows/auto_deploy.yml").read_text()
    deploy = (ROOT / ".github/workflows/deploy.yml").read_text()

    assert "interaction-reliability.yml" in auto_deploy
    assert "interaction-reliability.yml" in deploy
    import yaml
    for text, deploy_job in ((auto_deploy, "deploy-and-report"), (deploy, "deploy")):
        jobs = yaml.safe_load(text)["jobs"]
        assert jobs["interaction-reliability"]["uses"] == "./.github/workflows/interaction-reliability.yml"
        assert "interaction-reliability" in jobs[deploy_job]["needs"]
        assert jobs["interaction-reliability"].get("continue-on-error") is not True


def test_interaction_gate_fail_closed_control_stops_after_first_failure(tmp_path):
    # Execute the actual gate in an isolated checkout shape. A failing pytest
    # executable must prevent the frontend lane, not merely prove `set -e` works.
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (tmp_path / "backend").mkdir()
    (tmp_path / "frontend").mkdir()
    gate = scripts / "interaction_gate.sh"
    gate.write_text((ROOT / "scripts/interaction_gate.sh").read_text())
    bins = tmp_path / "bin"
    bins.mkdir()
    marker = tmp_path / "frontend_invoked"
    (bins / "python").write_text("#!/bin/sh\nexit 23\n")
    (bins / "yarn").write_text(f'#!/bin/sh\ntouch "{marker}"\n')
    for binary in bins.iterdir():
        binary.chmod(0o755)
    proc = subprocess.run(["bash", str(gate)], env={**os.environ, "PATH": f"{bins}:{os.environ['PATH']}"},
                          capture_output=True, text=True, timeout=15)
    assert proc.returncode == 23
    assert not marker.exists()
