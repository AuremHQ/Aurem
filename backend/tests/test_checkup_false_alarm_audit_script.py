"""The false-alarm audit script reads a real git checkout read-only and reports
checkup findings without ever printing secret values."""
from __future__ import annotations

import subprocess
import sys

from scripts import checkup_false_alarm_audit as audit
from services import checkup_meta as cm


def _repo(tmp_path):
    def git(*a):
        subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True, capture_output=True)
    git("init", "-q")
    (tmp_path / "package.json").write_text('{"dependencies": {"react": "18"}}')
    (tmp_path / "src").mkdir()
    (tmp_path / "src/App.jsx").write_text("import X from './Nope';\nexport default () => null;\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules/x.js").write_text("ignored")
    git("add", "-A")
    git("commit", "-q", "-m", "init")
    return str(tmp_path)


def test_audit_reads_checkout_and_reports_findings(tmp_path):
    repo = _repo(tmp_path)
    tc, total = audit.build_cache(repo, "HEAD", 5)
    assert "src/App.jsx" in tc and not any(k.startswith("node_modules") for k in cm.strip_meta(tc))
    assert total == 3 and "src/App.jsx" in cm.repo_paths(tc)
    out = audit.summarize(audit.run_checkup(tc, include_scanners=False), total)
    assert "unresolved_import" in {i["rule"] for i in out["items"]}


def test_audit_cli_prints_counts_and_exits_zero(tmp_path):
    repo = _repo(tmp_path)
    r = subprocess.run([sys.executable, "scripts/checkup_false_alarm_audit.py", repo], capture_output=True, text=True)
    assert r.returncode == 0 and "unresolved_import" in r.stdout
