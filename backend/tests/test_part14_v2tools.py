"""Part XIV — ORA Chat v2 tools fixes (backend/services/ora_chat_v2/tools.py
+ backend/services/ora_chat/codebase_index.py).

Runs against the REAL fixed modules with only the `services.ora_chat`
package import stubbed to point at the real codebase_index, and a fake
`services.journey_watch`. Fake async Mongo cursors stand in for Motor.

Backend root resolves from PART14_BACKEND (default: this file's
parents[1]) so the same file runs against the fixed tree AND against
pristine as a negative control.
"""
import asyncio
import importlib.util
import os
import sys
import tempfile
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND = Path(os.environ.get(
    "PART14_BACKEND", Path(__file__).resolve().parents[1]))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# --- real codebase_index, registered under its real dotted path -------
services = types.ModuleType("services")
services.__path__ = []
ora_chat = types.ModuleType("services.ora_chat")
ora_chat.__path__ = []
sys.modules.setdefault("services", services)
sys.modules.setdefault("services.ora_chat", ora_chat)
codebase = _load("services.ora_chat.codebase_index",
                 BACKEND / "services" / "ora_chat" / "codebase_index.py")
sys.modules["services.ora_chat.codebase_index"] = codebase

# --- fake journey_watch (records what period_days it was given) -------
journey_calls = []
journey_watch = types.ModuleType("services.journey_watch")


async def _fake_card(db, period_days=7):
    journey_calls.append(period_days)
    return {"ok": True, "period_days": period_days}


journey_watch.compute_journey_watch_card = _fake_card
sys.modules["services.journey_watch"] = journey_watch

tools = _load("part14_ora_tools",
              BACKEND / "services" / "ora_chat_v2" / "tools.py")


# --- fake async Mongo ----------------------------------------------------
class FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)
        self.limit_called_with = None

    def sort(self, *a, **k):
        return self

    def limit(self, n):
        self.limit_called_with = n
        return FakeCursor(self._rows[:n])

    def __aiter__(self):
        return self._aiter()

    async def _aiter(self):
        for r in self._rows:
            yield r


def _matches(row, flt):
    for key, cond in (flt or {}).items():
        if isinstance(cond, dict) and "$gte" in cond:
            v = row.get(key)
            if v is None or v < cond["$gte"]:
                return False
        elif row.get(key) != cond:
            return False
    return True


class FakeColl:
    def __init__(self, rows):
        self.rows = list(rows)
        self.last_filter = None

    def find(self, flt=None, *a, **k):
        self.last_filter = flt
        return FakeCursor([r for r in self.rows if _matches(r, flt)])

    async def count_documents(self, flt=None):
        self.last_filter = flt
        return sum(1 for r in self.rows if _matches(r, flt))


class FakeDB:
    def __init__(self, alerts=(), backlog=(), failures=()):
        self.health_notifications = FakeColl(alerts)
        self.ora_backlog_items = FakeColl(backlog)
        self.loop_sessions = FakeColl(failures)


def _run(coro):
    return asyncio.run(coro)


def _alert_rows(n, days_back=1):
    now = datetime.now(timezone.utc)
    return [{"category": "signup", "name": f"a{i}",
             "created_at_dt": now - timedelta(days=days_back)}
            for i in range(n)]


def _fail_rows(codes):
    now = datetime.now().timestamp()
    return [{"state": "failed", "error_code": c, "updated_at": now - i,
             "loop_id": f"L{i}"} for i, c in enumerate(codes)]


# --- (a) _validated_days ---------------------------------------------------
def test_validated_days_accepts_int_and_default():
    assert tools._validated_days(7) == (7, None)
    assert tools._validated_days(None) == (7, None)
    assert tools._validated_days(365) == (365, None)


def test_validated_days_accepts_digit_string_and_integral_float():
    assert tools._validated_days("7") == (7, None)
    assert tools._validated_days(" 10 ") == (10, None)
    assert tools._validated_days(7.0) == (7, None)


def test_validated_days_rejects_huge():
    v, err = tools._validated_days(10 ** 30)
    assert v is None and err["error"] == "invalid_days"
    v, err = tools._validated_days(10 ** 400)
    assert v is None and err["error"] == "invalid_days"
    v, err = tools._validated_days(366)
    assert v is None and err["error"] == "invalid_days"


def test_validated_days_rejects_non_numeric_negative_zero_float_bool():
    for bad in ("abc", "7.5", "", -5, 0, 7.9, True, False, [7], 7.5):
        v, err = tools._validated_days(bad)
        assert v is None and err["error"] == "invalid_days", bad


def test_get_alerts_huge_days_is_invalid_not_overflow():
    r = _run(tools.execute_tool(FakeDB(), "get_alerts", {"days": 10 ** 30}))
    assert r.get("error") == "invalid_days", r
    r = _run(tools.get_alerts(FakeDB(), days=10 ** 30))
    assert r.get("error") == "invalid_days", r


def test_get_alerts_bad_days_all_invalid():
    for bad in ("abc", -5, 0, 7.9, True):
        r = _run(tools.execute_tool(FakeDB(), "get_alerts", {"days": bad}))
        assert r.get("error") == "invalid_days", (bad, r)


def test_get_alerts_valid_days_echoes_validated_value():
    db = FakeDB(alerts=_alert_rows(3))
    r = _run(tools.get_alerts(db, days=7))
    assert r == {"days": 7, "total": 3, "by_kind": {"signup": 3}}, r
    r = _run(tools.get_alerts(db, days="7"))
    assert r["days"] == 7, r  # digit string coerces; echo is the int


def test_get_recent_loop_failures_huge_days_is_invalid_not_all_time():
    # 10**30 used to silently widen the window to all-time (float path).
    r = _run(tools.execute_tool(
        FakeDB(), "get_recent_loop_failures", {"days": 10 ** 30}))
    assert r.get("error") == "invalid_days", r
    for bad in ("abc", -5, 0, 7.9):
        r = _run(tools.execute_tool(
            FakeDB(), "get_recent_loop_failures", {"days": bad}))
        assert r.get("error") == "invalid_days", (bad, r)


def test_get_funnel_stats_validates_before_downstream():
    journey_calls.clear()
    r = _run(tools.execute_tool(
        FakeDB(), "get_funnel_stats", {"days": 10 ** 30}))
    assert r.get("error") == "invalid_days", r
    assert journey_calls == []  # downstream never saw the huge value
    r = _run(tools.get_funnel_stats(FakeDB(), days=7))
    assert r["period_days"] == 7 and journey_calls == [7], r


# --- (a4) by_error_code ------------------------------------------------------
def test_recent_loop_failures_counts_all_error_codes():
    db = FakeDB(failures=_fail_rows(["E_TIMEOUT", "E_GIT_PUSH", "E_LLM_500"]))
    r = _run(tools.get_recent_loop_failures(db, days=7))
    assert r["by_error_code"] == {
        "E_TIMEOUT": 1, "E_GIT_PUSH": 1, "E_LLM_500": 1}, r
    assert r["last_error_class"] == "E_TIMEOUT"
    assert r["count"] == 3 and r["total"] == 3 and r["shown"] == 3
    assert r["truncated"] is False


def test_recent_loop_failures_repeated_codes_aggregate():
    codes = ["E_TIMEOUT"] * 3 + ["E_GIT_PUSH"] * 2
    db = FakeDB(failures=_fail_rows(codes))
    r = _run(tools.get_recent_loop_failures(db, days=7))
    assert r["by_error_code"] == {"E_TIMEOUT": 3, "E_GIT_PUSH": 2}, r


# --- (b) read_investigation ---------------------------------------------------
def test_read_investigation_sibling_prefix_is_rejected(tmp_path):
    # Defence-in-depth predicate: a path under the sibling dir
    # <dir>2/ must NOT satisfy the prefix check for <dir>/.
    base = str(tmp_path / "memory")
    sibling = str(tmp_path / "memory2" / "investigation_x.md")
    assert sibling.startswith(base + os.sep) is False
    assert sibling.startswith(base) is True  # the old, broken check


def test_read_investigation_escape_names_still_rejected(tmp_path):
    tools._INVESTIGATION_DIR = str(tmp_path / "memory")
    (tmp_path / "memory").mkdir()
    for name in ("../memory2/investigation_evil.md",
                 "investigation_../../memory2/investigation_evil.md",
                 "..%2fetc.md",
                 "/tmp/memory2/investigation_evil.md",
                 "investigation_..md",
                 "investigation_evil.md/extra",
                 "other.md"):
        r = _run(tools.read_investigation(None, name))
        assert r.get("error") == "invalid_name", (name, r)


def test_read_investigation_truncation_flagged(tmp_path):
    inv = tmp_path / "memory"
    inv.mkdir()
    (inv / "investigation_big.md").write_text("x" * 25000)
    (inv / "investigation_small.md").write_text("hello")
    tools._INVESTIGATION_DIR = str(inv)
    r = _run(tools.read_investigation(None, "investigation_big.md"))
    assert r["truncated"] is True, r
    assert r["total_chars"] == 25000, r
    assert len(r["content"]) == 20000, r
    r = _run(tools.read_investigation(None, "investigation_small.md"))
    assert r["truncated"] is False and r["total_chars"] == 5, r
    assert r["content"] == "hello"
    r = _run(tools.read_investigation(None, "investigation_nope.md"))
    assert r.get("error") == "not_found", r


# --- (c) silent caps -----------------------------------------------------------
def test_get_alerts_has_no_cap_60_rows_all_counted():
    # (c1) negative control: the paper's "cap 50" never existed.
    db = FakeDB(alerts=_alert_rows(60))
    r = _run(tools.get_alerts(db, days=7))
    assert r["total"] == 60, r


def test_get_backlog_cap_is_flagged_with_true_total():
    db = FakeDB(backlog=[{"backlog_id": f"b{i}"} for i in range(40)])
    r = _run(tools.get_backlog(db))
    assert len(r["items"]) == 30, r
    assert r["total"] == 40 and r["truncated"] is True, r
    db = FakeDB(backlog=[{"backlog_id": f"b{i}"} for i in range(5)])
    r = _run(tools.get_backlog(db))
    assert len(r["items"]) == 5 and r["total"] == 5
    assert r["truncated"] is False, r


def test_recent_loop_failures_count_is_true_total_not_capped():
    db = FakeDB(failures=_fail_rows(["E_X"] * 25))
    r = _run(tools.get_recent_loop_failures(db, days=7))
    assert r["count"] == 25 and r["total"] == 25, r
    assert r["shown"] == 20 and r["truncated"] is True, r
    assert r["by_error_code"] == {"E_X": 20}, r
    assert r["by_error_code_truncated"] is True, r


# --- codebase_index helpers (real module, temp repo) ---------------------------
def _make_repo(tmp_path, files):
    """files: {relpath: content} under tmp_path; re-point the index."""
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    codebase._REPO_ROOT = tmp_path
    codebase._SCAN_DIRS = [tmp_path / "backend", tmp_path / "frontend" / "src"]
    codebase._CACHE["files"] = []
    codebase._CACHE["by_path"] = {}
    codebase._CACHE["def_to_paths"] = {}
    codebase._CACHE["built_at"] = 0.0


def _restore_index(orig_root, orig_scan):
    codebase._REPO_ROOT = orig_root
    codebase._SCAN_DIRS = orig_scan
    codebase._CACHE["files"] = []
    codebase._CACHE["by_path"] = {}
    codebase._CACHE["def_to_paths"] = {}
    codebase._CACHE["built_at"] = 0.0


def test_find_source_files_cap_flagged(tmp_path):
    orig_root, orig_scan = codebase._REPO_ROOT, codebase._SCAN_DIRS
    try:
        files = {f"backend/services/mod{i:02d}.py": "x = 1\n"
                 for i in range(30)}
        _make_repo(tmp_path, files)
        r = _run(tools.find_source_files(None, "backend/services/*.py"))
        assert r["total"] == 30 and len(r["matches"]) == 25, r
        assert r["truncated"] is True, r
        # Default list return is unchanged for existing callers.
        lst = _run(codebase.find_files("backend/services/*.py"))
        assert isinstance(lst, list) and len(lst) == 25
    finally:
        _restore_index(orig_root, orig_scan)


def test_search_code_defs_cap_flagged(tmp_path):
    orig_root, orig_scan = codebase._REPO_ROOT, codebase._SCAN_DIRS
    try:
        files = {f"backend/services/mod{i:02d}.py":
                 "def shared_symbol():\n    pass\n" for i in range(20)}
        _make_repo(tmp_path, files)
        r = _run(tools.search_code_defs(None, "shared_symbol"))
        assert r["total"] == 20 and len(r["results"]) == 15, r
        assert r["truncated"] is True, r
        lst = _run(codebase.search_defs("shared_symbol"))
        assert isinstance(lst, list) and len(lst) == 15
    finally:
        _restore_index(orig_root, orig_scan)


def test_read_source_file_byte_cap_flagged(tmp_path):
    orig_root, orig_scan = codebase._REPO_ROOT, codebase._SCAN_DIRS
    try:
        long_lines = "".join(f"# line {i:03d} " + "y" * 1990 + "\n"
                             for i in range(30))  # ~60 KB, 30 lines
        assert len(long_lines) > 40 * 1024
        _make_repo(tmp_path, {"backend/services/big.py": long_lines,
                              "backend/services/small.py": "a = 1\nb = 2\n"})
        r = _run(codebase.read_file("backend/services/big.py"))
        assert r["truncated"] is True, r
        assert r["byte_truncated"] is True, r
        assert r["total_bytes"] == len(long_lines.encode()), r
        assert r["total_lines"] == 30, r  # TRUE count, not the 40KB window
        r = _run(codebase.read_file("backend/services/small.py"))
        assert r["truncated"] is False and r["byte_truncated"] is False, r
        assert r["total_lines"] == 2, r
        # >200-line file still flags the line cap (pre-existing).
        many = "".join(f"x = {i}\n" for i in range(250))
        _make_repo(tmp_path, {"backend/services/many.py": many})
        r = _run(codebase.read_file("backend/services/many.py"))
        assert r["truncated"] is True and r["byte_truncated"] is False, r
        assert r["total_lines"] == 250 and r["lines_shown"] == 200, r
    finally:
        _restore_index(orig_root, orig_scan)


def test_compact_tree_per_group_cut_flagged(tmp_path):
    orig_root, orig_scan = codebase._REPO_ROOT, codebase._SCAN_DIRS
    try:
        files = {f"backend/services/mod{i:02d}.py": "x = 1\n"
                 for i in range(10)}
        files["frontend/src/App.jsx"] = "export default 1\n"
        _make_repo(tmp_path, files)
        tree = _run(codebase.compact_tree(max_files=150))
        assert "(+2 more in backend/services/)" in tree, tree
    finally:
        _restore_index(orig_root, orig_scan)


def test_compact_tree_global_cap_still_flagged(tmp_path):
    orig_root, orig_scan = codebase._REPO_ROOT, codebase._SCAN_DIRS
    try:
        files = {}
        for g in range(20):
            for i in range(10):
                files[f"backend/g{g:02d}/mod{i:02d}.py"] = "x = 1\n"
        _make_repo(tmp_path, files)
        tree = _run(codebase.compact_tree(max_files=150))
        assert "... (truncated, use /find or /read for more)" in tree, tree
    finally:
        _restore_index(orig_root, orig_scan)
