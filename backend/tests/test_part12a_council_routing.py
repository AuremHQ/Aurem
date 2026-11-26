"""
Part XII group 1 fixes (ORA Council & Routing) — regression tests.

Proves each CONFIRMED bug from verify-part12-council-routing.md is
closed (base: main 097697ba). Every test below FAILS on the pristine
seed and PASSES on the fixed tree (negative controls were run against
the pristine copy — see FIX-REPORT.md).

  (a)  routing.py — dead keyword routing: B/C keywords now exist and
       route analysis/writing tasks to B/C; word-boundary matching so
       "fix" in "prefix" / "test" in "latest" cannot hijack a route.
  (b1/b2) parliament.py — _fallback_single_call no longer hardcodes
       the Council A persona / mode="code" / task_type="code_fix".
  (b3) parliament.py — _log_event holds its asyncio task in a
       module-level set until done (tracked + failure logged).
  (c)  councils.py — gather(return_exceptions=True): one member
       raising cannot abort the whole vote.
  (d1) ceo.py — judge excerpt is head+tail, so a broken tail is
       visible to the judge.
  (d2) scoring.py — _score_output dispatches all 12 routed task
       types; no more flat-0.65 auto-accept; unknown types go to the
       judge (0.50 < floor 0.55).
  (d3) scoring.py — word-boundary signal matching in
       detect_output_type / _score_analysis / _score_writing.
  (e1) ora_council_retriever.py — mode-C rows with no pass_result
       fail closed (excluded).
  (e2) ora_council_retriever.py — _row_from_log tokenizes the FULL
       message; the 280-char cap applies to the stored display
       string only, never to the indexed terms.

Runs against the real `core.parliament` / `services` modules (only
the LLM calls are stubbed). NOT covered here, deliberately:
  (e3a) L3-on-empty-query is CONFIRMED but DELIBERATE per the module
       docstring ("at every return path") — no change, no test.
  (e3b) the L202-205 citation was WRONG-or-STALE — nothing to build.
"""
import asyncio
import logging
import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core.parliament.routing import TaskRouter  # noqa: E402
from core.parliament.scoring import (  # noqa: E402
    _score_output, _score_analysis, _score_writing, detect_output_type,
)
from core.parliament.councils import (  # noqa: E402
    _Council, _COUNCIL_A_PERSONA, _COUNCIL_B_PERSONAS, _COUNCIL_C_PERSONAS,
)
from core.parliament.ceo import CEO  # noqa: E402
from core.parliament import parliament as parl_mod  # noqa: E402
from core.parliament import llm_call as llm_call_mod  # noqa: E402
from core.parliament import ceo as ceo_mod  # noqa: E402
from services.ora_council_retriever import (  # noqa: E402
    _quality_filter, _row_from_log,
)

VALID_PY = (
    "import os\nfrom pathlib import Path\n\n"
    "class FixedThing:\n    def run(self):\n"
    "        return {\n            \"key\": \"value\",\n"
    "            \"other\": \"x\",\n"
    "        }\n"
)

# Analysis content scoring above the CEO floor via _score_analysis
# (numbers + structure + actionable conclusion + >= 50 words).
ANALYSIS_TEXT = (
    "## Analysis\n\n"
    "1. First finding: 42% of requests failed in the last 7 days, "
    "with 18 errors per hour at peak load across 3 regions.\n\n"
    "2. Second finding: latency rose 25% after the deploy on Monday, "
    "and 9 of 12 slow traces point at the same database query.\n\n"
    "3. Third finding: retry storms added 1200 extra calls per hour, "
    "which explains most of the 30% cost increase this week.\n\n"
    "We recommend adding a circuit breaker and we suggest capping "
    "retries at 2 per request as the next step for the team.\n"
)

# Writing content scoring above the floor via _score_writing
# (30-200 words + CTA + personalisation, no weak "I " opening).
WRITING_TEXT = (
    "Hi Sarah, your shop caught our eye because your sourdough sells "
    "out by noon every Saturday. We help local bakeries take weekend "
    "pre-orders online so you never turn a regular away. Would you "
    "like a quick demo this week? Just reply to this email and we "
    "will schedule a call at a time that suits you and your team."
)

ROUTER = TaskRouter()
_CODE_TYPES = ("code_fix", "code_review", "security", "lint_heal")
_ANALYSIS_TYPES = ("analysis", "report", "insight", "summarize")
_WRITING_TYPES = ("writing", "write", "email", "copy", "draft")


# ─── (a) keyword routing ────────────────────────────────────────────

def test_keyword_routes_analysis_to_b():
    assert ROUTER.route("please analyse this report and summarize the insights") == "B"
    assert ROUTER.route("summarize the Q3 report") == "B"
    assert ROUTER.route("classify these support tickets by topic") == "B"


def test_keyword_routes_writing_to_c():
    assert ROUTER.route("write an email draft for outreach copy") == "C"
    assert ROUTER.route("draft an outreach email") == "C"


def test_keyword_still_routes_code_to_a():
    assert ROUTER.route("fix the bug in this patch") == "A"
    assert ROUTER.route("refactor the code and validate the syntax") == "A"


def test_keyword_word_boundaries():
    # "fix" in "prefix" must not hijack the route to A — "report"
    # is the real signal here, so this goes to B.
    assert ROUTER.route("prefix the report with a header") == "B"
    # "write" in "overwrite" must not route to C; nothing else
    # matches either, so the safe default A applies.
    assert ROUTER.route("overwrite the file with the new version") == "A"
    # "test" in "latest" must not count as the A keyword "test".
    assert ROUTER.route("the latest news from the team") == "A"


def test_task_type_beats_keywords():
    assert ROUTER.route("summarize the report", {"task_type": "code_fix"}) == "A"
    assert ROUTER.route("fix the bug", {"task_type": "email"}) == "C"


def test_ctx_pin_beats_keywords():
    assert ROUTER.route("summarize the report", {"council": "C"}) == "C"
    assert ROUTER.route("fix the bug", {"council": "B"}) == "B"


def test_mixed_keywords_tie_falls_back_to_a():
    # One A hit ("fix") and one B hit ("report") — no winner, so the
    # safe default A applies instead of first-substring-wins.
    assert ROUTER.route("fix the report") == "A"


def test_no_keywords_defaults_to_a():
    assert ROUTER.route("hello there") == "A"


# ─── (b1/b2) fallback threads persona + task_type ────────────────────

def _fallback(monkeypatch, content, task, **context):
    """Run _fallback_single_call with the LLM stubbed; returns
    (decision, calls) where calls records every invocation's kwargs."""
    calls = []

    async def fake_llm_call_protected(**kwargs):
        calls.append(kwargs)
        return (content, 10.0, None)

    monkeypatch.setattr(llm_call_mod, "_llm_call_protected",
                        fake_llm_call_protected)
    parl = parl_mod.Parliament(db=None)
    decision = asyncio.run(parl._fallback_single_call(
        task=task, context=context, trace_id="t1", started=0.0,
    ))
    return decision, calls


def test_fallback_analysis_uses_council_b_persona_and_scoring(monkeypatch):
    decision, calls = _fallback(
        monkeypatch, ANALYSIS_TEXT, "analyse the quarterly report",
        council="B", task_type="analysis", user_id="u",
    )
    assert len(calls) == 1
    assert calls[0]["system"] == _COUNCIL_B_PERSONAS[0]
    assert calls[0]["system"] != _COUNCIL_A_PERSONA
    assert calls[0]["mode"] == "analysis"
    assert decision["status"] == "success"
    assert decision["council"] == "B"
    assert decision["scores"][0]["score"] == _score_output(
        ANALYSIS_TEXT.strip(), task_type="analysis")


def test_fallback_writing_uses_council_c_persona_and_scoring(monkeypatch):
    decision, calls = _fallback(
        monkeypatch, WRITING_TEXT, "draft an outreach email",
        council="C", task_type="write", user_id="u",
    )
    assert len(calls) == 1
    assert calls[0]["system"] == _COUNCIL_C_PERSONAS[0]
    assert calls[0]["system"] != _COUNCIL_A_PERSONA
    assert calls[0]["mode"] == "chat"
    assert decision["status"] == "success"
    assert decision["council"] == "C"
    assert decision["scores"][0]["score"] == _score_output(
        WRITING_TEXT.strip(), task_type="write")


def test_fallback_code_still_uses_council_a(monkeypatch):
    decision, calls = _fallback(
        monkeypatch, VALID_PY, "fix x.py",
        council="A", task_type="code_fix", user_id="u",
    )
    assert calls[0]["system"] == _COUNCIL_A_PERSONA
    assert calls[0]["mode"] == "code"
    assert decision["scores"][0]["score"] == _score_output(
        VALID_PY.strip(), task_type="code_fix")


def test_fallback_missing_task_type_defaults_to_code_fix(monkeypatch):
    # No task_type in context: the old code_fix default is preserved
    # (it is the scorer's own default), not invented per council.
    decision, _calls = _fallback(
        monkeypatch, VALID_PY, "fix x.py", council="B", user_id="u",
    )
    assert decision["scores"][0]["score"] == _score_output(
        VALID_PY.strip(), task_type="code_fix")


# ─── (b3) log task is tracked until done ─────────────────────────────

class _FailingCollection:
    async def insert_one(self, entry):
        raise RuntimeError("mongo down")


class _FailingDb:
    def __init__(self):
        self.parliament_log = _FailingCollection()


def test_log_event_task_is_tracked_until_done(caplog):
    parl = parl_mod.Parliament(db=_FailingDb())
    before = set(parl_mod._LOG_TASKS)

    async def _run():
        parl._log_event("test_event", "trace1", {}, {"x": 1})
        # Held immediately after the call returns (not orphaned)...
        assert len(parl_mod._LOG_TASKS - before) == 1
        # ...and dropped once it finishes.
        await asyncio.sleep(0.1)
        assert not (parl_mod._LOG_TASKS - before)

    with caplog.at_level(logging.DEBUG, logger="aurem-dev.parliament"):
        asyncio.run(_run())
    # ...with the failure actually logged, not lost to "Task exception
    # was never retrieved".
    assert "parliament_log event insert failed" in caplog.text


# ─── (c) one member raising cannot abort the vote ────────────────────

class _GoodMember:
    def __init__(self, name, temp, output, score):
        self.name = name
        self.temperature = temp
        self._output = output
        self._score = score

    async def cast_vote(self, *, task, context):
        return {"member": self.name, "output": self._output,
                "score": self._score, "error": None,
                "latency_ms": 1.0, "temp": self.temperature}


class _RaisingMember:
    name = "X-raising"
    temperature = 0.2

    async def cast_vote(self, *, task, context):
        raise RuntimeError("boom")


def test_one_member_raising_does_not_abort_vote():
    council = _Council()
    council.members = [
        _GoodMember("A1", 0.1, VALID_PY, 0.9),
        _RaisingMember(),
        _GoodMember("A3", 0.3, VALID_PY, 0.8),
    ]
    votes = asyncio.run(council.vote(task="fix", context={}))
    assert len(votes) == 3
    failed = [v for v in votes if v["member"] == "X-raising"]
    assert len(failed) == 1
    assert failed[0]["score"] == 0.0
    assert failed[0]["output"] == ""
    assert "RuntimeError" in (failed[0]["error"] or "")
    # The CEO still decides on the surviving members.
    decision = asyncio.run(
        CEO().decide(task="fix", votes=votes, context={"council": "A"}))
    assert decision["status"] == "success"
    assert decision["winner"] == "A1"


# ─── (d1) judge sees the tail ────────────────────────────────────────

def test_judge_excerpt_includes_broken_tail(monkeypatch):
    # Two candidates identical in the first 800 chars; one has a
    # clean tail, the other a broken tail. The old [:800] excerpt
    # made them indistinguishable to the judge.
    head = "# filler line to pad the candidate head\n" * 60
    assert len(head) > 1600  # forces the head+tail omission path
    clean = head + "CLEAN_TAIL_MARKER_ZZ return {'ok': True}\n"
    broken = head + "BROKEN_TAIL_MARKER_ZZ return {'broken': True\n"
    votes = [
        {"member": "cand-clean", "output": clean,
         "score": 0.2, "temp": 0.1, "error": None},
        {"member": "cand-broken", "output": broken,
         "score": 0.2, "temp": 0.2, "error": None},
    ]
    captured = {}

    async def fake_judge(**kwargs):
        captured.update(kwargs)
        return ("1", 1.0, None)

    monkeypatch.setattr(ceo_mod, "_ceo_judge_call_with_rescue", fake_judge)
    decision = asyncio.run(
        CEO().decide(task="fix the file", votes=votes,
                     context={"council": "A"}))
    user_prompt = captured["user"]
    assert "CLEAN_TAIL_MARKER_ZZ" in user_prompt
    assert "BROKEN_TAIL_MARKER_ZZ" in user_prompt
    assert "chars omitted" in user_prompt
    # The judge could tell them apart and picked the broken one —
    # proving the tail reached it (and that decide() still returns
    # the chosen candidate's FULL output).
    assert decision["winner"] == "cand-broken"
    assert "BROKEN_TAIL_MARKER_ZZ" in decision["output"]


# ─── (d2) every routed task type is scored, none flat-0.65 ───────────

def _probe_for(task_type):
    if task_type in _CODE_TYPES:
        return VALID_PY
    if task_type in _ANALYSIS_TYPES:
        return ANALYSIS_TEXT
    return WRITING_TEXT


def test_every_routed_task_type_gets_differentiated_score():
    for ttype in _CODE_TYPES + _ANALYSIS_TYPES + _WRITING_TYPES:
        score = _score_output(_probe_for(ttype), task_type=ttype)
        assert score != 0.65, f"{ttype} still falls through to flat 0.65"


def test_write_routes_to_writing_scorer():
    assert _score_output(WRITING_TEXT, task_type="write") == \
        _score_output(WRITING_TEXT, task_type="writing")


def test_broken_writing_does_not_auto_accept():
    garbage = "x" * 60
    assert _score_output(garbage, task_type="write") < 0.55
    assert _score_output(garbage, task_type="email") < 0.55
    assert _score_output(garbage, task_type="code_review") < 0.55


def test_unknown_task_type_goes_to_judge_not_auto_accept():
    assert _score_output(ANALYSIS_TEXT, task_type="other") == 0.50
    assert _score_output(ANALYSIS_TEXT, task_type="other") < 0.55


# ─── (d3) word-boundary signal matching ──────────────────────────────

def test_detect_output_type_word_boundaries():
    assert detect_output_type("classify these support tickets by topic") != "code_output"
    assert detect_output_type("debug the login flow") != "code_output"
    assert detect_output_type("Please prefix the report with a header") == "analysis_output"
    assert detect_output_type("overwrite the file with the new version") != "writing_output"


def test_detect_output_type_positive_controls():
    assert detect_output_type("fix the bug in this function") == "code_output"
    assert detect_output_type("write an email draft") == "writing_output"
    assert detect_output_type("analyze the report and summarize insights") == "analysis_output"


_NEUTRAL_WRITING = (
    "Acme Bakery opens at seven every morning with fresh bread, "
    "pastries, and coffee for the whole neighbourhood. The menu lists "
    "sourdough, croissants, muffins, cakes, and seasonal specials "
    "baked in small batches. Prices stay low while quality stays "
    "high, and regulars enjoy a loyalty card with a free pastry every "
    "tenth visit. Visit the shop on Main Street this weekend."
)


def test_youtube_does_not_earn_personalisation_credit():
    base = _score_writing(_NEUTRAL_WRITING)
    with_youtube = _score_writing(
        _NEUTRAL_WRITING + " See our YouTube channel.")
    assert with_youtube == base, (
        f"YouTube must not earn the 'you' credit: {with_youtube} != {base}")
    with_your = _score_writing(
        _NEUTRAL_WRITING + " Your feedback matters.")
    assert with_your > base
    with_you = _score_writing(
        _NEUTRAL_WRITING + " Thank you for reading.")
    assert with_you > base


_DESCRIPTIVE_ANALYSIS = (
    "The transaction volume rose 12% in March while customer "
    "satisfaction stayed at 88% across 400 survey responses from "
    "three regions during the same period. The figures cover every "
    "store in the region and every product line on the shelves. "
    "Managers reviewed the totals at the monthly meeting and noted "
    "the same pattern in each district. Delivery times stayed flat "
    "while basket sizes grew slightly in the second half of March."
)


def test_transaction_satisfaction_do_not_earn_action_credit():
    # "action" inside "transaction"/"satisfaction" is not a
    # recommendation. The old substring match scored this 0.90.
    assert _score_analysis(_DESCRIPTIVE_ANALYSIS) == 0.75
    # A real recommendation still earns the credit (+0.15 exactly —
    # same length band, no other signal changes).
    with_action = _score_analysis(
        _DESCRIPTIVE_ANALYSIS + " We recommend a review.")
    assert with_action == _score_analysis(_DESCRIPTIVE_ANALYSIS) + 0.15


# ─── (e1) mode-C pass_result fails closed ────────────────────────────

def test_quality_filter_mode_c_missing_pass_result_fails_closed():
    base = {"user_message": "fix x", "final_output": "done", "mode": "C"}
    assert _quality_filter(dict(base)) is False
    assert _quality_filter({**base, "pass_result": True}) is True
    assert _quality_filter({**base, "pass_result": False}) is False


def test_quality_filter_non_code_modes_unchanged():
    doc = {"user_message": "hi", "final_output": "hello", "mode": "A"}
    assert _quality_filter(doc) is True


# ─── (e2) tail tokens survive into tf ────────────────────────────────

def test_row_from_log_keeps_tail_tokens():
    head = "filler words here " * 20  # 380 chars of head filler
    doc = {"user_message": head + "zanzibar more tail words",
           "final_output": "ok", "mode": "A"}
    row = _row_from_log(doc)
    assert "zanzibar" in row["tf"], "tail token past char 280 must be indexed"
    assert "filler" in row["tf"]
    assert len(row["msg"]) == 280  # display cap preserved
    assert row["truncated"] is True
    assert row["n_tokens"] > 20


def test_row_from_log_short_message_unchanged():
    doc = {"user_message": "short question here",
           "final_output": "short answer", "mode": "A"}
    row = _row_from_log(doc)
    assert row["msg"] == "short question here"
    assert row["truncated"] is False
    assert row["tf"] == {"short": 1, "question": 1, "here": 1}
