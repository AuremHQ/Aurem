"""tests/test_code_quality_batch1_helpers_2026_11_21.py

Code Quality batch 1: eight functions over the complexity limit were split
into small helpers with NO behaviour change. The existing suites already
exercise the public functions; these tests pin the behaviour of the new
helpers (especially the branches the existing suites never reached, such as
the cost-alert recipient lookup) so a later edit cannot change it silently.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


# ── services/ora_chat/cost_tracker.py::_resolve_alert_recipient ───────
class _Users:
    def __init__(self, row=None, exc=None):
        self.row, self.exc = row, exc

    async def find_one(self, *a, **k):
        if self.exc:
            raise self.exc
        return self.row


class _Db:
    def __init__(self, row=None, exc=None):
        self.dev_users = _Users(row, exc)


async def test_alert_recipient_env_wins_over_the_database(monkeypatch):
    from services.ora_chat.cost_tracker import _resolve_alert_recipient
    monkeypatch.setenv("FOUNDER_EMAIL", "  founder@example.com ")
    assert await _resolve_alert_recipient(_Db({"email": "db@example.com"})) == "founder@example.com"


async def test_alert_recipient_falls_back_to_the_database(monkeypatch):
    from services.ora_chat.cost_tracker import _resolve_alert_recipient
    monkeypatch.delenv("FOUNDER_EMAIL", raising=False)
    assert await _resolve_alert_recipient(_Db({"email": "db@example.com"})) == "db@example.com"


@pytest.mark.parametrize("row", [None, {}, {"email": None}, {"email": ""}])
async def test_alert_recipient_empty_when_nothing_usable(monkeypatch, row):
    from services.ora_chat.cost_tracker import _resolve_alert_recipient
    monkeypatch.delenv("FOUNDER_EMAIL", raising=False)
    assert await _resolve_alert_recipient(_Db(row)) == ""


async def test_alert_recipient_survives_a_database_error(monkeypatch):
    from services.ora_chat.cost_tracker import _resolve_alert_recipient
    monkeypatch.delenv("FOUNDER_EMAIL", raising=False)
    assert await _resolve_alert_recipient(_Db(exc=RuntimeError("db down"))) == ""


# ── core/parliament/scoring.py::_score_code_fix ───────────────────────
@pytest.mark.parametrize("text,expected", [
    ("def a(): import os; return {1}; from x import y; class Z: pass", 0.92),  # 7 markers
    ("def import return from {", 0.78),                                        # 5 markers: below the 6 cut-off
    ("def import return from { }", 0.92),                                     # exactly 6 markers
    ("def a(): return {", 0.78),                                               # exactly 3 markers (def, return, {)
    ("def a(): return 1", 0.60),                                               # exactly 2 markers
    ("const x", 0.60),                                                         # 1 marker
    ("plain prose with none of them", 0.30),
    ("", 0.30),
])
async def test_score_code_fix_thresholds(text, expected):
    from core.parliament.scoring import _score_code_fix
    assert _score_code_fix(text) == expected


async def test_score_output_routes_code_fix_through_the_helper():
    from core.parliament.scoring import _score_code_fix, _score_output
    text = "def f():\n    return 1  # " + "x" * 80
    assert _score_output(text, task_type="code_fix") == _score_code_fix(text.strip())
    assert _score_output("", task_type="code_fix") == 0.0
    assert _score_output("short", task_type="code_fix") == 0.2
    assert _score_output("x" * 100, task_type="something_else") == 0.65


# ── routers/visibility.py::_score_items ───────────────────────────────
def _item(key, weight):
    return {"key": key, "name": key, "what_why": "", "weight": weight, "mode": "m"}


async def test_score_items_full_half_and_no_credit():
    from routers.visibility import _score_items
    items = [_item("a", 10), _item("b", 10), _item("c", 10), _item("d", 10)]
    states = {"a": {"status": "live"}, "b": {"status": "pr_merged"},
              "c": {"status": "pr_created"}}            # d is missing
    rows, score = _score_items(items, states)
    assert score == round(100 * (10 + 10 + 5) / 40)
    assert [r["status"] for r in rows] == ["live", "pr_merged", "pr_created", "missing"]


async def test_score_items_empty_catalog_scores_zero():
    from routers.visibility import _score_items
    assert _score_items([], {}) == ([], 0)


# ── services/project_memory.py::_clip_known_fields ────────────────────
async def test_clip_known_fields_keeps_only_known_non_empty_fields():
    from services.project_memory import _clip_known_fields
    out = _clip_known_fields({
        "stack": "fastapi", "test_command": "", "style_notes": 123,    # empty / wrong type dropped
        "fixes_that_worked": ["a", "b"], "user_rejected": [],          # empty list dropped
        "unknown_field": "ignored",
    })
    assert out == {"stack": "fastapi", "fixes_that_worked": ["a", "b"]}
    assert _clip_known_fields(None) == {} and _clip_known_fields({}) == {}


# ── services/loop_verify.py helpers ───────────────────────────────────
async def test_valid_files_drops_malformed_entries():
    from services.loop_verify import _valid_files
    good = {"path": "a.py", "content": "x = 1\n"}
    assert _valid_files([good, None, "s", {"path": "", "content": "x"}, {"path": "b.py"},
                         {"path": "c.py", "content": None}, {"path": "d.py", "content": ""}]) == [
        good, {"path": "d.py", "content": ""}]
    assert _valid_files(None) == [] and _valid_files([]) == []


async def test_first_diagnostic_prefers_stdout_then_stderr_and_skips_blank_lines():
    from services.loop_verify import _first_diagnostic
    assert _first_diagnostic("\n  \n  boom here \nsecond", "ignored") == "boom here"
    assert _first_diagnostic("", "\n err line\n") == "err line"
    assert _first_diagnostic("", "") == ""


async def test_skip_row_is_an_honest_unverified_pass():
    from services.loop_verify import _skip_row
    row = _skip_row("a.xyz", "no linter mapping for .xyz")
    assert row["ok"] is True and row["verified"] is False
    assert row["linter"] == "skip" and row["error_code"] == "FILE_LANGUAGE_UNVERIFIED"


async def test_cleanup_sandbox_never_raises(monkeypatch, tmp_path):
    import services.loop_verify as lv
    d = tmp_path / "sb"
    d.mkdir()
    (d / "f").write_text("x")
    lv._cleanup_sandbox(str(d))
    assert not d.exists()

    def boom(*a, **k):
        raise OSError("nope")
    monkeypatch.setattr(lv.shutil, "rmtree", boom)
    lv._cleanup_sandbox(str(tmp_path))          # must swallow the error


async def test_verify_files_unknown_extension_is_skipped_and_ok():
    from services.loop_verify import verify_files
    out = await verify_files([{"path": "notes.unknownext", "content": "hi"}])
    assert out["ok"] is True and out["errors"] == []
    assert out["results"][0]["linter"] == "skip"
    assert await verify_files([]) == {"ok": True, "results": [], "errors": []}


# ── routers/ask.py helpers ────────────────────────────────────────────
async def test_add_llms_line_merges_repeated_urls_and_ignores_noise():
    from routers.ask import _add_llms_line
    entries, by_url = [], {}
    _add_llms_line("- [Docs](https://x.test/docs): the docs", entries, by_url)
    _add_llms_line("- [More](https://x.test/docs/): extra detail", entries, by_url)   # same URL, trailing slash
    _add_llms_line("not a link line", entries, by_url)
    assert len(entries) == 1
    assert entries[0]["url"] == "https://x.test/docs"
    assert "the docs" in entries[0]["description"] and "extra detail" in entries[0]["description"]


async def test_read_llms_text_returns_empty_when_file_missing(monkeypatch):
    from routers import ask
    monkeypatch.setattr(ask, "_llms_path", lambda: None)
    assert ask._read_llms_text() == ""
    entries = ask._load_entries()
    assert entries and all({"name", "url", "description"} <= set(e) for e in entries)   # fallback entries only


# ── routers/chat/history.py::_prepare_turns ───────────────────────────
async def test_prepare_turns_cleans_sorts_and_hides_provider_codenames():
    from routers.chat.history import _prepare_turns
    doc = {"turns": [
        None, "junk", {"content": "no role"},
        {"role": "assistant", "content": "b", "ts": 2, "provider": "deepseek-v3-council-c"},
        {"role": "user", "content": "a", "ts": 1},
    ]}
    turns = _prepare_turns(doc)
    assert [t["content"] for t in turns] == ["a", "b"]
    assert "deepseek" not in str(turns)
    assert _prepare_turns(None) == [] and _prepare_turns({}) == []


async def test_prepare_turns_keeps_only_the_last_200():
    from routers.chat.history import _prepare_turns
    doc = {"turns": [{"role": "user", "content": str(i), "ts": i} for i in range(250)]}
    turns = _prepare_turns(doc)
    assert len(turns) == 200 and turns[0]["content"] == "50" and turns[-1]["content"] == "249"


# ── routers/usage.py::_real_marketing_counts ──────────────────────────
class _Cur:
    def __init__(self, rows):
        self.rows = rows

    async def to_list(self, n):
        return self.rows


class _Coll:
    def __init__(self, rows=None, count=0, exc=None):
        self.rows, self.count, self.exc = rows or [], count, exc

    def find(self, *a, **k):
        if self.exc:
            raise self.exc
        return _Cur(self.rows)

    async def count_documents(self, *a, **k):
        if self.exc:
            raise self.exc
        return self.count


class _StatsDb:
    def __init__(self, exc=None):
        self.dev_users = _Coll([{"user_id": "u1", "email": "real@customer.io"}], exc=exc)
        self.cto_tasks = _Coll(count=3)
        self.loop_sessions = _Coll(count=4)


async def test_real_marketing_counts_sums_task_and_loop_commits():
    from routers.usage import _real_marketing_counts
    developers, commits = await _real_marketing_counts(_StatsDb())
    assert (developers, commits) == (1, 7)


async def test_real_marketing_counts_fails_soft_to_zero():
    from routers.usage import _real_marketing_counts
    assert await _real_marketing_counts(_StatsDb(exc=RuntimeError("down"))) == (0, 0)


# ── routers/usage.py::public_stats (cache, outage, payload) ───────────
class _CountColl:
    def __init__(self, estimated=0, by_filter=None, exc=None):
        self.estimated, self.by_filter, self.exc = estimated, by_filter or {}, exc

    async def estimated_document_count(self):
        if self.exc:
            raise self.exc
        return self.estimated

    async def count_documents(self, flt):
        if self.exc:
            raise self.exc
        for key, value in self.by_filter.items():
            if key in flt:
                return value
        return 0


class _StatsFullDb:
    def __init__(self, council_exc=None):
        self.ora_council_logs = _CountColl(
            estimated=100,
            by_filter={"mode": 20, "correction_applied": 5, "lint_blocked": 3},
            exc=council_exc,
        )
        self.dev_users = _Coll([{"user_id": "u1", "email": "real@customer.io"}], count=0)
        self.dev_users.estimated = 7

        async def _est():
            return 7
        self.dev_users.estimated_document_count = _est
        self.cto_tasks = _Coll(count=9)
        self.loop_sessions = _Coll(count=2)


@pytest.fixture
def fresh_stats_cache(monkeypatch):
    from routers import usage
    monkeypatch.setitem(usage._PUBLIC_STATS_CACHE, "data", None)
    monkeypatch.setitem(usage._PUBLIC_STATS_CACHE, "ts", 0.0)
    return usage


async def test_public_stats_builds_payload_and_caches_it(monkeypatch, fresh_stats_cache):
    usage = fresh_stats_cache
    monkeypatch.setattr("cto_services.db.get_db", lambda: _StatsFullDb())
    first = await usage.public_stats()
    assert first["available"] is True
    assert (first["users"], first["interactions"], first["code_tasks"]) == (7, 100, 20)
    assert (first["review_saves"], first["lint_blocks_caught"]) == (5, 3)
    assert first["review_save_rate_pct"] == 25.0           # 5 corrections / 20 code tasks
    assert first["real_developers"] == 1 and first["commits_shipped"] == 11   # 9 + 2

    monkeypatch.setattr("cto_services.db.get_db", lambda: None)               # cache hit must not touch the db
    assert await usage.public_stats() == first


async def test_public_stats_unavailable_without_database(monkeypatch, fresh_stats_cache):
    monkeypatch.setattr("cto_services.db.get_db", lambda: None)
    assert await fresh_stats_cache.public_stats() == {"available": False}


async def test_public_stats_outage_serves_stale_cache_else_unavailable(monkeypatch, fresh_stats_cache):
    usage = fresh_stats_cache
    monkeypatch.setattr("cto_services.db.get_db", lambda: _StatsFullDb(council_exc=RuntimeError("down")))
    assert await usage.public_stats() == {"available": False}                 # nothing cached yet

    usage._PUBLIC_STATS_CACHE["data"] = {"available": True, "users": 1}
    usage._PUBLIC_STATS_CACHE["ts"] = 0.0                                     # expired -> will retry the db
    assert await usage.public_stats() == {"available": True, "users": 1}      # stale beats a 500


# ── services/ora_chat/cost_tracker.py::_maybe_send_threshold_alert ────
class _Alerts:
    def __init__(self, already=None):
        self.already, self.inserted = already, []

    async def find_one(self, *a, **k):
        return self.already

    async def insert_one(self, doc):
        self.inserted.append(doc)


class _AlertDb:
    def __init__(self, already=None):
        self.ora_chat_budget_alerts = _Alerts(already)
        self.dev_users = _Users({"email": "founder@example.com"})


def _status(used_pct=90, alert_pct=80):
    return {"used_pct": used_pct, "alert_pct": alert_pct, "month": "2026-11",
            "cap_usd": 100, "spent_usd": 90, "remaining_usd": 10}


@pytest.fixture
def alert_env(monkeypatch):
    import services.ora_chat.cost_tracker as ct
    monkeypatch.delenv("FOUNDER_EMAIL", raising=False)
    sent = []

    async def _send(to, *, text, html):
        sent.append((to, text))
        return True, None

    monkeypatch.setattr("services.onboarding_email._resend_send", _send)

    def configure(db, status):
        async def _bs():
            return status
        monkeypatch.setattr(ct, "get_db", lambda: db)
        monkeypatch.setattr(ct, "budget_status", _bs)
        return ct

    return configure, sent


async def test_threshold_alert_sends_once_and_records_it(alert_env):
    configure, sent = alert_env
    db = _AlertDb()
    ct = configure(db, _status())
    await ct._maybe_send_threshold_alert()
    assert [to for to, _ in sent] == ["founder@example.com"]
    row = db.ora_chat_budget_alerts.inserted[0]
    assert row["month"] == "2026-11" and row["type"] == "threshold"
    assert row["delivered"] is True and row["error"] is None and row["used_pct"] == 90


async def test_threshold_alert_not_sent_below_threshold_or_twice(alert_env):
    configure, sent = alert_env
    db = _AlertDb()
    await configure(db, _status(used_pct=10))._maybe_send_threshold_alert()
    assert sent == [] and db.ora_chat_budget_alerts.inserted == []
    db2 = _AlertDb(already={"month": "2026-11"})
    await configure(db2, _status())._maybe_send_threshold_alert()
    assert sent == [] and db2.ora_chat_budget_alerts.inserted == []


async def test_threshold_alert_without_a_recipient_sends_nothing(alert_env):
    configure, sent = alert_env
    db = _AlertDb()
    db.dev_users = _Users(None)
    await configure(db, _status())._maybe_send_threshold_alert()
    assert sent == [] and db.ora_chat_budget_alerts.inserted == []


async def test_threshold_alert_records_a_failed_send_with_a_clipped_error(alert_env, monkeypatch):
    configure, _ = alert_env

    async def _fail(to, *, text, html):
        return False, "x" * 500

    monkeypatch.setattr("services.onboarding_email._resend_send", _fail)
    db = _AlertDb()
    await configure(db, _status())._maybe_send_threshold_alert()
    row = db.ora_chat_budget_alerts.inserted[0]
    assert row["delivered"] is False and len(row["error"]) == 200


async def test_threshold_alert_without_a_database_is_a_noop(alert_env):
    configure, sent = alert_env
    await configure(None, _status())._maybe_send_threshold_alert()
    assert sent == []
