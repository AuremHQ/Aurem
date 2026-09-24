"""
FRESH, LIVE re-reproduction of the founder-reported ReRoots destructive-diff
incident — run on 2026-09-13, using the REAL production call path (not a
reference to any earlier test run). No mocks.

Exercises, in order, the EXACT functions/constants worker_api.py itself
calls for a first-pass codegen edit:
  1. `_AI_SYS`                (real production system prompt, imported directly)
  2. `services.llm.call_llm`  (real LLM call, max_tokens=12000, temp=0.0 — the
                                CURRENT production budget, not the old test's 1500)
  3. `services.llm_file_parser.parse_file_blocks`
  4. `services.cto_pipeline_steps._run_hallucination_gate` (the FULL gate,
     including its own internal retry-call to the real LLM if needed)

Prints the actual unified diff + actual gate verdict for manual inspection.
Run 3x back-to-back (LLM is non-deterministic) to build confidence.
"""
import asyncio
import difflib
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routers.cto_projects.worker_api import _AI_SYS  # REAL prod system prompt
from services.llm import call_llm
from services.llm_file_parser import parse_file_blocks
from services.cto_pipeline_steps import _run_hallucination_gate

ORIGINAL_APP_JS = """import React, { useContext, useState, useEffect, useCallback, useRef, useMemo, lazy } from 'react';
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

TASK = "update the footer copyright text to say 'Phase 0 live re-repro 2026-09-13'"
CONTENTS = {"src/App.js": ORIGINAL_APP_JS}
FILES_BLOB = f"FILE: src/App.js\n```\n{ORIGINAL_APP_JS}\n```"
USER_MSG = f"TASK: {TASK}\n\n{FILES_BLOB}"


async def run_once(run_no: int):
    print(f"\n{'='*70}\nRUN {run_no}  —  {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n{'='*70}")
    reply = await call_llm(
        messages=[{"role": "user", "content": USER_MSG}],
        system=_AI_SYS, max_tokens=12000, temperature=0.0,
    )
    edits = parse_file_blocks(reply or "")
    if not edits or "src/App.js" not in edits:
        print("MODEL RETURNED NO PARSEABLE src/App.js EDIT this run — skipping")
        print("RAW REPLY (first 500 chars):", (reply or "")[:500])
        return None

    first_pass_body = edits["src/App.js"]
    diff = list(difflib.unified_diff(
        ORIGINAL_APP_JS.splitlines(), first_pass_body.splitlines(),
        fromfile="src/App.js (before)", tofile="src/App.js (first-pass AI output)",
        lineterm="",
    ))
    print(f"--- FIRST-PASS DIFF ({len(diff)} lines) ---")
    print("\n".join(diff))

    final_edits, err = await _run_hallucination_gate(
        task_id=f"phase0-live-{run_no}", edits=dict(edits), contents=CONTENTS,
        user_msg=USER_MSG, ai_sys=_AI_SYS,
    )
    print(f"\n--- GATE VERDICT ---")
    if err:
        print(f"BLOCKED (refused to push): {err[:400]}")
    else:
        final_body = final_edits.get("src/App.js", "")
        final_diff = list(difflib.unified_diff(
            ORIGINAL_APP_JS.splitlines(), final_body.splitlines(),
            fromfile="src/App.js (before)", tofile="src/App.js (post-gate, would-be-committed)",
            lineterm="",
        ))
        added = sum(1 for l in final_diff if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in final_diff if l.startswith("-") and not l.startswith("---"))
        print(f"ALLOWED (would be pushed) — final diff: +{added} / -{removed}")
        print("\n".join(final_diff))
        # Ground-truth check independent of the gate's own logic:
        orig_import_lines = [l for l in ORIGINAL_APP_JS.splitlines() if l.strip().startswith("import ")]
        missing = [l for l in orig_import_lines if l not in final_body]
        if missing:
            print(f"\n!!! DANGER: {len(missing)} import line(s) missing from what the gate ALLOWED:")
            for m in missing:
                print("   ", m)
        else:
            print("\nAll 8 original import lines confirmed present in the allowed output. Safe.")
    return err


async def main():
    for i in range(1, 4):
        await run_once(i)

if __name__ == "__main__":
    asyncio.run(main())
