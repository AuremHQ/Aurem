"""Shared import stubs + fake Mongo for the P1e audit-fix tests.

The modules under test (services/orchestrator.py, services/loop_verify.py,
services/chat_helpers.py, routers/chat/misc.py,
services/ora_chat/slash_commands.py, services/ora_chat/safety.py) pull
cto_services + half the service layer at module top, none of which is
seeded in this lane's tree. The stubs below are minimal dummies for
modules the code under test never calls — same pattern the P0 backend
lane's test_audit_p0_voice_contract.py uses. Every stubbed name is
listed next to the real import it satisfies. All installs are
``setdefault`` so the REAL module wins when it exists (CI, full tree).

Also ships FakeCollection/FakeDB: a small in-memory Motor stand-in
that implements the exact update operators the code under test uses
($set with dotted paths incl. numeric turns indices, $push with
$each+$slice, $setOnInsert, upsert). It mimics real Mongo's
null-padding on an out-of-range $set index — the behaviour the Iter 34
note on /turn/shipped documents — so a test that asserts "no sparse
turns were created" is meaningful.
"""
from __future__ import annotations

import re
import sys
import types
from pathlib import Path
from types import SimpleNamespace

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

_INSTALLED = False


def install_p1e_stubs() -> None:
    """Install every fake module (idempotent). Must run BEFORE any
    `import services.…` / `import routers.chat.…` in the test file."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    async def _async_none(*a, **k):
        return None

    async def _async_empty_str(*a, **k):
        return ""

    # ── cto_services.auth: current_dev (misc.py) + is_admin_or_founder
    # (slash_commands.py _image). The founder stub reads the same
    # `is_founder` flag the tests put on the ctx user dict.
    cto = types.ModuleType("cto_services")
    cto.__path__ = []
    sys.modules.setdefault("cto_services", cto)
    auth = types.ModuleType("cto_services.auth")
    auth.current_dev = _async_none
    auth.is_admin_or_founder = lambda user: bool((user or {}).get("is_founder"))
    auth.require_admin = _async_none
    auth.create_token = lambda *a, **k: ""
    sys.modules.setdefault("cto_services.auth", auth)
    dbmod = types.ModuleType("cto_services.db")
    dbmod.get_db = lambda: None
    sys.modules.setdefault("cto_services.db", dbmod)

    # ── services.llm (orchestrator.py, chat_helpers.py): only the two
    # names imported at module top. No test here calls the LLM.
    llm = types.ModuleType("services.llm")

    async def _fake_call_llm(*a, **k):
        return {"ok": True, "content": ""}

    llm.call_llm_with_meta = _fake_call_llm
    llm.cap_for = lambda mode: 4000
    sys.modules.setdefault("services.llm", llm)

    # ── services.identity (orchestrator.py)
    ident = types.ModuleType("services.identity")
    ident.OR_IDENTITY = "ORA"
    ident.PRODUCT_IDENTITY = "Aurem"
    sys.modules.setdefault("services.identity", ident)

    # ── services.tools_bridge (orchestrator.py)
    tb = types.ModuleType("services.tools_bridge")
    tb.list_tools = _async_none
    tb.invoke_tool = _async_none
    tb.extract_tool_calls = lambda *a, **k: []
    tb.strip_tool_calls = lambda s: s
    tb.detect_unsourced_citations = lambda *a, **k: []
    sys.modules.setdefault("services.tools_bridge", tb)

    # ── services.local_tools (orchestrator.py): faithful copy of the
    # real _is_safe_repo_path charset rule (letters, digits, space,
    # dot, dash, underscore, `/` — nothing else), so the hook's
    # unsafe-path gate behaves in tests exactly as in production.
    lt = types.ModuleType("services.local_tools")
    lt.TOOL_SPECS = []
    lt.invoke_local_tool = _async_none
    _UNSAFE = re.compile(r"[^A-Za-z0-9 _.\-/]")

    def _is_safe_repo_path(path):
        if not path or not isinstance(path, str):
            return False
        return not _UNSAFE.search(path)

    lt._is_safe_repo_path = _is_safe_repo_path
    sys.modules.setdefault("services.local_tools", lt)

    # ── services.skill_usage (orchestrator.py)
    su = types.ModuleType("services.skill_usage")
    su.log_skill_use = lambda *a, **k: None
    sys.modules.setdefault("services.skill_usage", su)

    # ── services.interaction_contract / session_summary /
    # commit_boundary (chat_helpers._persist_turn lazy imports)
    ic = types.ModuleType("services.interaction_contract")
    ic.finalize = lambda *a, **k: {}
    ic.new_request_id = lambda: "test-request-id"
    sys.modules.setdefault("services.interaction_contract", ic)
    ss = types.ModuleType("services.session_summary")
    ss.maybe_update_summary = _async_none
    sys.modules.setdefault("services.session_summary", ss)
    cb = types.ModuleType("services.commit_boundary")
    cb.propose_from_turn = _async_none
    sys.modules.setdefault("services.commit_boundary", cb)

    # ── services.usage: is_founder_email (misc.py)
    usage = types.ModuleType("services.usage")
    usage.is_founder_email = lambda *a, **k: False
    sys.modules.setdefault("services.usage", usage)

    # ── services.ora_chat.codebase_index (slash_commands.py): the
    # repo-read handlers are not under test here; the fake answers
    # with empty results so _repo_tree/_find/_read/_defs never touch
    # disk. The real image_gen module IS used (seeded in the tree).
    ci = types.ModuleType("services.ora_chat.codebase_index")
    ci.compact_tree = _async_empty_str
    ci.index_stats = _async_none
    ci.find_files = _async_none
    ci.read_file = _async_none
    ci.search_defs = _async_none
    sys.modules.setdefault("services.ora_chat.codebase_index", ci)

    # ── routers.chat package: dummy `router` (misc.py
    # `from . import router`) so importing routers.chat.misc does NOT
    # trigger the real routers/chat/__init__.py cascade (turn, stream,
    # history — not seeded in this lane).
    routers_pkg = types.ModuleType("routers")
    routers_pkg.__path__ = [str(BACKEND_DIR / "routers")]
    sys.modules.setdefault("routers", routers_pkg)

    class _DummyRouter:
        def __getattr__(self, name):
            def _decorator(*a, **k):
                def _wrap(fn):
                    return fn
                return _wrap
            return _decorator

    chat_pkg = types.ModuleType("routers.chat")
    chat_pkg.__path__ = [str(BACKEND_DIR / "routers" / "chat")]
    chat_pkg.router = _DummyRouter()
    sys.modules.setdefault("routers.chat", chat_pkg)

    # ── routers.chat.deps: get_current_user_db (misc.py)
    deps = types.ModuleType("routers.chat.deps")
    deps.get_current_user_db = _async_none
    sys.modules.setdefault("routers.chat.deps", deps)


# ── Fake Mongo ───────────────────────────────────────────────────────
def _match(doc: dict, filt: dict) -> bool:
    for k, v in filt.items():
        if isinstance(v, dict) and "$exists" in v:
            if bool(v["$exists"]) != (k in doc):
                return False
            continue
        if doc.get(k) != v:
            return False
    return True


def _set_dotted(doc: dict, dotted: str, value) -> None:
    """$set with dotted paths. Numeric segments address list items;
    an out-of-range index pads with None, exactly like real Mongo
    (the Iter 34 sparse-turns behaviour)."""
    parts = dotted.split(".")
    cur = doc
    for part in parts[:-1]:
        if part.isdigit():
            idx = int(part)
            while len(cur) <= idx:
                cur.append(None)
            if cur[idx] is None:
                cur[idx] = {}
            cur = cur[idx]
        else:
            if cur.get(part) is None:
                cur[part] = {}
            cur = cur[part]
    last = parts[-1]
    if last.isdigit() and isinstance(cur, list):
        idx = int(last)
        while len(cur) <= idx:
            cur.append(None)
        cur[idx] = value
    else:
        cur[last] = value


class FakeCollection:
    """In-memory stand-in for a Motor collection (subset of ops)."""

    def __init__(self, docs=None):
        self.docs: list[dict] = list(docs or [])

    async def find_one(self, filt, projection=None, sort=None):
        rows = [d for d in self.docs if _match(d, filt)]
        if sort:
            key, direction = sort[0]
            rows.sort(key=lambda d: d.get(key) or 0,
                      reverse=(direction == -1))
        return rows[0] if rows else None

    async def update_one(self, filt, update, upsert=False):
        for doc in self.docs:
            if _match(doc, filt):
                self._apply(doc, update)
                return SimpleNamespace(matched_count=1, modified_count=1,
                                       upserted_id=None)
        if upsert:
            doc = {k: v for k, v in filt.items()
                   if not isinstance(v, dict)}
            for k, v in (update.get("$setOnInsert") or {}).items():
                _set_dotted(doc, k, v)
            self._apply(doc, update)
            self.docs.append(doc)
            return SimpleNamespace(matched_count=0, modified_count=0,
                                   upserted_id="fake-id")
        return SimpleNamespace(matched_count=0, modified_count=0,
                               upserted_id=None)

    def _apply(self, doc: dict, update: dict) -> None:
        for k, v in (update.get("$set") or {}).items():
            _set_dotted(doc, k, v)
        for k, v in (update.get("$inc") or {}).items():
            parts = k.split(".")
            cur = doc
            for part in parts[:-1]:
                cur = cur.setdefault(part, {})
            cur[parts[-1]] = cur.get(parts[-1], 0) + v
        for k, v in (update.get("$push") or {}).items():
            each = v.get("$each", [])
            arr = doc.setdefault(k, [])
            arr.extend(each)
            if "$slice" in v and isinstance(v["$slice"], int):
                n = v["$slice"]
                if n < 0:
                    del arr[:max(0, len(arr) + n)]

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id="fake-id")

    async def count_documents(self, filt):
        return sum(1 for d in self.docs if _match(d, filt))


class FakeDB:
    """Attribute + item access to named FakeCollections."""

    def __init__(self, **collections):
        self._collections = {name: FakeCollection(docs)
                             for name, docs in collections.items()}

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self._collections.setdefault(name, FakeCollection())

    def __getitem__(self, name):
        return self._collections.setdefault(name, FakeCollection())
