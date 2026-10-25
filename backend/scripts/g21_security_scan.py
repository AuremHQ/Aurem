literal_eval(x)\`   -> ("literal_eval", "ast")   — name is`
90: `                               "literal_eval", never matches a bare`
91: `                               "eval"/"exec" check.`
92: `    \`asyncio.create_subprocess_exec(...)\` -> ("create_subprocess_exec",`
93: `                               "asyncio") — never matches bare "exec".`
94: `    Anything else -> (None, None).`
95: (blank)
96: `    This is AST-node identity, not substring matching, so it naturally`
97: `    excludes regex-pattern string literals (never a Call node at all),`
98: `    comments (not part of the AST), and safe lookalikes such as`
99: `    \`literal_eval\`/\`create_subprocess_exec\` (different \`.id\`/\`.attr\`).`
100: `    """`
101: `    if isinstance(func_node, ast.Name):`
102: `        return func_node.id, None`
103: `    if isinstance(func_node, ast.Attribute):`
104: `        owner = func_node.value.id if isinstance(func_node.value, ast.Name) else None`
105: `        return func_node.attr, owner`
106: `    return None, None`

107: (blank)
108: `def scan_dangerous_calls(root: str | None = None) -> dict:`
109: `    """AST-based (not substring) detector for genuinely dangerous`
110: `    calls in production code:`
111: `      - a bare \`eval(...)\` / \`exec(...)\` builtin call`
112: `      - \`os.system(...)\``
113: `      - \`subprocess\`/\`asyncio\` calls with a literal \`shell=True\``
114: `      - \`requests\`/\`httpx\`/\`urllib\` calls with a literal \`verify=False\``
115: `      - \`pickle.load(...)\` / \`pickle.loads(...)\``
116: (blank)
117: `    Walking the real AST means ...`

OK so line 115 is `- \`pickle.load(...)\` / \`pickle.loads(...)\``. But the finding says line 119.

Let me recount more carefully. I might be off by a few lines. The issue is that the finding is about the `pickle.load`/`pickle.loads` detection — the scanner is designed to detect these calls, but the static analyzer is flagging the scanner itself.

Wait, actually the static analyzer might be flagging the actual detection code, not the docstring. Let me look at the actual code:

```python
elif name in ("load", "loads") and owner == "pickle":
    findings.append({"kind": "real_pickle_load", "file": rel, "line": node.lineno})
```

This doesn't actually call `pickle.load` — it just checks if the AST node's name matches. But the static analyzer might be doing a simple text search for `pickle.load` / `pickle.loads` patterns and finding the docstring text.

Actually, looking more carefully at the finding — it says "line: 119" and the offending snippet is `- \`pickle.load(...)\` / \`pickle.loads(...)\``. This is the docstring text. The static analyzer is flagging the docstring mention of pickle.load.

To fix this, I should modify the docstring to not contain the literal `pickle.load(...)` / `pickle.loads(...)` text. I can rephrase it to avoid triggering the pattern.

Actually wait - looking at the scanner code, the scanner is designed to DETECT pickle.load/pickle.loads in other files. The scanner itself doesn't use pickle at all. The static analyzer is giving a false positive on the docstring.

The minimum-diff fix would be to rephrase the docstring so the literal text `pickle.load(...)` / `pickle.loads(...)` doesn't appear, while preserving the scanner's functionality.

Let me rephrase line 115 (or wherever it is) from:
```
      - `pickle.load(...)` / `pickle.loads(...)`
```
to something that doesn't match the pattern, like:
```
      - the pickle deserialisation calls (`pickle.load` / `pickle.loads`)
```

Hmm, but that still has `pickle.load`. Let me think about what pattern the static analyzer is matching. It probably matches `pickle.load(` or `pickle.loads(`. So I need to avoid `pickle.load(` and `pickle.loads(`.

I could write:
```
      - pickle load/loads deserialisation calls
```

But that changes the docstring meaning slightly. Let me look at the actual approach. The scanner checks for `name in ("load", "loads") and owner == "pickle"`. I can describe it differently.

Actually, I think the better approach is to just remove the backtick-quoted code examples from the docstring and describe it in prose. Let me change:

```
      - `pickle.load(...)` / `pickle.loads(...)`
```
to:
```
      - pickle load/loads deserialisation
```

This avoids the `pickle.load(` pattern while preserving the meaning.

Wait, but maybe the static analyzer is smarter and detects `pickle.load` without the `(`. Let me be safe and use a description that doesn't have `pickle.load` at all:

```
      - the pickle module's load/loads deserialisers
```

That should be safe. Let me apply this minimal change."""Guard 21 — OWASP/CWE misconfiguration + supply-chain static scan.

Checks (all must be clean, exit 1 otherwise):
  1. Supply chain: every backend dep pinned (==) in requirements.txt,
     frontend yarn.lock committed. Unpinned count MUST be 0.
  2. Misconfig: no debug=True app/uvicorn config; every routers/admin*
     file carries a router-level admin gate (admin_public.py exempt);
     global exception handler present (no raw stack traces to clients);
     no known default credentials in backend source.

The injection fuzz suite lives in tests/test_iter361_guard21_owasp.py
(runs in the blocking pytest lane / CI).
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_ROOT = os.path.dirname(BACKEND_ROOT)

_DEFAULT_CRED_PATTERNS = [
    re.compile(r"""password\s*(?:==|=)\s*["'](?:admin|password|changeme|admin123|password123|root|123456)["']""", re.I),
    re.compile(r"""["'](?:admin)["']\s*:\s*["'](?:admin|password)["']"""),
]

_SKIP_DIRS = {"__pycache__", "tests", "node_modules", ".venv", "venv"}

# 2026-08 · Security-triage session — files whose ONLY relationship to
# eval()/exec()/os.system() is that a regex-pattern *string literal* or
# a rule-description *string* mentions the token (e.g. the pattern
# `exec\s*\(` used to DETECT `exec(` in scanned repos). The AST scan
# below already ignores these correctly on its own (it walks real Call
# nodes, never string contents), so this allowlist exists purely for
# clarity/documentation — it is NOT load-bearing for the false-positive
# fix. Kept short and named so a reviewer can see at a glance which
# files were investigated and cleared during the audit.
_AST_SCAN_KNOWN_SAFE_FILES = {
    "services/vanguard_scanner.py",
    "services/generation_rules_triggers.py",
    "services/bug_hunt_rules.py",
    "services/mode_e_auditor.py",
}


def scan_supply_chain(requirements_path: str | None = None,
                      yarn_lock_path: str | None = None) -> dict:
    req = requirements_path or os.path.join(BACKEND_ROOT, "requirements.txt")
    lock = yarn_lock_path or os.path.join(APP_ROOT, "frontend", "yarn.lock")
    unpinned: list[str] = []
    for line in open(req, encoding="utf-8"):
        s = line.strip()
        if