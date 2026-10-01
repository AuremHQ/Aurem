"""Checkup v2 foundation — repo meta travels with the file cache without
leaking into scanners, and a missing fact is "couldn't verify", not "clear"."""
from __future__ import annotations

import asyncio

import pytest

from services import checkup_meta as cm
from services.checkup_orchestrator import iter_checkup, run_checkup


def test_meta_round_trips_and_strips():
    tc = cm.with_meta({"a.py": "x"}, [".env", "a.py"], {".gitignore": "node_modules\n"})
    assert cm.repo_paths(tc) == [".env", "a.py"]
    assert cm.meta_file(tc, ".gitignore") == "node_modules\n"
    assert cm.strip_meta(tc) == {"a.py": "x"}


def test_absent_file_is_none_but_unfetched_raises():
    tc = cm.with_meta({}, ["a.py"], {})
    assert cm.meta_file(tc, ".gitignore") is None
    with pytest.raises(cm.MetaUnavailable):
        cm.repo_paths({"a.py": "x"})
    with pytest.raises(cm.MetaUnavailable):
        cm.meta_file({"a.py": "x"}, ".gitignore")


def test_existing_checks_ignore_meta_keys():
    plain = {"package.json": '{"dependencies":{"react":"18"}}',
             "src/App.jsx": "export default function A(){return <p>hi</p>}\n"}
    with_m = cm.with_meta(plain, ["package.json", "src/App.jsx"], {})
    meta_only = {"no_project_memory"}        # needs the file list, so only appears when meta is present
    assert {i["rule_id"] for i in run_checkup(plain, include_scanners=False)["items"]} == \
           {i["rule_id"] for i in run_checkup(with_m, include_scanners=False)["items"]} - meta_only


def test_scanners_never_see_meta_keys(monkeypatch):
    seen = {}

    def fake(tc):
        seen["keys"] = set(tc)
        return []
    monkeypatch.setattr("services.codebase_health_core.SCANNERS", {"probe": fake})
    tc = cm.with_meta({"a.py": "x"}, ["a.py"], {".gitignore": "x"})
    list(iter_checkup(tc))
    assert seen["keys"] == {"a.py"}


def test_check_needing_missing_meta_is_reported_unverified(monkeypatch):
    def needs_paths(tc):
        return [{"scanner": "x", "rule_id": "r", "severity": "low",
                 "evidence": {"file": p, "line": 0}} for p in cm.repo_paths(tc)]
    monkeypatch.setattr("services.checkup_orchestrator.CHECKS", [("safe", "probe", needs_paths)])
    res = run_checkup({"a.py": "x"}, include_scanners=False)
    assert res["items"] == []
    assert [u["check"] for u in res["unverified"]] == ["probe"]


def test_read_with_meta_degrades_when_tree_fetch_fails(monkeypatch):
    async def cache(*_a):
        return {"a.py": "x"}

    async def boom(*_a):
        raise RuntimeError("github down")
    monkeypatch.setattr(cm, "fetch_repo_meta", boom)
    tc = asyncio.run(cm.read_with_meta("o", "r", "t", cache))
    assert tc == {"a.py": "x"}
    with pytest.raises(cm.MetaUnavailable):
        cm.repo_paths(tc)


def test_read_with_meta_attaches_meta(monkeypatch):
    async def cache(*_a):
        return {"a.py": "x"}

    async def ok(*_a):
        return [".env", "a.py"], {".gitignore": "dist\n"}
    monkeypatch.setattr(cm, "fetch_repo_meta", ok)
    tc = asyncio.run(cm.read_with_meta("o", "r", "t", cache))
    assert cm.repo_paths(tc) == [".env", "a.py"] and cm.meta_file(tc, ".gitignore") == "dist\n"


def test_unverified_confidence_items_are_never_shown_as_findings(monkeypatch):
    def unsure(_tc):
        return [{"scanner": "x", "rule_id": "maybe_bad", "severity": "critical",
                 "confidence": "unverified", "evidence": {"file": "a.py", "line": 1}}]
    monkeypatch.setattr("services.checkup_orchestrator.CHECKS", [("safe", "probe", unsure)])
    res = run_checkup({"a.py": "x"}, include_scanners=False)
    assert res["items"] == [] and res["score"] == run_checkup({}, include_scanners=False)["score"]
    assert [u["check"] for u in res["unverified"]] == ["maybe_bad"]
