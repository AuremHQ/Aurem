"""
tests/test_2026_02_patch_repo_file_b4.py — Part B4 regression test.

`write_repo_file` only ever accepted a FULL file body — there was no
tool contract for a targeted, minimal-diff edit at all. This is the
class of gap behind the real App.jsx incident (a "change the footer
text" edit silently dropped 4 unrelated imports because the model was
regenerating the entire file). `patch_repo_file` closes it.
"""
import pytest

from services.local_tools import LOCAL_TOOLS, TOOL_SPECS, patch_repo_file


def test_patch_repo_file_is_registered_as_a_real_tool():
    assert "patch_repo_file" in LOCAL_TOOLS
    assert LOCAL_TOOLS["patch_repo_file"] is patch_repo_file


def test_patch_repo_file_has_a_catalog_entry_that_tells_the_model_to_prefer_it():
    spec = next(s for s in TOOL_SPECS if s["name"] == "patch_repo_file")
    assert "old_value" in spec["args_spec"]
    assert "new_value" in spec["args_spec"]
    write_spec = next(s for s in TOOL_SPECS if s["name"] == "write_repo_file")
    assert "patch_repo_file" in write_spec["description"]


@pytest.mark.asyncio
async def test_patch_repo_file_rejects_when_old_value_not_found(monkeypatch):
    async def fake_read(ctx, args):
        return {"ok": True, "content": "def foo():\n    return 1\n"}
    import services.local_tools as lt
    monkeypatch.setattr(lt, "read_repo_file", fake_read)

    res = await patch_repo_file({}, {
        "path": "x.py", "old_value": "return 2", "new_value": "return 3",
    })
    assert res["ok"] is False
    assert res["gate"] == "patch_not_found"


@pytest.mark.asyncio
async def test_patch_repo_file_rejects_when_old_value_is_ambiguous(monkeypatch):
    async def fake_read(ctx, args):
        return {"ok": True, "content": "x = 1\nx = 1\n"}
    import services.local_tools as lt
    monkeypatch.setattr(lt, "read_repo_file", fake_read)

    res = await patch_repo_file({}, {
        "path": "x.py", "old_value": "x = 1", "new_value": "x = 2",
    })
    assert res["ok"] is False
    assert res["gate"] == "patch_not_unique"


@pytest.mark.asyncio
async def test_patch_repo_file_only_changes_the_matched_spot_delegates_to_write(monkeypatch):
    """The exact shape of the App.jsx incident: a large file, one
    targeted change requested, everything else must survive verbatim
    — proven here by asserting the EXACT full body handed to
    write_repo_file (not just that the change 'looks right')."""
    original = (
        "import Foo from 'foo';\nimport Bar from 'bar';\n\n"
        "function App() {\n  return <Footer text=\"old\" />;\n}\n"
    )
    captured = {}

    async def fake_read(ctx, args):
        return {"ok": True, "content": original}

    async def fake_write(ctx, args):
        captured["content"] = args["content"]
        return {"ok": True, "sha": "abc123", "path": args["path"]}

    import services.local_tools as lt
    monkeypatch.setattr(lt, "read_repo_file", fake_read)
    monkeypatch.setattr(lt, "write_repo_file", fake_write)

    res = await patch_repo_file({}, {
        "path": "App.jsx",
        "old_value": 'text="old"', "new_value": 'text="new"',
    })
    assert res["ok"] is True
    assert captured["content"] == (
        "import Foo from 'foo';\nimport Bar from 'bar';\n\n"
        "function App() {\n  return <Footer text=\"new\" />;\n}\n"
    )
    # both imports survived — the exact class of bug this tool exists to prevent
    assert "import Foo from 'foo';" in captured["content"]
    assert "import Bar from 'bar';" in captured["content"]


@pytest.mark.asyncio
async def test_patch_repo_file_rejects_empty_old_value():
    res = await patch_repo_file({}, {"path": "x.py", "old_value": "", "new_value": "y"})
    assert res["ok"] is False
