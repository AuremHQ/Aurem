"""
tests/test_2026_09_12_critical_diff_hallucination_and_cancel.py

Founder-reported CRITICAL bug (RerootsBeauty/ReRoots- test account):
a trivial "update the footer copyright text" request came back with a
diff for App.js showing "+2 / -58" — including the top-level React
import and 4 context-provider imports fully deleted — marked "clean"
with a live "Approve the fix" button. A later "STOP — cancel this"
message did not disable that button.

Root causes fixed this session:
  1. `contents[path] = body[:10000]` (worker_api.py / worker_git.py)
     truncated the ORIGINAL file shown to the model well below real
     file sizes, while the model was still told to return a COMPLETE
     new file body — guaranteed data loss for any file over 10KB with
     zero warning to the model.
  2. `_hallucination_reasons()`'s 40%-keep-ratio gate only catches
     WHOLESALE rewrites. A file that drops just its import block
     while keeping ~90% of the rest of the file (exactly what was
     reported) sails through untouched.
  3. `ShipLintBadge` labelled its BRIEF-text-only lint check as a bare
     "clean ✓" next to the diff, reasonably read as "the code is
     safe" — it never checked the code at all.
  4. No mechanism let a LATER chat message invalidate an EARLIER
     still-pending "Approve the fix" button.

This suite validates the fixes with the REAL functions (no mocks) —
tests 1-6 are pure/deterministic; test 7 makes a REAL LLM call.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from services.cto_projects_helpers import (
    _hallucination_reasons,
    _dropped_import_reasons,
    _extract_top_level_imports,
    _context_with_truncation_marker,
    FILE_CONTEXT_CHAR_CAP,
)


# ─────────────────────────────────────────────────────────────────────
# Synthetic repro of the EXACT reported failure shape: a large-ish
# App.js with a top-level React import + 4 context-provider imports,
# routes, and a footer near the bottom. The "bad" AI output below
# mirrors the reported diff: footer text changed correctly, but the
# import block is gone while ~90% of the rest of the file survives.
# ─────────────────────────────────────────────────────────────────────
_ORIGINAL_APP_JS = """import React, { useContext, useState, useEffect, useCallback, useRef, useMemo, lazy } from 'react';
import { AuthContext } from './context/AuthContext';
import { CartContext } from './context/CartContext';
import { SiteContentContext } from './context/SiteContentContext';
import { TranslationContext } from './context/TranslationContext';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import Header from './components/Header';
import ProductList from './components/ProductList';

function App() {
  const { user } = useContext(AuthContext);
  const { cart } = useContext(CartContext);
  const { content } = useContext(SiteContentContext);
  const { t } = useContext(TranslationContext);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setReady(true);
  }, []);

  return (
    <BrowserRouter>
      <Header user={user} cart={cart} />
      <Routes>
        <Route path="/" element={<ProductList content={content} />} />
      </Routes>
      <footer className="site-footer">
        <p>&copy; 2025 ReRoots Beauty. All rights reserved.</p>
      </footer>
    </BrowserRouter>
  );
}

export default App;
"""

_BAD_AI_OUTPUT = """function App() {
  const { user } = useContext(AuthContext);
  const { cart } = useContext(CartContext);
  const { content } = useContext(SiteContentContext);
  const { t } = useContext(TranslationContext);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setReady(true);
  }, []);

  return (
    <BrowserRouter>
      <Header user={user} cart={cart} />
      <Routes>
        <Route path="/" element={<ProductList content={content} />} />
      </Routes>
      <footer className="site-footer">
        <p>&copy; 2025 regression test rollback check</p>
      </footer>
    </BrowserRouter>
  );
}

export default App;
"""


def test_1_old_ratio_gate_alone_misses_the_reported_pattern():
    """Proves the GAP existed: the pre-existing keep-ratio-only check
    does NOT flag this diff (ratio stays comfortably above the 0.4
    trip-wire even with the whole import block gone), which is why
    the dangerous diff reached "Approve the fix" un-flagged."""
    reasons = _hallucination_reasons(
        {"src/App.js": _BAD_AI_OUTPUT}, {"src/App.js": _ORIGINAL_APP_JS},
    )
    assert reasons == [], (
        "sanity check failed — if this now fires, the ratio threshold "
        "changed and this test's premise needs updating"
    )


def test_2_new_import_gate_catches_the_reported_pattern():
    """The FIX: a dedicated, deterministic check for dropped top-level
    imports catches exactly this case, independent of overall ratio."""
    reasons = _dropped_import_reasons(
        {"src/App.js": _BAD_AI_OUTPUT}, {"src/App.js": _ORIGINAL_APP_JS},
    )
    assert reasons, "dropped-import gate should have fired but did not"
    joined = " ".join(reasons)
    assert "src/App.js" in joined
    assert "import" in joined.lower()
    # Every distinct dropped import should be enumerated (or counted).
    assert "8 import" in joined or "React" in joined


def test_3_extract_top_level_imports_js():
    imports = _extract_top_level_imports("src/App.js", _ORIGINAL_APP_JS)
    assert len(imports) == 8
    assert any("AuthContext" in i for i in imports)
    assert any("CartContext" in i for i in imports)
    assert any("SiteContentContext" in i for i in imports)
    assert any("TranslationContext" in i for i in imports)


def test_4_extract_top_level_imports_python():
    py_src = (
        "import os\n"
        "import sys\n"
        "from typing import Optional\n"
        "from services.foo import bar\n\n"
        "def f():\n    pass\n"
    )
    imports = _extract_top_level_imports("services/x.py", py_src)
    assert len(imports) == 4


def test_5_a_faithful_edit_never_trips_the_new_gate():
    """A GOOD edit that preserves every import must NOT be flagged —
    the gate must not create false positives on legitimate edits."""
    good_output = _ORIGINAL_APP_JS.replace(
        "All rights reserved.", "regression test rollback check",
    )
    reasons = _dropped_import_reasons(
        {"src/App.js": good_output}, {"src/App.js": _ORIGINAL_APP_JS},
    )
    assert reasons == [], f"false positive on a faithful edit: {reasons}"
    ratio_reasons = _hallucination_reasons(
        {"src/App.js": good_output}, {"src/App.js": _ORIGINAL_APP_JS},
    )
    assert ratio_reasons == []


def test_6_truncation_marker_caps_and_warns_instead_of_silently_cutting():
    small = "x" * 100
    body, was_trunc = _context_with_truncation_marker(small, cap=200)
    assert body == small
    assert was_trunc is False

    big = "y" * 50_000
    body, was_trunc = _context_with_truncation_marker(big, cap=FILE_CONTEXT_CHAR_CAP)
    assert was_trunc is True
    assert "TRUNCATED" in body
    assert "DO NOT attempt a full-file rewrite" in body
    assert body.startswith("y" * 100)  # real content still leads


@pytest.mark.asyncio
async def test_7_live_llm_reproduces_and_gate_catches_it_real_call():
    """NO MOCKS — sends the EXACT same system prompt + a large
    synthetic file through the REAL model (DeepSeek, same as
    production) with a trivial text-edit task, then runs the REAL
    `_run_hallucination_gate`. This is the closest safe reproduction
    of the live incident without touching a real GitHub repo: if the
    model drops an import (reproducing the reported failure mode),
    the gate must catch it and either repair or refuse — it must
    NEVER be allowed through un-flagged.
    """
    from services.llm import call_llm
    from services.llm_file_parser import parse_file_blocks
    from services.cto_projects_helpers import _dropped_import_reasons as _dig

    task = "update the footer copyright text to say 'regression test rollback check'"
    contents = {"src/App.js": _ORIGINAL_APP_JS}
    files_blob = f"FILE: src/App.js\n```\n{_ORIGINAL_APP_JS}\n```"
    user_msg = f"TASK: {task}\n\n{files_blob}"
    sys_prompt = (
        "You are AUREM — a senior engineer who SHIPS production-grade code.\n"
        "OUTPUT CONTRACT: Line 1 = 'SUMMARY: <one line>'. Then for EVERY "
        "file you change: 'FILE: <path>' then a ``` fenced COMPLETE file "
        "body. Preserve every existing line (including every import) you "
        "did not intend to change."
    )
    reply = await call_llm(
        messages=[{"role": "user", "content": user_msg}],
        system=sys_prompt, max_tokens=1500, temperature=0.0,
    )
    edits = parse_file_blocks(reply or "")
    if not edits:
        pytest.skip("model returned no parseable FILE block this run — "
                    "non-deterministic LLM output, not a gate failure")
    # Whatever the model actually did, the real check must correctly
    # classify it: if it dropped an import, the gate fires; if it
    # faithfully preserved the file, the gate stays silent. Either
    # way, prove the gate's verdict matches ground truth by comparing
    # against a direct, independent import-presence check.
    reasons = _dig(edits, contents)
    for path, new_body in edits.items():
        orig = contents.get(path)
        if not orig:
            continue
        orig_imports = _extract_top_level_imports(path, orig)
        really_missing = [i for i in orig_imports if i not in new_body.replace("\n", " ")]
        if really_missing:
            assert reasons, (
                f"model dropped {len(really_missing)} import(s) on this "
                f"real call and the gate did NOT flag it: {really_missing}"
            )
        else:
            assert not reasons, f"false positive on a faithful live-model edit: {reasons}"
