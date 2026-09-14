"""
tests/test_2026_02_required_tool_gate_b3.py — Part B3 regression test.

Reproduces the EXACT reported incident: "Fix this critical issue:
exec_usage (in .agent/skills/plugins/antigravity-awesome-skills-claude/
skills/007/scripts/scanners/injection_scanner.py)" — confirms the
deterministic gate now fires and forces a real read_repo_file call
before any LLM response, instead of the old behavior (asking "what
should change").
"""
import pytest

from services.required_tool_gate import detect_required_read, build_forced_read_context

REAL_PATH = (
    ".agent/skills/plugins/antigravity-awesome-skills-claude/"
    "skills/007/scripts/scanners/injection_scanner.py"
)
INCIDENT_MESSAGE = f"Fix this critical issue: exec_usage (in {REAL_PATH})"


def test_exact_incident_message_is_detected():
    hit = detect_required_read(INCIDENT_MESSAGE)
    assert hit is not None
    assert hit["rule_id"].lower() == "exec_usage"
    assert hit["path"] == REAL_PATH


def test_path_only_no_rule_id_does_not_match():
    assert detect_required_read(f"can you clean up {REAL_PATH}") is None


def test_rule_id_only_no_path_does_not_match():
    assert detect_required_read("what's exec_usage again?") is None


def test_generic_vague_security_mention_does_not_match():
    # per spec: "there's a security issue somewhere" must NEVER match —
    # this gate is narrow by design, not a general NLP classifier.
    assert detect_required_read("there's a security issue somewhere in the app") is None


@pytest.mark.asyncio
async def test_forced_read_context_calls_real_read_repo_file_and_injects_content(monkeypatch):
    calls = []

    async def fake_read_repo_file(ctx, args):
        calls.append(args["path"])
        return {"ok": True, "content": "exec() usage -- verify input is not user-controlled"}

    import services.local_tools as local_tools_mod
    monkeypatch.setattr(local_tools_mod, "read_repo_file", fake_read_repo_file)

    block = await build_forced_read_context(message=INCIDENT_MESSAGE, ctx={})
    assert calls == [REAL_PATH]  # read_repo_file WAS actually called — this is the core fix
    assert "REQUIRED READ" in block
    assert "exec_usage" in block
    assert "verify input is not user-controlled" in block  # real content present, not fabricated


@pytest.mark.asyncio
async def test_forced_read_context_empty_when_gate_does_not_apply():
    block = await build_forced_read_context(message="hey how's it going", ctx={})
    assert block == ""


@pytest.mark.asyncio
async def test_forced_read_context_reports_real_failure_not_silent(monkeypatch):
    async def fake_read_repo_file(ctx, args):
        return {"ok": False, "error": "file_not_found"}

    import services.local_tools as local_tools_mod
    monkeypatch.setattr(local_tools_mod, "read_repo_file", fake_read_repo_file)

    block = await build_forced_read_context(message=INCIDENT_MESSAGE, ctx={})
    assert "READ FAILED" in block or "failed" in block.lower()
    assert "file_not_found" in block


@pytest.mark.asyncio
async def test_fallback_response_never_leaks_internal_retry_directive(monkeypatch):
    """2026-02 round-4 — confirmed LIVE (founder QA report, Bug 3):
    the deterministic fallback_response for a not-found read embedded
    local_tools.py's raw error verbatim, including the internal-only
    "STOP guessing paths. Your next tool call MUST be list_repo_files
    ... do not cite any file paths — until you have called
    list_repo_files and seen the actual layout." directive — meant
    ONLY to steer the model's OWN next tool call, never the user. This
    fallback_response is this module's OWN deterministic text (not
    LLM-generated) and runs AFTER apply_output_guards in turn.py/
    stream.py, so services/internal_directive_guard.py's identical
    strip (wired into apply_output_guards) never got a chance to run
    on it. Fixed by applying the same strip at the source."""
    from services.required_tool_gate import run_required_read_gate

    async def fake_read_repo_file(ctx, args):
        return {
            "ok": False,
            "error": (
                f"FILE NOT FOUND: `{REAL_PATH}` does not exist on "
                "owner/repo@main. STOP guessing paths. Your next tool "
                "call MUST be `list_repo_files` with a glob (e.g. "
                "`**/auth*.py`, `**/*router*.py`) to DISCOVER the real "
                "paths in this repo. Do not write a plan, do not "
                "produce a handoff brief, do not cite any file paths "
                "— until you have called list_repo_files and seen the "
                "actual layout."
            ),
        }

    import services.local_tools as local_tools_mod
    monkeypatch.setattr(local_tools_mod, "read_repo_file", fake_read_repo_file)

    gate = await run_required_read_gate(
        message=INCIDENT_MESSAGE, ctx={"user_id": "u1", "project_id": "p1", "bin_ctx": None},
    )
    reply = gate["fallback_response"]
    assert "STOP guessing paths" not in reply
    assert "list_repo_files" not in reply
    assert "do not cite any file paths" not in reply
    # The real, informative part of the error survives.
    assert "FILE NOT FOUND" in reply
    assert REAL_PATH in reply


# ── integration: wired into the REAL resolve_pre_llm pipeline ──────
# Confirms this isn't just a standalone unit that nothing calls —
# both /chat/send (turn.py) and /chat/stream (worker.py) route through
# resolve_pre_llm(), and PreLLMOutcome.forced_read_context is now the
# field both call sites append into extra_sys before chat_with_tools.
@pytest.mark.asyncio
async def test_resolve_pre_llm_populates_forced_read_context_on_the_real_incident(monkeypatch):
    from routers.chat_pre_llm import resolve_pre_llm, PreLLMOutcome

    class _Body:
        def __init__(self, prompt):
            self.prompt = prompt
            self.session_id = "s-b3-test"
            self.project_id = None
            self.ora_panel = False

    async def _classify(*a, **kw):
        return {"tier": "agentic", "confidence": 0.9}
    monkeypatch.setattr("core.intent_gateway.classify", _classify)

    calls = []
    async def fake_read_repo_file(ctx, args):
        calls.append(args["path"])
        return {"ok": True, "content": "exec() usage -- verify input is not user-controlled"}
    import services.local_tools as local_tools_mod
    monkeypatch.setattr(local_tools_mod, "read_repo_file", fake_read_repo_file)

    outcome = await resolve_pre_llm(
        db=None, user={"user_id": "u1", "tier": "free"},
        body=_Body(INCIDENT_MESSAGE), bin_ctx=None,
        prior_fix_signal=False, prior_turn_text="", session_summary="",
        allowed_modes=["swift", "pro"], req_mode="swift",
        run_confirm_boundary=False,
    )
    assert isinstance(outcome, PreLLMOutcome)
    assert calls == [REAL_PATH]
    assert "REQUIRED READ" in outcome.forced_read_context
    assert "exec_usage" in outcome.forced_read_context


@pytest.mark.asyncio
async def test_resolve_pre_llm_forced_read_context_empty_for_ordinary_message(monkeypatch):
    from routers.chat_pre_llm import resolve_pre_llm

    class _Body:
        def __init__(self, prompt):
            self.prompt = prompt
            self.session_id = "s-b3-test-2"
            self.project_id = None
            self.ora_panel = False

    async def _classify(*a, **kw):
        return {"tier": "agentic", "confidence": 0.9}
    monkeypatch.setattr("core.intent_gateway.classify", _classify)

    outcome = await resolve_pre_llm(
        db=None, user={"user_id": "u1", "tier": "free"},
        body=_Body("build me a login page"), bin_ctx=None,
        prior_fix_signal=False, prior_turn_text="", session_summary="",
        allowed_modes=["swift", "pro"], req_mode="swift",
        run_confirm_boundary=False,
    )
    assert outcome.forced_read_context == ""


@pytest.mark.asyncio
async def test_resolve_pre_llm_overrides_a_clarify_tier_misclassification(monkeypatch):
    """The REAL root cause found live: the intent classifier
    misclassified the exact incident message as tier="clarify",
    which short-circuits BEFORE chat_with_tools ever runs — bypassing
    forced_read_context entirely. A message with a real rule-id AND a
    real path must never be allowed into the clarify short-circuit,
    regardless of what the classifier guessed."""
    from routers.chat_pre_llm import resolve_pre_llm

    class _Body:
        def __init__(self, prompt):
            self.prompt = prompt
            self.session_id = "s-b3-clarify-override"
            self.project_id = None
            self.ora_panel = False

    async def _classify(*a, **kw):
        return {"tier": "clarify", "confidence": 0.3}  # the real misclassification
    monkeypatch.setattr("core.intent_gateway.classify", _classify)

    async def fake_read_repo_file(ctx, args):
        return {"ok": True, "content": "exec() usage -- verify input is not user-controlled"}
    import services.local_tools as local_tools_mod
    monkeypatch.setattr(local_tools_mod, "read_repo_file", fake_read_repo_file)

    outcome = await resolve_pre_llm(
        db=None, user={"user_id": "u1", "tier": "free"},
        body=_Body(INCIDENT_MESSAGE), bin_ctx=None,
        prior_fix_signal=False, prior_turn_text="", session_summary="",
        allowed_modes=["swift", "pro"], req_mode="swift",
        run_confirm_boundary=False,
    )
    # result must be None (fell through to the real chat_with_tools
    # path) — NOT a canned "clarify" short-circuit reply.
    assert outcome.result is None
    assert "REQUIRED READ" in outcome.forced_read_context
    assert outcome.required_read_gate is not None


def test_enforce_required_read_gate_overrides_a_generic_clarifying_question():
    from services.required_tool_gate import run_required_read_gate, enforce_required_read_gate
    import asyncio

    async def fake_read_repo_file(ctx, args):
        return {"ok": False, "error": "No project selected."}
    import services.local_tools as local_tools_mod
    orig = local_tools_mod.read_repo_file
    local_tools_mod.read_repo_file = fake_read_repo_file
    try:
        gate = asyncio.get_event_loop().run_until_complete(
            run_required_read_gate(message=INCIDENT_MESSAGE, ctx={})
        )
    finally:
        local_tools_mod.read_repo_file = orig

    # Simulates a LATER guard (e.g. bail_reason.py) overwriting the
    # LLM's honest reply with a generic canned clarifying question —
    # the exact live-confirmed failure mode.
    canned = 'Just to make sure I get this right — what exactly should change, and where?'
    fixed = enforce_required_read_gate(gate, canned)
    assert fixed != canned
    assert "exec_usage" in fixed
    assert ".agent/skills/plugins" in fixed

    # A response that DID honor the gate must be left untouched.
    # 2026-02 round-4 — path is now REQUIRED verbatim (rule-id alone
    # is not proof the real path survived downstream filters
    # unmangled; see enforce_required_read_gate docstring).
    honest = (
        "I read .agent/skills/plugins/antigravity-awesome-skills-claude/"
        "skills/007/scripts/scanners/injection_scanner.py for exec_usage "
        "and it's a false positive."
    )
    assert enforce_required_read_gate(gate, honest) == honest

    # A response that mentions the rule-id but has its path token
    # mangled by a later filter (confirmed live, round 3) must still
    # be overridden — this is the actual bug this round fixes.
    mangled = "The read for rule exec_usage at path .that page failed."
    fixed_mangled = enforce_required_read_gate(gate, mangled)
    assert fixed_mangled != mangled
    assert ".agent/skills/plugins" in fixed_mangled
