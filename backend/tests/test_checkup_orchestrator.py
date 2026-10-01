"""Checkup-first PR 2 — planted-issue vs clean fixture repos.

The planted repo contains known defects; the checkup must find each one.
The clean repo (with intentionally public things) must produce ZERO
critical/high items — a false alarm costs more trust than a miss.
"""
from __future__ import annotations

import base64
import itertools
import json

from services.checkup_orchestrator import iter_checkup, run_checkup, seo_cards_to_items


def _jwt(role: str) -> str:
    b = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return f"{b({'alg': 'HS256'})}.{b({'role': role, 'iss': 'supabase'})}.c2lnbmF0dXJlMTIzNDU2"


PKG = json.dumps({"dependencies": {"react": "18", "@supabase/supabase-js": "2"}})

PLANTED = {
    "package.json": PKG,
    "src/lib/supabase.js": f'export const key = "{_jwt("service_role")}";\n',
    "src/App.jsx": (
        "import Missing from './components/Missing';\n"
        "import axios from 'axios';\n"
        "export default function App(){\n"
        "  fetch('/api/orders');\n"
        "  return <button onClick={() => {}}>Buy</button>;\n}\n"
    ),
    "supabase/migrations/001.sql": "create table orders (id int);\n",
    "server/index.js": "app.get('/api/health', h);\n",
}

CLEAN = {
    "package.json": PKG,
    "src/lib/supabase.js": f'export const anon = "{_jwt("anon")}";\n',
    "src/components/Hello.jsx": "export default function Hello(){ return <p>hi</p>; }\n",
    "src/App.jsx": (
        "import React from 'react';\n"
        "import Hello from './components/Hello';\n"
        "export default function App(){\n"
        "  fetch('/api/health');\n"
        "  return <button onClick={() => save()}>Save</button>;\n}\n"
    ),
    "server/index.js": "app.get('/api/health', h);\n",
    "supabase/migrations/001.sql": "create table public.products (id int);\n"
                                   "alter table public.products enable row level security;\n"
                                   "create policy p on public.products for select using (true);\n",
    ".env.example": "VITE_X=\n",
}


def _rules(res):
    return {i["rule_id"] for i in res["items"]}


def test_planted_issues_are_all_found():
    res = run_checkup(PLANTED, include_scanners=False)
    got = _rules(res)
    assert {"service_role_key_in_client", "unresolved_import", "undeclared_package",
            "missing_route", "dead_button"} <= got
    sev = {i["rule_id"]: i["severity"] for i in res["items"]}
    assert sev["service_role_key_in_client"] == "critical"
    assert res["stack"]["supabase"] is True
    safe = next(s for s in res["stages"] if s["stage"] == "safe")
    assert safe["verdict"] == "problem"
    assert res["score"] < 100


def test_service_role_secret_value_never_in_output():
    res = run_checkup(PLANTED, include_scanners=False)
    blob = json.dumps(res["items"])
    assert _jwt("service_role").split(".")[2] not in blob


def test_clean_repo_has_zero_false_alarms():
    res = run_checkup(CLEAN, include_scanners=True)
    bad = [i for i in res["items"] if i["severity"] in ("critical", "high")]
    assert bad == [], bad
    assert res["score"] == 100
    assert res["unverified"] == []


def test_vendored_dirs_are_ignored():
    tc = dict(CLEAN)
    tc["node_modules/x/src/a.js"] = f'k="{_jwt("service_role")}"'
    tc[".agent/skills/src/a.js"] = "import './nope';"
    assert run_checkup(tc, include_scanners=False)["items"] == []


def test_crashing_check_is_reported_unverified_not_passed(monkeypatch):
    from services import checkup_orchestrator as o

    def boom(_):
        raise RuntimeError("x")
    monkeypatch.setattr(o, "CHECKS", [("safe", "boom", boom)])
    res = run_checkup(CLEAN, include_scanners=False)
    assert res["unverified"] == [{"stage": "safe", "check": "boom", "reason": "check failed to run"}]


def test_budget_exhaustion_marks_remaining_unverified():
    ticks = itertools.count(0, 100)
    res = run_checkup(CLEAN, include_scanners=False, budget_s=150, clock=lambda: next(ticks))
    assert res["unverified"], "later checks must be flagged, not silently skipped"


def test_progress_events_stream_in_order():
    evs = list(iter_checkup(PLANTED, include_scanners=False))
    types = [e["type"] for e in evs]
    assert types[0] == "started" and types[-1] == "done"
    assert types.index("stage_started") < types.index("items")


def test_seo_cards_fold_in_as_launch_without_paths_in_copy():
    items = seo_cards_to_items([{"path": "public/index.html", "file_label": "Home page",
                                 "bullets": ["no title"]}])
    assert items[0]["stage"] == "launch"
    assert "public/index.html" not in items[0]["title_plain"] + items[0]["impact_plain"]


def test_non_js_repo_runs_without_error():
    res = run_checkup({"app/main.py": "print(1)\n", "requirements.txt": "fastapi\n"},
                      include_scanners=False)
    assert res["stack"]["backend_python"] is True and res["items"] == []


def test_permission_stage_included_and_honest_for_other_stacks():
    res = run_checkup(PLANTED, include_scanners=False)
    assert "rls_disabled" in {i["rule_id"] for i in res["items"]}
    assert next(s for s in res["stages"] if s["stage"] == "permissions")["verdict"] == "problem"
    assert res["permissions"]["supported"] is True
    other = run_checkup({"requirements.txt": "django\n"}, include_scanners=False)
    assert other["permissions"]["supported"] is False and other["permissions"]["reason"]
