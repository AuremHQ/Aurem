"""Regression — the Backend job must not be killed silently by its own timeout.

`Backend — pytest` has `timeout-minutes: 20`. Once Guard 21 stopped masking
it, "Run tests" was cancelled at the 20-minute mark with no summary, no
durations and no coverage.json. The step now stops pytest with SIGINT before
that (`timeout -s INT`), which must still print the slowest tests and exit 124.
"""
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

CI_YML = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


def _backend_job_and_run_step():
    jobs = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))["jobs"]
    job = next(j for j in jobs.values() if j.get("name") == "Backend — pytest")
    step = next(s for s in job["steps"] if s.get("name") == "Run tests")
    return job, step["run"]


def test_regression_iter392_run_tests_cap_is_inside_job_timeout():
    job, script = _backend_job_and_run_step()
    cap = re.search(r"timeout\s+-s\s+INT\s+(\d+)m\s+python\s+-m\s+pytest", script)
    assert cap, "Run tests must wrap pytest in `timeout -s INT <N>m`"
    assert int(cap.group(1)) < job["timeout-minutes"]
    assert "--durations=" in script


@pytest.mark.skipif(shutil.which("timeout") is None, reason="needs coreutils timeout")
def test_regression_iter392_sigint_cap_prints_durations_and_exits_124(tmp_path):
    (tmp_path / "test_slow.py").write_text(textwrap.dedent("""
        import time
        def test_a(): time.sleep(1.5)
        def test_b(): time.sleep(1.5)
        def test_c(): time.sleep(1.5)
        def test_d(): time.sleep(1.5)
    """))
    proc = subprocess.run(
        ["timeout", "-s", "INT", "4", sys.executable, "-m", "pytest", "-q",
         "--durations=3", "-p", "no:cacheprovider", "test_slow.py"],
        cwd=tmp_path, capture_output=True, text=True, timeout=60, check=False,
        env={**os.environ, "PYTEST_ADDOPTS": ""},
    )
    assert proc.returncode == 124
    assert "slowest 3 durations" in proc.stdout
    assert "KeyboardInterrupt" in proc.stdout
