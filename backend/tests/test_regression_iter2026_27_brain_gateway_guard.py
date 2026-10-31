"""tests/test_regression_iter2026_27_brain_gateway_guard.py

ORA Brain Part 3, Phase F — guard test + architectural invariants.

1. Behavioral proof that claim_recording actually de-duplicates a
   real Parliament call (which internally calls call_llm_with_meta)
   so it produces exactly ONE brain_calls row, not two.
2. Source-level allow-list: which files call a provider-transport
   function (`_call_claude`/`_call_deepseek`/`_call_glm`/
   `_call_longcat`/`call_openrouter_model`) directly. The 3 main
   entry points (expected — they ARE the transport layer), the 7
   side-door files Part 3's own spec names (log-only, intentionally
   NOT wrapped), and — as of Part 4, F5 — the 7 files this audit
   found calling a provider directly WITHOUT going through the
   gateway at all (`_RECORD_WRAPPED_CALLERS`). Those 7 are no
   longer an unwrapped gap: each one now routes its call through
   `services.brain_gateway.record_wrap` (verified below, not just
   allow-listed) for record-only visibility, with zero routing
   change. Anything else matching the pattern — i.e. truly
   unwrapped — fails the guard.
3. services/ora_client.py (founder-only aurem.live client) is
   untouched by this change and still states its founder-only
   contract.
4. None of Part 3's new/modified files reference Qwen or a
   local/self-trained ORA model.
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

import core.parliament.llm_call as llm_call_mod
import services.brain_gateway as bg
import services.llm as sl
import services.llm._meta as meta_mod

pytestmark = pytest.mark.asyncio

_BACKEND = Path(__file__).resolve().parent.parent

_PROVIDER_CALL_RE = re.compile(
    r"\b_call_claude\(|\b_call_deepseek\(|\b_call_glm\(|\b_call_longcat\(|\bcall_openrouter_model\(",
)

# The 3 main entry points — calling the transport layer here IS the
# transport layer itself, not a bypass.
_MAIN_ENTRY_POINT_FILES = {
    "services/llm/_meta.py",
    "services/llm/__init__.py",
    # core/parliament/llm_call.py calls call_llm_with_meta, not the
    # raw transports, so it is NOT expected to match this regex —
    # listed anyway for documentation completeness.
    "core/parliament/llm_call.py",
    # These two are the TRANSPORT LAYER ITSELF — where
    # call_openrouter_model / _call_claude / _call_deepseek / _call_glm
    # / _call_longcat are actually implemented (one calling another as
    # part of the same fallback chain). Matching here is the
    # implementation, not a bypass of it.
    "services/llm/openrouter_client.py",
    "services/llm/openrouter_providers.py",
}

# Side doors explicitly named in Part 3's own VERIFY FIRST section —
# log-only / no context injection, intentionally NOT wrapped by the
# gateway in this step.
_NAMED_SIDE_DOORS = {
    "services/ora_chat/providers.py",
    "services/ora_chat_v2/llm_client.py",
    "services/llm/groq_client.py",
    "services/llm/jev_client.py",
    "services/vision_describe.py",
    "services/advisor_vision.py",
    "routers/admin_bin.py",
}

# Part 4, F5 — these 7 files were found calling a provider-transport
# function directly, bypassing the gateway entirely (Part 3's VERIFY
# FIRST section didn't name them). They are no longer an "unwrapped"
# gap: each now routes its call through
# services.brain_gateway.record_wrap (record-only, no routing change,
# fail-open — see that function's docstring). Allow-listed here
# because they still textually match `_PROVIDER_CALL_RE` (the actual
# call is now an argument to record_wrap, not a bypass of it) AND
# positively verified below to actually call record_wrap, not just
# assumed.
_RECORD_WRAPPED_CALLERS = {
    "routers/chat/misc.py",
    "routers/chat/worker.py",
    "services/agents.py",
    "services/graph_builder.py",
    "services/vanguard_verify_agent.py",
    "services/video_pipeline/house_rules_check.py",
    "services/web_inspect.py",
}

# The "truly unwrapped, no gateway involvement at all" bucket — empty
# as of Part 4, F5. Keep this named and asserted-empty (rather than
# deleting it) so a future regression that adds a NEW unwrapped
# caller is forced to explain itself here, not silently re-grow this
# set.
_UNWRAPPED_CALLERS: set[str] = set()

_ALLOWED_CALLERS = (
    _MAIN_ENTRY_POINT_FILES | _NAMED_SIDE_DOORS
    | _RECORD_WRAPPED_CALLERS | _UNWRAPPED_CALLERS
)

_NEW_OR_MODIFIED_BRAIN_FILES = (
    "services/brain_gateway.py",
    "services/knowledge_injector.py",
    "services/project_memory.py",
    "routers/project_memory.py",
    "routers/brain_admin.py",
    "services/llm/_meta.py",
    "services/llm/__init__.py",
    "core/parliament/llm_call.py",
)


def _find_direct_provider_callers() -> set[str]:
    hits = set()
    for py_file in _BACKEND.rglob("*.py"):
        if "/tests/" in str(py_file) or py_file.name.startswith("test_"):
            continue
        try:
            text = py_file.read_text(encoding="utf-8")
        except Exception as e:                                  # noqa: BLE001
            logger.debug("guard scan skipped %s: %r", py_file, e)
            continue
        if _PROVIDER_CALL_RE.search(text):
            hits.add(str(py_file.relative_to(_BACKEND)))
    return hits


class TestNoUnlistedProviderBypass:
    def test_every_direct_caller_is_on_the_allow_list(self):
        hits = _find_direct_provider_callers()
        unlisted = hits - _ALLOWED_CALLERS
        assert not unlisted, (
            f"New/unlisted file(s) call a provider directly, bypassing "
            f"the gateway: {sorted(unlisted)}"
        )

    def test_unwrapped_callers_bucket_is_empty(self):
        """Part 4, F5's explicit DONE criterion: the allow-list for
        truly-unwrapped (no gateway involvement at all) callers is
        EMPTY. If this ever fails, a new caller was added to
        _UNWRAPPED_CALLERS without wrapping it — don't just grow the
        set, wrap the call with record_wrap or write a reason here."""
        assert _UNWRAPPED_CALLERS == set()

    def test_every_record_wrapped_caller_actually_calls_record_wrap(self):
        """Not just allow-listed — positively verified each of the 7
        files found bypassing the gateway now routes its call
        through the shared helper."""
        for rel in _RECORD_WRAPPED_CALLERS:
            text = (_BACKEND / rel).read_text(encoding="utf-8")
            assert "record_wrap" in text, f"{rel} is allow-listed as record-wrapped but never calls record_wrap"

    def test_allow_list_entries_still_actually_exist(self):
        """The allow-list itself must stay honest — if a side-door
        file is deleted/renamed, its entry should be removed too,
        not linger as dead documentation."""
        for rel in _NAMED_SIDE_DOORS | _RECORD_WRAPPED_CALLERS:
            assert (_BACKEND / rel).exists(), f"allow-listed file missing: {rel}"


class TestOraClientFounderOnlyUnchanged:
    def test_ora_client_still_states_founder_only_contract(self):
        text = (_BACKEND / "services/ora_client.py").read_text(encoding="utf-8")
        assert "Founder-only" in text
        assert "FOUNDER_EMAILS" in text

    def test_part3_did_not_touch_ora_client(self):
        """Regression guard, not a new audit — Part 3 never imports
        or modifies services/ora_client.py."""
        for rel in _NEW_OR_MODIFIED_BRAIN_FILES:
            text = (_BACKEND / rel).read_text(encoding="utf-8")
            assert "ora_client" not in text


class TestNoLocalOrTrainedModelRouting:
    def test_no_qwen_or_local_model_references_in_brain_files(self):
        for rel in _NEW_OR_MODIFIED_BRAIN_FILES:
            text = (_BACKEND / rel).read_text(encoding="utf-8").lower()
            assert "qwen" not in text
            assert "own-trained" not in text and "own_trained" not in text
            assert "trained ora model" not in text


# ──────────────────── behavioral dedup proof (not grep) ──────────────────
class TestRecordedOnceAcrossNestedEntryPoints:
    @pytest.fixture(autouse=True)
    async def _cancel_worker_after(self):
        # Part 4, F1 — record_call() self-heals a dead worker, so
        # every test below (all drive a REAL entry point) spawns a
        # detached background task. Cancel it so it never outlives
        # this test's event loop.
        yield
        task = bg._worker_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def test_parliament_call_produces_exactly_one_brain_calls_row(self, monkeypatch):
        """_llm_call_protected calls services.llm.call_llm_with_meta
        internally. Without claim_recording, that would be 2 rows for
        1 logical call. Exercise the REAL wired code (not a grep) and
        assert exactly 1 row ends up queued."""
        monkeypatch.setattr(bg, "_write_queue", None)
        monkeypatch.setattr(bg, "_worker_task", None)

        async def _fake_impl(system, user, max_tokens=1500, mode="chat",
                              user_id=None, review_mode=None, step_hook=None,
                              feature=None):
            return {"ok": True, "content": "hello world", "provider": "fake",
                    "model": "fake-model", "total_tokens": 42}
        monkeypatch.setattr(meta_mod, "_call_llm_with_meta_impl", _fake_impl)

        content, _latency_ms, err = await llm_call_mod._llm_call_protected(
            system="sys", user="usr", max_tokens=100, mode="code",
            review_mode="swift", user_id="u1",
        )
        assert err is None
        assert content == "hello world"
        assert bg._write_queue is not None
        assert bg._write_queue.qsize() == 1
        row = await bg._write_queue.get()
        assert row["surface"] == "parliament"
        assert row["model"] == "fake-model"
        assert row["tokens"] == 42

    async def test_direct_orchestrator_call_without_parliament_also_records_once(self, monkeypatch):
        monkeypatch.setattr(bg, "_write_queue", None)
        monkeypatch.setattr(bg, "_worker_task", None)

        async def _fake_impl(system, user, max_tokens=1500, mode="chat",
                              user_id=None, review_mode=None, step_hook=None,
                              feature=None):
            return {"ok": True, "content": "direct reply", "provider": "fake2"}
        monkeypatch.setattr(meta_mod, "_call_llm_with_meta_impl", _fake_impl)

        result = await sl.call_llm_with_meta("sys", "usr", mode="chat", user_id="u2")
        assert result["content"] == "direct reply"
        assert bg._write_queue.qsize() == 1
        row = await bg._write_queue.get()
        assert row["surface"] == "orchestrator"

    async def test_gateway_failure_never_breaks_the_real_llm_call(self, monkeypatch):
        """Even if record_call itself is broken, the LLM call's own
        result must still come back unaffected."""
        monkeypatch.setattr(bg, "_write_queue", None)
        monkeypatch.setattr(bg, "_worker_task", None)

        async def _broken_record_call(**kwargs):
            raise RuntimeError("gateway is on fire")
        monkeypatch.setattr(bg, "record_call", _broken_record_call)

        async def _fake_impl(system, user, max_tokens=1500, mode="chat",
                              user_id=None, review_mode=None, step_hook=None,
                              feature=None):
            return {"ok": True, "content": "still works", "provider": "fake3"}
        monkeypatch.setattr(meta_mod, "_call_llm_with_meta_impl", _fake_impl)

        result = await sl.call_llm_with_meta("sys", "usr", mode="chat", user_id="u3")
        assert result["content"] == "still works"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
