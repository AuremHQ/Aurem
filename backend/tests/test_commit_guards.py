"""S7 commit safety guards — behavioural, incl. the real incident shape
(an edit silently dropping top-level code while keeping ~90% of the file)."""
from __future__ import annotations

import asyncio

import pytest

from services import commit_guards as cg

PY_OLD = "\n".join(
    ["import os", ""] + [f"def f{i}():\n    return {i}\n" for i in range(8)]
    + ["class Keeper:\n    pass\n"])


def rules(vs):
    return {v["rule"] for v in vs}


def test_new_file_and_identical_edit_are_clean():
    assert cg.evaluate_file("a.py", None, "x = 1\n") == []
    assert cg.evaluate_file("a.py", PY_OLD, PY_OLD) == []


def test_mass_delete_and_shrink_flagged():
    new = "import os\n"
    assert {"mass_delete", "shrunk"} <= rules(cg.evaluate_file("a.py", PY_OLD, new))


def test_small_edit_to_big_file_is_fine():
    new = PY_OLD.replace("return 3", "return 33")
    assert cg.evaluate_file("a.py", PY_OLD, new) == []


def test_syntax_error_flagged_for_py_and_json():
    assert "syntax" in rules(cg.evaluate_file("a.py", None, "def x(:\n"))
    assert "syntax" in rules(cg.evaluate_file("a.json", '{"a":1}', '{"a":'))
    assert cg.evaluate_file("a.json", None, '{"a": 1}') == []


def test_dropped_function_flagged_even_when_90_percent_remains():
    new = PY_OLD.replace("def f3():\n    return 3\n", "")
    v = cg.evaluate_file("a.py", PY_OLD, new)
    assert rules(v) == {"symbol_removed"} and "f3" in v[0]["detail"]


def test_js_symbol_removal_flagged():
    old = "import a from 'a';\nexport function keep(){return 1}\nexport function lose(){return 2}\n" + "// pad\n" * 12
    new = "import a from 'a';\nexport function keep(){return 1}\n" + "// pad\n" * 12
    assert "symbol_removed" in rules(cg.evaluate_file("x.jsx", old, new))


@pytest.mark.parametrize("path", ["node_modules/x/a.py", "vendor/a.py", ".agent/s/a.py", "dist/a.py",
                                  "build/a.py", "benchmarks/a.py", ".next/a.py", "coverage/a.py", "pkg/vendor/a.py"])
def test_vendored_dirs_skipped(path):
    assert cg.evaluate_file(path, PY_OLD, "def x(:\n") == []


def test_tiny_files_do_not_trip_ratio_rules():
    assert cg.evaluate_file("a.py", "x = 1\ny = 2\n", "z = 1\n") == []


class _Resp:
    def __init__(self, status, text):
        self.status_code, self.text = status, text


class _Client:
    def __init__(self, old):
        self.old = old

    async def get(self, url, params=None, headers=None):
        return _Resp(200, self.old) if self.old is not None else _Resp(404, "")


class _Coll:
    def __init__(self):
        self.rows = []

    async def insert_one(self, d):
        self.rows.append(d)

    async def find_one(self, q):
        return self.mode_doc


class _DB:
    def __init__(self, mode=None):
        self.guardrail_events = _Coll()
        self.guard_config = _Coll()
        self.guard_config.mode_doc = {"mode": mode} if mode else None


def _run(db, old, new):
    return asyncio.run(cg.check_commit(
        db, files={"a.py": new}, owner="o", repo="r", branch="b", head_sha="h", token="t",
        headers={}, client=_Client(old), api="https://api"))


def test_default_mode_is_shadow_logs_but_never_blocks():
    db = _DB()
    v = _run(db, PY_OLD, "import os\n")
    assert v and db.guardrail_events.rows[0]["event"] == "CG_WARN"


def test_block_mode_raises_plain_error():
    with pytest.raises(cg.CommitGuardBlockedError) as ei:
        _run(_DB("block"), PY_OLD, "import os\n")
    assert "Nothing was committed" in str(ei.value)


def test_fails_open_when_old_content_unavailable_and_on_crash():
    assert _run(_DB("block"), None, "x = 1\n") == []

    class _Boom:
        async def get(self, *a, **k):
            raise RuntimeError("x")
    out = asyncio.run(cg.check_commit(_DB("block"), files={"a.py": "x=1"}, owner="o", repo="r", branch="b",
                                      head_sha="h", token="t", headers={}, client=_Boom(), api="u"))
    assert out == []
