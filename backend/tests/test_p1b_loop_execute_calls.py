"""P1 L5 — per-file LLM call count in services/loop_execute.py.

Audit claim (verify-latency-debt.md, CONFIRMED): worst case 3 sequential
LLM calls per existing file — surgical `try_minimal_edit` (1), diagnose-
first `_localize_change_target` (1), full rewrite (1) — under a 60 s
per-file timeout.

Fix: when the surgical LLM call actually COMPLETES, it has already read
the whole file + task once, so the diagnose-first pass is a redundant
re-verification of the same inputs and is skipped. Worst case drops
3 -> 2 calls/file; surgical success stays 1 call; non-trivial requests
(no surgical call) still get the localize pass, unchanged.

These tests drive `_generate_one_inner` directly with a stub LLM that
records every invocation (distinguished by max_tokens: 600 surgical,
300 localize, 4000 rewrite) and a stub fetch_file. No provider, no
Mongo, no GitHub — `services/generation_rules` is deliberately NOT
seeded here, so its manifest injection is skipped via the function's
own try/except, exactly as in production when that import fails.
"""
import pytest

from services.loop_execute import _generate_one_inner

PLAN = {"title": "Add a header note", "bullets": ["add note at top"]}

# >= 100 chars so the localizer would NOT skip it on size alone — the
# skip under test must come from the fix, not the pre-existing
# tiny-file early return in _localize_change_target.
CURRENT = (
    "# Project README\n\n"
    "This project does things.\n"
    "It has several lines of text here.\n"
    "More context lines follow below.\n"
    "def helper():\n"
    "    return 42\n"
    "\n"
    "And a trailing paragraph to push the file well past a hundred\n"
    "characters of content, so localization is genuinely eligible.\n"
)

REWRITE_BODY = "# Project README\n\n<!-- P1B-NOTE -->\n\nThis project does things."  # _strip_fences strips the trailing \n


class _StubLLM:
    """Records every LLM invocation; behaviour set per test."""

    def __init__(self, surgical="ok", localize="ok"):
        self.calls = []  # (max_tokens, mode, review_mode)
        self.surgical = surgical  # "ok" | "not_expressible" | "raise"
        self.localize = localize

    async def __call__(self, *, system, user, max_tokens, mode,
                       user_id=None, review_mode=None):
        self.calls.append((max_tokens, mode, review_mode))
        if max_tokens == 600:  # surgical (minimal_edit)
            if self.surgical == "raise":
                raise RuntimeError("provider down")
            if self.surgical == "not_expressible":
                return {"content": '{"op": "not_expressible"}'}
            return {"content": '{"op": "prepend", "text": "<!-- P1B-NOTE -->"}'}
        if max_tokens == 300:  # diagnose-first localize
            return {"content": '{"line": 5, "function": "helper", "reason": "target"}'}
        # full rewrite
        return {"content": REWRITE_BODY}

    def kinds(self):
        return [c[0] for c in self.calls]


async def _run(llm, *, current=CURRENT, user_message):
    async def fetch_file(owner, repo, path, branch, token):
        return current

    return await _generate_one_inner(
        client=None, idx=1, total=1, path="README.md",
        plan=PLAN, user_message=user_message,
        owner="o", repo="r", branch="main", token="t", user_id="u1",
        fetch_file=fetch_file, call_llm_with_meta=llm,
    )


TRIVIAL_MSG = "add a one-line comment at the top of README"


async def test_surgical_success_uses_one_llm_call():
    """The task's acceptance test: a file that succeeds surgically
    uses 1 LLM call, not 3."""
    llm = _StubLLM(surgical="ok")
    out = await _run(llm, user_message=TRIVIAL_MSG)
    assert llm.kinds() == [600], f"expected exactly the surgical call, got {llm.calls}"
    assert out == {"path": "README.md",
                   "content": "<!-- P1B-NOTE -->\n" + CURRENT}


async def test_surgical_failure_skips_localize():
    """Surgical call completed but declined (not_expressible) -> the
    diagnose-first re-verification is skipped: 2 calls total
    (surgical + rewrite), no 300-token localize call."""
    llm = _StubLLM(surgical="not_expressible")
    out = await _run(llm, user_message=TRIVIAL_MSG)
    assert llm.kinds() == [600, 4000], f"localize must be skipped, got {llm.calls}"
    assert out == {"path": "README.md", "content": REWRITE_BODY}


async def test_nontrivial_request_still_localizes():
    """Negative control: a non-trivial request makes no surgical call,
    so the localize pass must still run (localize + rewrite = 2)."""
    llm = _StubLLM()
    out = await _run(llm, user_message="refactor the module to use async throughout")
    assert llm.kinds() == [300, 4000], f"localize must still run, got {llm.calls}"
    assert out == {"path": "README.md", "content": REWRITE_BODY}


async def test_new_file_uses_one_call():
    """New file (empty current): no surgical, no localize — rewrite
    only. Pre-existing behaviour, pinned so the fix cannot regress it."""
    llm = _StubLLM()
    out = await _run(llm, current="", user_message=TRIVIAL_MSG)
    assert llm.kinds() == [4000], f"expected rewrite only, got {llm.calls}"
    assert out == {"path": "README.md", "content": REWRITE_BODY}


async def test_surgical_provider_failure_still_localizes():
    """The first call must actually COMPLETE to suppress the re-check:
    when the surgical LLM call raises, localize still runs (3 calls:
    failed surgical + localize + rewrite)."""
    llm = _StubLLM(surgical="raise")
    out = await _run(llm, user_message=TRIVIAL_MSG)
    assert llm.kinds() == [600, 300, 4000], f"localize must run after a failed surgical call, got {llm.calls}"
    assert out == {"path": "README.md", "content": REWRITE_BODY}
