"""Part XII group 3 — hallucination / citation / output guards (part12b).

Covers verify-part12-guards.md:
  a1  authoriz regex must not flag "authorized"
  a2  may/might hedge must exempt only the hedged clause
  a3  hallucination_guard.apply() must strip/block the claim
  b1  citation_guard must scan fenced content too
  b2  enforce() must rewrite/flag unverified counts/versions;
      L196-197 suppression removed
  b3  grounding_check must not count codebase_tree as grounding
  b4  fake/dir/grounding_check.py must land in FABRICATED
  c1  adversarial_review must fail CLOSED on errors/budget skip
  c2  faithfulness_judge must parse {/} in reasoning
  d1  output_guard must preserve Markdown indentation
  d2  incomplete_reply_guard must not invent a closing sentence
      for a trailing colon
  d3  internal_directive_guard must strip "Tell the user clearly:"

Negative control: PART12B_BACKEND=<pristine backend dir> pytest <file>
"""
import os
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

BACKEND = Path(os.environ.get(
    "PART12B_BACKEND", Path(__file__).resolve().parents[1]))
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def _stub(name, **attrs):
    if name in sys.modules:
        return sys.modules[name]
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


def _stub_pkg(name, path=None):
    if name in sys.modules:
        return sys.modules[name]
    m = types.ModuleType(name)
    m.__path__ = [str(path)] if path else []
    sys.modules[name] = m
    return m


@pytest.fixture(scope="module", autouse=True)
def _stubs():
    _stub_pkg("cto_services")
    _stub("cto_services.db", get_db=lambda: None, set_db=lambda db: None)
    _stub("services.ora_chat.safety", KNOWN_COMMANDS=("/read", "/scan", "/image"))
    _stub("services.local_tools",
          LOCAL_TOOLS={"read_repo_file", "read_repo_files", "list_repo_files"})
    _stub("services.task_chain_logging",
          get_turn_tool_records=lambda recs: {
              (r.get("tool") or r.get("name") or "") for r in (recs or [])})
    _stub_pkg("services.llm", BACKEND / "services" / "llm")
    yield


# ─────────────────────── (a) hallucination_guard ─────────────────
@pytest.fixture(scope="module")
def hg():
    import services.hallucination_guard as m
    return m


class TestAuthorizRegex:
    def test_authorized_not_flagged(self, hg):
        assert hg._detect_unsupported_claims("The PAT is authorized.") == []

    def test_unauthorized_flagged(self, hg):
        assert hg._detect_unsupported_claims("The PAT is unauthorized.") != []

    def test_401_authorized_not_flagged(self, hg):
        assert hg._detect_unsupported_claims("401 authorized") == []

    def test_401_unauthorized_flagged(self, hg):
        assert hg._detect_unsupported_claims("401 unauthorized") != []

    def test_appears_authorized_not_flagged(self, hg):
        assert hg._detect_unsupported_claims(
            "The PAT appears authorized.") == []


class TestHedgeScope:
    def test_hedge_on_remedy_does_not_exempt_claim(self, hg):
        flagged = hg._detect_unsupported_claims(
            "The PAT is unauthorized, it may need a fresh token.")
        assert flagged != []

    def test_hedge_after_claim_does_not_exempt(self, hg):
        flagged = hg._detect_unsupported_claims(
            "The PAT is unauthorized and I might check it later.")
        assert flagged != []

    def test_hedge_governing_claim_still_exempts(self, hg):
        assert hg._detect_unsupported_claims(
            "The PAT may be unauthorized.") == []

    def test_may_month_does_not_exempt(self, hg):
        flagged = hg._detect_unsupported_claims(
            "The PAT is unauthorized in May.")
        assert flagged != []


class TestApplyStrips:
    def test_apply_removes_flagged_sentence(self, hg):
        out, flagged = hg.apply("The PAT is unauthorized.", [])
        assert flagged != []
        assert "The PAT is unauthorized" not in out
        assert "Unverified status claim removed" in out
        assert "[ISSUE] Self-check" in out

    def test_apply_keeps_clean_text(self, hg):
        out, flagged = hg.apply("The fix is ready.", [])
        assert out == "The fix is ready."
        assert flagged == []

    def test_apply_keeps_supported_claim(self, hg):
        inv = [{"ok": False, "error": "401 unauthorized"}]
        out, flagged = hg.apply("The PAT is unauthorized.", inv)
        assert out == "The PAT is unauthorized."
        assert flagged == []

    def test_apply_keeps_surrounding_text(self, hg):
        out, flagged = hg.apply(
            "Done. The PAT is unauthorized. Let me know.", [])
        assert flagged != []
        assert "Done." in out
        assert "The PAT is unauthorized" not in out


# ─────────────────────── (b1/b2) citation_guard ──────────────────
@pytest.fixture(scope="module")
def cg():
    import services.citation_guard as m
    return m


class TestFencedClaims:
    def test_fenced_unread_path_is_unverified(self, cg):
        text = "Here is the file:\n```python\nsrc/fake_module.py\n```\n"
        claims = cg._extract_claims(text)
        assert "src/fake_module.py" in claims["paths"]
        report = cg.CitationGuard().verify(text, [])
        assert "src/fake_module.py" in report["unverified_paths"]

    def test_fenced_unread_count_and_version_claimed(self, cg):
        text = "```\nwe use v9.9.9 with 999 packages\n```\n"
        claims = cg._extract_claims(text)
        assert "v9.9.9" in claims["versions"]
        assert "999 packages" in claims["counts"]

    def test_fenced_relative_import_still_exempt(self, cg):
        # The 2026-02 incident: quoted file content containing a
        # relative import must NOT become a citation claim.
        text = ("```js\nimport('../../refresh-skills-plugin.js')\n```\n")
        claims = cg._extract_claims(text)
        assert claims["paths"] == []

    def test_prose_claim_still_found(self, cg):
        claims = cg._extract_claims("see src/fake_module.py for details")
        assert "src/fake_module.py" in claims["paths"]

    def test_unterminated_fence_still_scanned(self, cg):
        claims = cg._extract_claims("```python\nsrc/fake_module.py v9.9.9")
        assert "src/fake_module.py" in claims["paths"]


class TestVersionsCounts:
    def test_no_read_versions_counts_block(self, cg):
        report = cg.CitationGuard().verify(
            "We use v9.9.9 with 312 packages", [])
        assert report["pass"] is False
        assert report["unverified_versions"] == ["v9.9.9"]
        assert report["unverified_counts"] == ["312 packages"]

    def test_read_does_not_suppress_reporting(self, cg):
        tool_calls = [{"tool": "read_repo_file",
                       "args": {"path": "unrelated.py"}}]
        report = cg.CitationGuard().verify(
            "We use v9.9.9 with 312 packages", tool_calls)
        # Reported even though a (different) file was read — the old
        # `if not read else []` silenced these entirely.
        assert report["unverified_versions"] == ["v9.9.9"]
        assert report["unverified_counts"] == ["312 packages"]

    @pytest.mark.asyncio
    async def test_enforce_rewrites_unverified_numbers(self, cg):
        seen = {}

        async def fake_llm(*, original_messages=None,
                           additional_context=None, instruction=None, **kw):
            seen["context"] = additional_context or ""
            return "REWRITTEN without numbers"

        async def fake_read(ctx, args):
            return {"ok": False}

        out = await cg.CitationGuard().enforce(
            "We use v9.9.9 with 312 packages", [],
            ctx={}, llm_caller=fake_llm, original_messages=[],
            read_repo_file=fake_read)
        assert out["retried"] is True
        assert out["text"] == "REWRITTEN without numbers"
        assert "9.9.9" in seen["context"] and "312 packages" in seen["context"]
        assert "DROP" in seen["context"]

    @pytest.mark.asyncio
    async def test_enforce_still_fetches_unread_paths(self, cg):
        async def fake_llm(*, original_messages=None,
                           additional_context=None, instruction=None, **kw):
            return "REWRITTEN"

        async def fake_read(ctx, args):
            return {"ok": False}

        out = await cg.CitationGuard().enforce(
            "see src/real_module.py for details", [],
            ctx={}, llm_caller=fake_llm, original_messages=[],
            read_repo_file=fake_read)
        assert out["retried"] is True
        assert out["fetched"]["src/real_module.py"].startswith("FILE NOT FOUND")


# ─────────────────── (b3/b4) grounding_check ──────────────────────
CANONICAL = {
    "paths": {"backend/services/ora_chat/grounding_check.py",
              "backend/services/output_guard.py"},
    "basenames": {"grounding_check.py", "output_guard.py"},
    "defs": set(),
}


@pytest.fixture(scope="module")
def gc():
    import services.ora_chat.grounding_check as m
    return m


class TestGroundingTreeNotGrounding:
    def test_classify_trusts_the_contexts_it_is_given(self, gc):
        # classify_claims itself is a pure function: it trusts the
        # contexts it is GIVEN. The b3 fix is at the CALL SITES
        # (check_and_log / run_post_response_check, tested below),
        # which no longer pass the tree. This test pins that the
        # pure function is unchanged — tree text passed in still
        # grounds — so a future reader doesn't "fix" it twice.
        out = gc.classify_claims(
            ["backend/services/output_guard.py"], canonical=CANONICAL,
            turn_contexts=["backend/services/output_guard.py"])
        assert out["unverified"] == []

    @pytest.mark.asyncio
    async def test_check_and_log_tree_does_not_ground(self, gc):
        reply = "See backend/services/output_guard.py for details."
        out = await gc.check_and_log(
            user_id="u", session_id="s", query="q", reply=reply,
            route="general", retrieved_context="",
            codebase_tree="backend/services/output_guard.py\nbackend/x.py",
            system_highlights="")
        assert "backend/services/output_guard.py" in out["ungrounded"]

    @pytest.mark.asyncio
    async def test_check_and_log_retrieved_grounds(self, gc):
        reply = "See backend/services/output_guard.py for details."
        out = await gc.check_and_log(
            user_id="u", session_id="s", query="q", reply=reply,
            route="general",
            retrieved_context="### backend/services/output_guard.py\ncontent",
            codebase_tree="", system_highlights="")
        assert out["ungrounded"] == []

    @pytest.mark.asyncio
    async def test_post_response_tree_only_is_unverified(self, gc):
        reply = "See backend/services/output_guard.py for details."
        import services.ora_chat.codebase_index as _ci
        with patch.object(
                _ci, "canonical_paths",
                new=AsyncMock(return_value=CANONICAL)):
            out = await gc.run_post_response_check(
                user_id="u", session_id="s", query="q", reply=reply,
                route="general", retrieved_context="",
                codebase_tree="backend/services/output_guard.py",
                system_highlights="")
        assert "backend/services/output_guard.py" in out["unverified"]
        assert out["fabricated"] == []

    @pytest.mark.asyncio
    async def test_post_response_retrieved_is_clean(self, gc):
        reply = "See backend/services/output_guard.py for details."
        import services.ora_chat.codebase_index as _ci
        with patch.object(
                _ci, "canonical_paths",
                new=AsyncMock(return_value=CANONICAL)):
            out = await gc.run_post_response_check(
                user_id="u", session_id="s", query="q", reply=reply,
                route="general",
                retrieved_context="backend/services/output_guard.py",
                codebase_tree="", system_highlights="")
        assert out["unverified"] == [] and out["fabricated"] == []


class TestBasenameEvasion:
    def test_fake_dir_lands_in_fabricated(self, gc):
        out = gc.classify_claims(
            ["fake/dir/grounding_check.py"], canonical=CANONICAL,
            turn_contexts=[])
        assert out["fabricated"] == ["fake/dir/grounding_check.py"]

    def test_real_path_is_unverified_not_fabricated(self, gc):
        out = gc.classify_claims(
            ["backend/services/output_guard.py"], canonical=CANONICAL,
            turn_contexts=[])
        assert out["fabricated"] == []
        assert out["unverified"] == ["backend/services/output_guard.py"]

    def test_bare_filename_still_resolves(self, gc):
        out = gc.classify_claims(
            ["output_guard.py"], canonical=CANONICAL, turn_contexts=[])
        assert out["fabricated"] == []
        assert out["unverified"] == ["output_guard.py"]

    def test_totally_invented_is_fabricated(self, gc):
        out = gc.classify_claims(
            ["totally_made_up_xyz.py"], canonical=CANONICAL, turn_contexts=[])
        assert out["fabricated"] == ["totally_made_up_xyz.py"]

    @pytest.mark.asyncio
    async def test_fake_dir_line_claim_is_unverified(self, gc):
        reply = "See fake/dir/grounding_check.py line 42 for the check."
        import services.ora_chat.codebase_index as _ci
        with patch.object(
                _ci, "canonical_paths",
                new=AsyncMock(return_value=CANONICAL)):
            out = await gc.run_post_response_check(
                user_id="u", session_id="s", query="q", reply=reply,
                route="general",
                retrieved_context="backend/services/ora_chat/grounding_check.py",
                codebase_tree="", system_highlights="")
        assert "fake/dir/grounding_check.py" in out["fabricated"]
        assert "fake/dir/grounding_check.py:L42" in out["unverified"]


# ─────────────────── (c1) adversarial_review ──────────────────────
@pytest.fixture(scope="module")
def ar():
    _stub("services.ora_chat.cost_tracker",
          budget_status=AsyncMock(return_value={"day_cap_usd": 2.5,
                                                "day_spent_usd": 0.10,
                                                "mode": "normal"}),
          log_call=AsyncMock(return_value=0.001))
    _stub("services.ora_chat.providers",
          one_shot=AsyncMock(return_value=("", {}, None)))
    import services.ora_chat.adversarial_review as m
    return m


DRAFT = "Humara caching cache_orchestrator.py handle karta hai."
_BUDGET_OK = AsyncMock(return_value={"day_cap_usd": 2.5,
                                     "day_spent_usd": 0.10,
                                     "mode": "normal"})
_BUDGET_NEAR_CAP = AsyncMock(return_value={"day_cap_usd": 2.5,
                                           "day_spent_usd": 2.20,
                                           "mode": "warning"})


class TestReviewFailsClosed:
    @pytest.mark.asyncio
    async def test_budget_skip_passed_is_false(self, ar):
        one = AsyncMock()
        with patch.object(ar.cost_tracker, "budget_status", new=_BUDGET_NEAR_CAP), \
             patch.object(ar, "one_shot", new=one):
            out = await ar.run_review(user_id="u", session_id="s",
                                      query="q", draft=DRAFT, context="ctx")
        assert out["skipped"] == "review_skipped_budget"
        assert out["passed"] is False
        one.assert_not_called()

    @pytest.mark.asyncio
    async def test_reviewer_error_passed_is_false(self, ar):
        with patch.object(ar.cost_tracker, "budget_status", new=_BUDGET_OK), \
             patch.object(ar, "one_shot",
                          new=AsyncMock(return_value=("", {}, "http_500"))):
            out = await ar.run_review(user_id="u", session_id="s",
                                      query="q", draft=DRAFT, context="ctx")
        assert out["skipped"].startswith("reviewer_error")
        assert out["passed"] is False

    @pytest.mark.asyncio
    async def test_unparseable_passed_is_false(self, ar):
        with patch.object(ar.cost_tracker, "budget_status", new=_BUDGET_OK), \
             patch.object(ar, "one_shot",
                          new=AsyncMock(return_value=("not json", {}, None))):
            out = await ar.run_review(user_id="u", session_id="s",
                                      query="q", draft=DRAFT, context="ctx")
        assert out["skipped"] == "reviewer_unparseable"
        assert out["passed"] is False

    @pytest.mark.asyncio
    async def test_empty_draft_passed_is_false(self, ar):
        out = await ar.run_review(user_id="u", session_id="s",
                                  query="q", draft="  ", context="ctx")
        assert out["skipped"] == "empty_draft"
        assert out["passed"] is False

    @pytest.mark.asyncio
    async def test_real_pass_still_passes(self, ar):
        with patch.object(ar.cost_tracker, "budget_status", new=_BUDGET_OK), \
             patch.object(ar.cost_tracker, "log_call",
                          new=AsyncMock(return_value=0.001)), \
             patch.object(ar, "one_shot",
                          new=AsyncMock(return_value=('{"result":"PASS"}',
                                                       {"input_tokens": 10,
                                                        "output_tokens": 5},
                                                       None))):
            out = await ar.run_review(user_id="u", session_id="s",
                                      query="q", draft=DRAFT, context="ctx")
        assert out["passed"] is True and out["flags"] == []


# ─────────────────── (c2) faithfulness_judge ──────────────────────
@pytest.fixture(scope="module")
def fj():
    import services.faithfulness_judge as m
    return m


class TestBraceAwareParsing:
    def test_clean_payload(self, fj):
        j = fj._extract_verdict_json(
            '{"verdict": "faithful", "unsupported_claims": [], '
            '"reasoning": "all claims match"}')
        assert j and j["verdict"] == "faithful"

    def test_braces_in_reasoning(self, fj):
        j = fj._extract_verdict_json(
            '{"verdict": "faithful", "unsupported_claims": [], '
            '"reasoning": "matches {a, b} sets"}')
        assert j and j["verdict"] == "faithful"

    def test_braces_in_claims(self, fj):
        j = fj._extract_verdict_json(
            '{"verdict": "unfaithful", '
            '"unsupported_claims": ["claims {x} exists"], '
            '"reasoning": "invented"}')
        assert j and j["verdict"] == "unfaithful"
        assert j["unsupported_claims"] == ["claims {x} exists"]

    def test_nested_object_in_reasoning(self, fj):
        j = fj._extract_verdict_json(
            '{"verdict": "faithful", "unsupported_claims": [], '
            '"reasoning": "see {\\"k\\": 1} for detail"}')
        assert j and j["verdict"] == "faithful"

    def test_fenced_payload_with_braces(self, fj):
        j = fj._extract_verdict_json(
            '```json\n{"verdict": "faithful", "unsupported_claims": [], '
            '"reasoning": "matches {a, b}"}\n```')
        assert j and j["verdict"] == "faithful"

    def test_prose_before_json(self, fj):
        j = fj._extract_verdict_json(
            'Here is my verdict: {"verdict": "faithful", '
            '"unsupported_claims": [], "reasoning": "ok {x}"}')
        assert j and j["verdict"] == "faithful"

    def test_garbage_is_none(self, fj):
        assert fj._extract_verdict_json("the draft looks bad overall") is None
        assert fj._extract_verdict_json("") is None


# ─────────────────────── (d1) output_guard ───────────────────────
@pytest.fixture(scope="module")
def og():
    import services.output_guard as m
    return m


class TestIndentationPreserved:
    TEXT = ("- item\n  - nested item\n    - deep\n```python\ndef f():\n"
            "    return 1\n```\nA  double  space.")

    def test_nested_list_and_code_keep_indentation(self, og):
        out, _ = og.strip_machinery_leak(self.TEXT)
        assert "  - nested item" in out
        assert "    - deep" in out
        assert "    return 1" in out

    def test_midline_double_space_still_collapsed(self, og):
        out, _ = og.strip_machinery_leak(self.TEXT)
        assert "A double space." in out

    def test_universal_only_path_same(self, og):
        out, _ = og.strip_machinery_leak(self.TEXT, universal_only=True)
        assert "  - nested item" in out
        assert "    return 1" in out


# ───────────────── (d2) incomplete_reply_guard ────────────────────
@pytest.fixture(scope="module")
def irg():
    import services.incomplete_reply_guard as m
    return m


class TestTrailingColon:
    def test_list_intro_colon_unchanged(self, irg):
        assert irg.ensure_complete("Here's what I found:") == "Here's what I found:"
        assert irg.is_incomplete("Here's what I found:") is False

    def test_other_colon_unchanged(self, irg):
        assert irg.ensure_complete("The fix is ready:") == "The fix is ready:"

    def test_dangling_phrase_still_completed(self, irg):
        out = irg.ensure_complete("Let me check the logs")
        assert out.endswith("That's the main point. Want the full details?")

    def test_complete_reply_unchanged(self, irg):
        assert irg.ensure_complete("Done.") == "Done."

    def test_ellipsis_still_incomplete(self, irg):
        assert irg.is_incomplete("Working on it...") is True


# ──────────────── (d3) internal_directive_guard ───────────────────
@pytest.fixture(scope="module")
def idg():
    import services.internal_directive_guard as m
    return m


REVOKED_ERROR = (
    "OAuth/PAT token expired. STOP calling repo tools — they will all "
    "fail the same way until access is restored. Tell the user clearly: "
    "'GitHub access to foo/bar was revoked — reconnect your GitHub "
    "account (or refresh the token) to keep working.' Do not guess at "
    "file paths or blame an unrelated API."
)


class TestDirectiveUnwrap:
    def test_prefix_stripped_message_kept(self, idg):
        out = idg.strip_leaked_tool_directives(REVOKED_ERROR)
        assert "Tell the user clearly:" not in out
        assert "STOP calling repo tools" not in out
        assert "OAuth/PAT token expired" not in out
        assert "Do not guess at file paths" not in out
        assert "GitHub access to foo/bar was revoked" in out
        assert "reconnect your GitHub account" in out

    def test_no_leaked_framing_left(self, idg):
        out = idg.strip_leaked_tool_directives(REVOKED_ERROR)
        assert "Tell the user" not in out

    def test_apostrophe_message_not_truncated(self, idg):
        text = "Tell the user clearly: 'Can't connect right now.'"
        out = idg.strip_leaked_tool_directives(text)
        assert "Tell the user clearly:" not in out
        assert "Can't connect right now." in out

    def test_clean_text_unchanged(self, idg):
        text = "The fix is ready. Please review it."
        assert idg.strip_leaked_tool_directives(text) == text
