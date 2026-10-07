"""Check Library v2 / Group 8 (L0.1): detection fires on bad / silent on clean;
the draft is deterministic, built from the repo, and never leaks code."""
from __future__ import annotations

import json

import pytest

from services import checkup_meta as cm
from services.project_memory import build_project_memory, check_project_memory, has_project_memory

PKG = json.dumps({"scripts": {"dev": "vite", "build": "vite build", "test": "vitest run", "postinstall": "rm -rf /"},
                  "dependencies": {"react": "18", "@supabase/supabase-js": "2"}})
APP = {"package.json": PKG, "supabase/migrations/001.sql": "create table t (id int);", "requirements.txt": "pytest\nruff\n"}


def _tc(paths, app=None):
    return cm.with_meta(dict(app or APP), paths, {})


def test_missing_rules_file_fires_medium():
    [item] = check_project_memory(_tc(["package.json", "src/App.jsx", "docs/AGENTS.md"]))
    assert item["rule_id"] == "no_project_memory" and item["severity"] == "medium"
    assert "AGENTS.md" in item["evidence"]["detail"]


@pytest.mark.parametrize("path", ["AGENTS.md", "CLAUDE.md", "claude.md", ".cursorrules", ".windsurfrules", "GEMINI.md",
                                  ".cursor/rules/style.mdc", ".github/copilot-instructions.md"])
def test_any_known_rules_file_is_silent(path):
    assert check_project_memory(_tc(["package.json", path])) == []
    assert has_project_memory([path])


def test_unfetched_file_list_is_unverified_not_clear():
    with pytest.raises(cm.MetaUnavailable):
        check_project_memory(dict(APP))


def test_draft_is_built_from_the_repo():
    files = build_project_memory(_tc(["package.json"]), "A booking app for dog groomers.")
    md = files["AGENTS.md"]
    assert "A booking app for dog groomers." in md
    assert "react + supabase + python" in md and "supabase/migrations" in md
    assert "`npm run dev`" in md and "`npm run test`" in md and "`pytest`" in md and "`ruff check .`" in md
    assert "postinstall" not in md                       # only well-known scripts are listed
    assert files["CLAUDE.md"].startswith("See [AGENTS.md](AGENTS.md)")
    assert "Never put passwords" in md and "migration" in md


def test_draft_is_deterministic_and_has_placeholders_when_unknown():
    a = build_project_memory({"a.py": "x"})
    assert a == build_project_memory({"a.py": "x"})
    assert "_Describe in one or two sentences" in a["AGENTS.md"] and "_Add the commands" in a["AGENTS.md"]


def test_draft_purpose_is_trimmed_and_single_paragraph():
    md = build_project_memory({}, "line1\n\n## injected heading\n" + "x" * 1000)["AGENTS.md"]
    assert "\n## injected heading" not in md and len(md) < 2500
