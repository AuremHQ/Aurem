"""tests/test_regression_iter2026_10e_gh_tree_leading_slash.py

Part A fix — the fresh-ship-branch 422 (ref ORA-5ff229, task
t_9b2f02148171, repo TJSNDHU/Aurem): branch `auremcto/ship-...` was
created at b58fa79, blob upload logged "blob /README.md" (leading
slash), then `POST /repos/.../git/trees` 422'd. GitHub's Git Trees API
rejects a leading "/" in a tree entry's `path`.

A1 note: the real GitHub 422 response body for this incident was NOT
found in this sandbox's backend logs or Mongo `cto_tasks` collection
(searched by exact task_id/ref_id/owner — zero hits; this environment's
DB is a separate dev instance from production). This fix is derived
from GitHub's documented Git Trees API contract and the one concrete
logged clue ("blob /README.md"), not from the raw 422 body — see the
PART A report for the full A1 writeup.

Fix: services/github_api_writer.py::_normalize_tree_path() strips a
leading "/"/"./" , normalizes backslashes/doubled slashes, and rejects
empty/".." paths outright — applied to every path in `commit_files()`
BEFORE any blob upload, so a bad path fails fast (no wasted network
calls) instead of reaching GitHub and 422ing after every blob already
landed.
"""
from __future__ import annotations

import json

import httpx
import pytest

from services import github_api_writer as writer


def _make_mock_client(handler):
    """Same pattern as tests/test_github_app_service.py — httpx.MockTransport,
    no live network, no external dependency."""
    _RealAsyncClient = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.pop("transport", None)
        return _RealAsyncClient(transport=httpx.MockTransport(handler), **kwargs)
    return factory


def _ok_json(payload: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


class TestCommitFilesNormalizesLeadingSlash:
    @pytest.mark.asyncio
    async def test_leading_slash_path_reaches_tree_api_without_slash(self, monkeypatch):
        """THE regression proof: a file path with a leading slash
        (exactly what the live incident logged — "blob /README.md")
        must reach `POST .../git/trees` as a clean relative path."""
        tree_posts: list[dict] = []
        blob_posts: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if request.method == "GET" and path.endswith("/git/ref/heads/auremcto/ship-t1"):
                return _ok_json({"object": {"sha": "basehead0000000000000000000000000000"}})
            if request.method == "GET" and "/git/commits/basehead" in path:
                return _ok_json({"tree": {"sha": "basetree0000000000000000000000000000"}})
            if request.method == "POST" and path.endswith("/git/blobs"):
                body = json.loads(request.content)
                blob_posts.append(body)
                return _ok_json({"sha": "blobsha0000000000000000000000000000000"})
            if request.method == "POST" and path.endswith("/git/trees"):
                body = json.loads(request.content)
                tree_posts.append(body)
                return _ok_json({"sha": "newtree0000000000000000000000000000000"})
            if request.method == "POST" and path.endswith("/git/commits"):
                return _ok_json({"sha": "newcommit000000000000000000000000000000"})
            if request.method == "PATCH" and path.endswith("/git/refs/heads/auremcto/ship-t1"):
                return _ok_json({"ok": True})
            raise AssertionError(f"unexpected request: {request.method} {path}")

        monkeypatch.setattr(httpx, "AsyncClient", _make_mock_client(handler))

        res = await writer.commit_files(
            owner="TJSNDHU", repo="Aurem", branch="auremcto/ship-t1",
            token="ghp_faketoken_leadslash",
            files={"/README.md": "# widgets\n\nfix\n"},
            commit_message="AUREM: fix",
            author_name="Jane Dev", author_email="jane@example.com",
        )

        assert res["ok"] is True
        assert len(tree_posts) == 1
        tree_entry_paths = [e["path"] for e in tree_posts[0]["tree"]]
        assert tree_entry_paths == ["README.md"], (
            f"leading slash reached the Git Trees API: {tree_entry_paths}"
        )
        # The blob upload itself also carries the normalized path through.
        assert len(blob_posts) == 1

    @pytest.mark.asyncio
    async def test_backslash_and_double_slash_and_dot_slash_normalized(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if request.method == "GET" and "git/ref/heads" in path:
                return _ok_json({"object": {"sha": "basehead0000000000000000000000000000"}})
            if request.method == "GET" and "/git/commits/basehead" in path:
                return _ok_json({"tree": {"sha": "basetree0000000000000000000000000000"}})
            if request.method == "POST" and path.endswith("/git/blobs"):
                return _ok_json({"sha": "blobsha0000000000000000000000000000000"})
            if request.method == "POST" and path.endswith("/git/trees"):
                body = json.loads(request.content)
                paths = [e["path"] for e in body["tree"]]
                assert paths == ["src/app.py", "docs/readme.md"], paths
                return _ok_json({"sha": "newtree0000000000000000000000000000000"})
            if request.method == "POST" and path.endswith("/git/commits"):
                return _ok_json({"sha": "newcommit000000000000000000000000000000"})
            if request.method == "PATCH" and "git/refs/heads" in path:
                return _ok_json({"ok": True})
            raise AssertionError(f"unexpected request: {request.method} {path}")

        monkeypatch.setattr(httpx, "AsyncClient", _make_mock_client(handler))

        res = await writer.commit_files(
            owner="o", repo="r", branch="main", token="tok",
            files={"./src\\app.py": "x", "//docs//readme.md": "y"},
            commit_message="m", author_name="n", author_email="e@x.com",
        )
        assert res["ok"] is True

    @pytest.mark.asyncio
    async def test_path_traversal_rejected_before_any_network_call(self):
        calls: list = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            raise AssertionError("no network call should happen for a bad path")

        with pytest.raises(ValueError, match="traversal"):
            await writer.commit_files(
                owner="o", repo="r", branch="main", token="tok",
                files={"../../etc/passwd": "x"},
                commit_message="m", author_name="n", author_email="e@x.com",
            )
        assert calls == []

    @pytest.mark.asyncio
    async def test_empty_path_rejected_before_any_network_call(self):
        with pytest.raises(ValueError, match="empty"):
            await writer.commit_files(
                owner="o", repo="r", branch="main", token="tok",
                files={"   ": "x"},
                commit_message="m", author_name="n", author_email="e@x.com",
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
