"""fix_items layer — additive over cto_open_findings (checkup-first PR 1).

Behavioural: drives the real functions against an in-memory collection
that mimics the Motor calls the layer makes.
"""
from __future__ import annotations

import asyncio

from services import fix_items as fi


class _Res:
    def __init__(self, upserted_id=None):
        self.upserted_id = upserted_id


class _Coll:
    def __init__(self):
        self.rows: list[dict] = []

    def _match(self, r, q):
        return all(r.get(k) == v for k, v in q.items())

    async def find_one(self, q, projection=None, sort=None):
        hits = [dict(r) for r in self.rows if self._match(r, q)]
        return hits[-1] if sort else (hits[0] if hits else None)

    async def update_one(self, q, upd, upsert=False):
        row = next((r for r in self.rows if self._match(r, q)), None)
        created = None
        if row is None:
            if not upsert:
                return _Res()
            row = dict(q)  # Mongo copies equality fields of the filter on upsert
            row.update(upd.get("$setOnInsert", {}))
            self.rows.append(row)
            created = len(self.rows)
        row.update(upd.get("$set", {}))
        return _Res(created)

    async def insert_one(self, doc):
        self.rows.append(dict(doc))

    def find(self, q, proj=None):
        rows = [dict(r) for r in self.rows if self._match(r, q)]

        class _It:
            def __aiter__(s):
                s._i = iter(rows)
                return s

            async def __anext__(s):
                try:
                    return next(s._i)
                except StopIteration:
                    raise StopAsyncIteration
        return _It()


class _DB:
    def __init__(self):
        self.cto_open_findings = _Coll()
        self.feature_flags = _Coll()
        self.checkup_events = _Coll()
        self.project_checkups = _Coll()


def run(c):
    return asyncio.run(c)


def test_legacy_row_projects_without_losing_fields():
    legacy = {"finding_id": "security::a.py:3:r1", "project_id": "p", "scanner": "security",
              "rule_id": "r1", "severity": "high", "file": "a.py", "line": 3,
              "message": "m", "status": "aged-out", "first_seen_at": 1}
    item = fi.to_fix_item(legacy)
    assert item["status"] == "ignored" and item["stage"] == "safe"
    assert item["evidence"]["file"] == "a.py"
    assert "a.py" not in item["title_plain"] + item["impact_plain"]  # S10
    assert "status" in legacy and legacy["status"] == "aged-out"  # not mutated


def test_upsert_adds_fields_and_keeps_legacy_keys():
    db = _DB()
    run(fi.upsert_item(db, user_id="u", project_id="p",
                       item={"scanner": "database", "rule_id": "rls_off", "severity": "critical",
                             "evidence": {"file": "m.sql", "line": 1}}))
    row = db.cto_open_findings.rows[0]
    for k in ("finding_id", "scanner", "rule_id", "severity", "file", "line", "status", "first_seen_at"):
        assert k in row
    assert row["stage"] == "permissions" and row["status"] == "open"


def test_fixed_item_redetected_becomes_regressed_and_ignored_stays():
    db = _DB()
    item = {"id": "x", "scanner": "security", "rule_id": "r", "severity": "high"}
    run(fi.upsert_item(db, user_id="u", project_id="p", item=item))
    assert run(fi.set_status(db, user_id="u", project_id="p", item_id="x", new_status="fixing"))
    assert run(fi.set_status(db, user_id="u", project_id="p", item_id="x",
                             new_status="fixed", fixed_by="proof_rerun"))
    assert run(fi.upsert_item(db, user_id="u", project_id="p", item=item)) == "regressed"
    run(fi.set_status(db, user_id="u", project_id="p", item_id="x", new_status="ignored"))
    assert run(fi.upsert_item(db, user_id="u", project_id="p", item=item)) == "ignored"


def test_fixed_requires_proof_and_valid_transition():
    db = _DB()
    run(fi.upsert_item(db, user_id="u", project_id="p", item={"id": "x", "scanner": "security"}))
    assert not run(fi.set_status(db, user_id="u", project_id="p", item_id="x", new_status="fixed"))
    run(fi.set_status(db, user_id="u", project_id="p", item_id="x", new_status="fixing"))
    assert not run(fi.set_status(db, user_id="u", project_id="p", item_id="x", new_status="fixed"))
    assert not run(fi.set_status(db, user_id="u", project_id="p", item_id="nope", new_status="fixing"))


def test_score_is_the_existing_curve_and_ignores_closed_items():
    from services.codebase_health_core import _score_for_findings
    items = [{"severity": "critical", "status": "open"}, {"severity": "high", "status": "open"},
             {"severity": "critical", "status": "fixed"}, {"severity": "critical", "status": "ignored"}]
    assert fi.score_for(items) == _score_for_findings([{"severity": "critical"}, {"severity": "high"}])
    assert fi.score_for([]) == 100


def test_stage_summary_verdicts():
    items = [{"stage": "safe", "severity": "critical", "status": "open"},
             {"stage": "launch", "severity": "low", "status": "open"}]
    got = {s["stage"]: s["verdict"] for s in fi.stage_summary(items)}
    assert got == {"runs": "ok", "safe": "problem", "permissions": "ok", "launch": "attention"}


def test_ensure_flags_default_off_and_never_flips_existing():
    db = _DB()
    db.feature_flags.rows.append({"flag": "checkup_flow", "enabled": True})
    created = run(fi.ensure_flags(db))
    assert created == len(fi.CHECKUP_FLAGS) - 1
    by = {r["flag"]: r for r in db.feature_flags.rows}
    assert by["checkup_flow"]["enabled"] is True
    assert all(not by[f]["enabled"] for f in fi.CHECKUP_FLAGS if f != "checkup_flow")
    assert run(fi.ensure_flags(db)) == 0


def test_events_validated_and_never_raise():
    db = _DB()
    assert run(fi.record_event(db, user_id="u", project_id="p", event="checkup_started"))
    assert not run(fi.record_event(db, user_id="u", project_id="p", event="bogus"))

    class _Bad:
        class checkup_events:
            @staticmethod
            async def insert_one(d):
                raise RuntimeError("down")
    assert run(fi.record_event(_Bad, user_id="u", project_id="p", event="fix_clicked")) is False


def test_list_items_hides_closed_by_default():
    db = _DB()
    for i, st in enumerate(("open", "ignored")):
        db.cto_open_findings.rows.append({"user_id": "u", "project_id": "p", "finding_id": f"f{i}",
                                          "scanner": "security", "severity": "high", "status": st})
    assert [i["id"] for i in run(fi.list_items(db, user_id="u", project_id="p"))] == ["f0"]
    assert len(run(fi.list_items(db, user_id="u", project_id="p", include_closed=True))) == 2
