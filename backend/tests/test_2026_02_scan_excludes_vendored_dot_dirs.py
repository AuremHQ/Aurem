"""
Regression for the reported bug: Codebase Health scanned a vendored
AI-tool skill-plugin directory (`.agent/skills/plugins/<marketplace
repo>/...`) as if it were the customer's own app, producing a wall of
noise findings ("135 open issues", mostly inside that tree) the
customer never wrote and doesn't own.

Root cause: `services/repo_text_cache.py`'s candidate filter only
skipped EXACT directory names (`_SKIP_DIRS` = node_modules, .git,
dist, build, .next, venv, .venv, __pycache__, .cache, coverage) and
never generalized to "any dot-prefixed directory is tooling/IDE/vendor
scaffolding" -- the exact convention `services/github_api_writer.py`'s
`_is_hidden()` already uses on the commit/write side (explicitly
naming `.agent/` as an example). The scan/read side never matched it.

Fix: new `_in_dotted_dir()` helper, wired into both the cache-hit and
fresh-fetch candidate filters in `build_text_cache()`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.repo_text_cache import _in_dotted_dir


def test_vendored_agent_skills_tree_is_excluded():
    assert _in_dotted_dir(
        ".agent/skills/plugins/antigravity-awesome-skills-claude/"
        "skills/loki-mode/benchmarks/results/humaneval-loki-solutions/160.py"
    ) is True
    assert _in_dotted_dir(
        ".agent/skills/apps/web-app/src/__tests__/"
        "refresh-skills-plugin.security.test.js"
    ) is True


def test_other_dot_prefixed_tool_dirs_are_excluded():
    for p in (".vscode/settings.json", ".idea/workspace.xml",
              ".claude/config.json", ".github/workflows/ci.yml"):
        assert _in_dotted_dir(p) is True, p


def test_real_app_files_are_not_excluded():
    assert _in_dotted_dir("backend/routers/codebase_health.py") is False
    assert _in_dotted_dir("frontend/src/App.jsx") is False


def test_dotfiles_as_filename_itself_are_not_excluded():
    """The filename segment (last segment) is exempt -- .env/.gitignore
    content IS meaningfully scanned (hardcoded-secret checks, missing-
    .gitignore checks)."""
    assert _in_dotted_dir("backend/.env") is False
    assert _in_dotted_dir(".gitignore") is False
    assert _in_dotted_dir("frontend/.env.example") is False
