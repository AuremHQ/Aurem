"""
Part XIII · 3(a) — commit_files 409 rebase-retry.

A 409 on the ref-advance PATCH means the branch head moved under us
between the step-0 head read and the PATCH (concurrent push). The fix
re-reads the live head, rebuilds tree+commit on the FRESH head (blobs
are already uploaded — content-addressed, no re-upload), then PATCHes
the fresh commit once. A second 409 still raises PushFailedError
carrying the LATEST orphaned commit SHA.

Real commit_files() code, mocked GitHub transport (respx) — the same
standard as test_iter_ship_commit_robustness.py T3/T4a.
"""
import json

import httpx
import pytest

from core.errors import PushFailedError

OWNER, REPO, BRANCH = "acme", "widgets", "main"
BASE = f"https://api.github.com/repos/{OWNER}/{REPO}"
OLD_HEAD = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
NEW_HEAD = "b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3"
COMMIT_1 = "cafebabe00112233445566778899aabbccddeeff"
COMMIT_2 = "deadbeef1234567890abcdef0000111122223333"


def _mock_github(respx_mock, *, patch_side_effect, head_side_effect=None,
                 commits_side_effect=None, trees_side_effect=None):
    """Mock the GitHub Data API the way T3 does, with per-call
    side_effect lists where the retry path needs distinct answers."""
    respx_mock.get(f"{BASE}/git/ref/heads/{BRANCH}").mock(
        side_effect=head_side_effect or [
            httpx.Response(200, json={"object": {"sha": OLD_HEAD}}),
        ])
    respx_mock.get(f"{BASE}/git/commits/{OLD_HEAD}").mock(
        return_value=httpx.Response(200, json={"tree": {"sha": "treeSHA0"}}))
    respx_mock.get(f"{BASE}/git/commits/{NEW_HEAD}").mock(
        return_value=httpx.Response(200, json={"tree": {"sha": "treeSHA1"}}))
    respx_mock.get(f"{BASE}/git/commits/{COMMIT_1}").mock(
        return_value=httpx.Response(200, json={"tree": {"sha": "treeSHA1"}}))
    respx_mock.post(f"{BASE}/git/blobs").mock(
        return_value=httpx.Response(201, json={"sha": "blobSHA0"}))
    respx_mock.post(f"{BASE}/git/trees").mock(
        side_effect=trees_side_effect or [
            httpx.Response(201, json={"sha": "newTreeSHA0"}),
        ])
    respx_mock.post(f"{BASE}/git/commits").mock(
        side_effect=commits_side_effect or [
            httpx.Response(201, json={"sha": COMMIT_1}),
        ])
    respx_mock.patch(f"{BASE}/git/refs/heads/{BRANCH}").mock(
        side_effect=patch_side_effect)


def _body(call):
    return json.loads(call.request.content)


def _calls(respx_mock, method, path_fragment):
    return [
        c for c in respx_mock.calls
        if c.request.method == method
        and path_fragment in str(c.request.url)
    ]


async def _commit():
    from services.github_api_writer import commit_files
    return await commit_files(
        owner=OWNER, repo=REPO, branch=BRANCH, token="fake-tok",
        files={"proof.txt": "Part XIII 3(a) proof\n"},
        commit_message="Part XIII 3(a): 409 retry proof",
        author_email="aurem@example.com", author_name="AUREM",
    )


async def test_409_triggers_one_rebase_retry_and_succeeds(respx_mock):
    """First PATCH 409 → head re-read (new head) → rebuild tree/commit
    on the fresh head → second PATCH 200 → returns the REBUILT sha.
    The rebuilt commit's parent is the fresh head, not the stale one."""
    _mock_github(
        respx_mock,
        head_side_effect=[
            httpx.Response(200, json={"object": {"sha": OLD_HEAD}}),
            httpx.Response(200, json={"object": {"sha": NEW_HEAD}}),
        ],
        commits_side_effect=[
            httpx.Response(201, json={"sha": COMMIT_1}),
            httpx.Response(201, json={"sha": COMMIT_2}),
        ],
        trees_side_effect=[
            httpx.Response(201, json={"sha": "newTreeSHA0"}),
            httpx.Response(201, json={"sha": "newTreeSHA1"}),
        ],
        patch_side_effect=[
            httpx.Response(409, json={"message": "Update is not a fast forward"}),
            httpx.Response(200, json={"ref": f"refs/heads/{BRANCH}"}),
        ],
    )
    result = await _commit()
    assert result["ok"] is True
    assert result["full_sha"] == COMMIT_2

    patch_calls = _calls(respx_mock, "PATCH", "git/refs/heads/main")
    assert len(patch_calls) == 2
    # Second PATCH points the ref at the REBUILT commit.
    assert _body(patch_calls[1])["sha"] == COMMIT_2

    tree_calls = _calls(respx_mock, "POST", "git/trees")
    assert len(tree_calls) == 2
    assert _body(tree_calls[0])["base_tree"] == "treeSHA0"
    # Rebuilt tree is based on the FRESH head's tree.
    assert _body(tree_calls[1])["base_tree"] == "treeSHA1"

    commit_calls = _calls(respx_mock, "POST", "git/commits")
    assert len(commit_calls) == 2
    assert _body(commit_calls[0])["parents"] == [OLD_HEAD]
    # Rebuilt commit's parent is the fresh head — the ref only ever
    # advances to a descendant of the live head.
    assert _body(commit_calls[1])["parents"] == [NEW_HEAD]
    # Blobs were NOT re-uploaded (content-addressed reuse).
    assert len(_calls(respx_mock, "POST", "git/blobs")) == 1


async def test_second_409_raises_with_latest_commit_sha(respx_mock):
    """409, then 409 again → PushFailedError carrying the REBUILT
    (latest) commit SHA, not the first orphaned one."""
    _mock_github(
        respx_mock,
        head_side_effect=[
            httpx.Response(200, json={"object": {"sha": OLD_HEAD}}),
            httpx.Response(200, json={"object": {"sha": NEW_HEAD}}),
        ],
        commits_side_effect=[
            httpx.Response(201, json={"sha": COMMIT_1}),
            httpx.Response(201, json={"sha": COMMIT_2}),
        ],
        trees_side_effect=[
            httpx.Response(201, json={"sha": "newTreeSHA0"}),
            httpx.Response(201, json={"sha": "newTreeSHA1"}),
        ],
        patch_side_effect=[
            httpx.Response(409, json={"message": "Update is not a fast forward"}),
            httpx.Response(409, json={"message": "Update is not a fast forward"}),
        ],
    )
    with pytest.raises(PushFailedError) as exc_info:
        await _commit()
    assert exc_info.value.commit_sha == COMMIT_2
    assert "409" in exc_info.value.reason
    assert len(_calls(respx_mock, "PATCH", "git/refs/heads/main")) == 2


async def test_422_still_raises_immediately_with_original_sha(respx_mock):
    """Non-409 failures are NOT retried: a 422 raises at once with
    the original SHA, exactly one PATCH, no head re-read."""
    _mock_github(
        respx_mock,
        patch_side_effect=[
            httpx.Response(422, json={"message": "Update is not a fast forward"}),
        ],
    )
    with pytest.raises(PushFailedError) as exc_info:
        await _commit()
    assert exc_info.value.commit_sha == COMMIT_1
    assert "422" in exc_info.value.reason
    assert len(_calls(respx_mock, "PATCH", "git/refs/heads/main")) == 1
    # No re-read: the head was fetched exactly once (step 0).
    assert len(_calls(respx_mock, "GET", "git/ref/heads/main")) == 1
    assert len(_calls(respx_mock, "POST", "git/trees")) == 1
    assert len(_calls(respx_mock, "POST", "git/commits")) == 1


async def test_409_but_head_already_our_commit_is_success(respx_mock):
    """The first PATCH actually landed (stale/lost read) — the re-read
    head IS our commit, so the retry is a no-op success with the
    original SHA and no rebuild."""
    _mock_github(
        respx_mock,
        head_side_effect=[
            httpx.Response(200, json={"object": {"sha": OLD_HEAD}}),
            httpx.Response(200, json={"object": {"sha": COMMIT_1}}),
        ],
        patch_side_effect=[
            httpx.Response(409, json={"message": "Update is not a fast forward"}),
        ],
    )
    result = await _commit()
    assert result["ok"] is True
    assert result["full_sha"] == COMMIT_1
    assert len(_calls(respx_mock, "PATCH", "git/refs/heads/main")) == 1
    assert len(_calls(respx_mock, "POST", "git/trees")) == 1
    assert len(_calls(respx_mock, "POST", "git/commits")) == 1


async def test_409_with_head_reread_failure_raises_original_sha(respx_mock):
    """If the head re-read itself fails, the error still carries the
    original orphaned SHA and says the re-read failed."""
    _mock_github(
        respx_mock,
        head_side_effect=[
            httpx.Response(200, json={"object": {"sha": OLD_HEAD}}),
            httpx.Response(404, json={"message": "Not Found"}),
        ],
        patch_side_effect=[
            httpx.Response(409, json={"message": "Update is not a fast forward"}),
        ],
    )
    with pytest.raises(PushFailedError) as exc_info:
        await _commit()
    assert exc_info.value.commit_sha == COMMIT_1
    assert "409" in exc_info.value.reason
    assert "head re-read failed" in exc_info.value.reason
    assert len(_calls(respx_mock, "PATCH", "git/refs/heads/main")) == 1
