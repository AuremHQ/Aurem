"""tests/test_regression_iter2026_66_slop_squat_check.py

Security audit Phase 3, rule 6: slop squatting (an LLM-invented package name
in package.json / requirements.txt that does not exist on npm / PyPI, so an
attacker can register it).

Safety properties under test:
  * ONLY an HTTP 404 means "does not exist"; 429, 5xx, redirects, timeouts and
    network errors produce no finding;
  * package names are validated against the npm / PyPI name grammar before they
    reach a URL, and the registry hosts are fixed (no SSRF);
  * bounded: <= 50 packages per scan, concurrency 5, 3 s timeout, 24 h cache;
  * private-looking / local specifiers are never looked up.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from services import slop_squat_check as ssc

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _fresh_cache():
    ssc.clear_cache()
    yield
    ssc.clear_cache()


def _client(handler):
    return lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _pkg_json(**sections) -> str:
    return json.dumps(sections, indent=2)


# ── extraction ────────────────────────────────────────────────────────
def test_extract_from_package_json_all_dependency_sections():
    text = _pkg_json(
        name="my-app",
        dependencies={"react": "^18.0.0"},
        devDependencies={"vitest": "^3.0.0"},
        peerDependencies={"left-pad": "*"},
        optionalDependencies={"fsevents": "^2"},
    )
    got = {(r, n) for r, n, _ in ssc.extract_packages("package.json", text)}
    assert got == {("npm", "react"), ("npm", "vitest"), ("npm", "left-pad"), ("npm", "fsevents")}


@pytest.mark.parametrize("spec", [
    "file:../local", "link:../x", "workspace:*", "git+https://github.com/a/b.git",
    "https://example.com/x.tgz", "http://example.com/x.tgz", "github:user/repo", "user/repo",
    "npm:other-package@1", "git://github.com/a/b", "portal:../x",
])
def test_extract_skips_non_registry_specifiers(spec):
    text = _pkg_json(dependencies={"weird": spec})
    assert ssc.extract_packages("package.json", text) == []


def test_extract_skips_scoped_packages_and_own_workspace_names():
    text = _pkg_json(
        name="acme-root", workspaces=["packages/*", "apps/web"],
        dependencies={"@acme/internal": "1.0.0", "acme-root": "1.0.0", "real-pkg": "1.0.0"},
    )
    assert [n for _, n, _ in ssc.extract_packages("package.json", text)] == ["real-pkg"]


def test_extract_package_json_line_numbers_point_at_the_dependency():
    text = '{\n  "dependencies": {\n    "alpha": "1",\n    "beta": "2"\n  }\n}\n'
    lines = {n: line for _, n, line in ssc.extract_packages("package.json", text)}
    assert lines == {"alpha": 3, "beta": 4}


def test_extract_package_json_handles_garbage_and_wrong_types():
    for text in ("", "not json", "[]", "null", '{"dependencies": []}', '{"dependencies": {"a": 1, "b": null}}'):
        assert isinstance(ssc.extract_packages("package.json", text), list)


def test_extract_from_requirements_txt():
    text = (
        "# comment\nrequests==2.31.0\nFlask_Cors>=4.0,<5\nuvicorn[standard]==0.30  # inline comment\n"
        "numpy ; python_version >= '3.9'\n-r other.txt\n-e .\n-e git+https://github.com/a/b.git#egg=b\n"
        "git+https://github.com/a/b.git\n./local/pkg\n../sibling\n/abs/path\nhttps://example.com/pkg.whl\n"
        "pkg @ https://example.com/pkg.whl\n--index-url https://example.com/simple\nboto3==1.34 \\\n"
        "    --hash=sha256:abc\n\n"
    )
    got = {(r, n) for r, n, _ in ssc.extract_packages("backend/requirements.txt", text)}
    assert got == {("pypi", "requests"), ("pypi", "flask-cors"), ("pypi", "uvicorn"),
                   ("pypi", "numpy"), ("pypi", "boto3")}


@pytest.mark.parametrize("name", [
    "../../etc/passwd", "a/b", "a b", "evil.com/x", "x" * 300, "café", "a\tb", "", "-leading", "a:b",
    "a?x=1", "a#frag", "a%2fb", "a@b", "a\\b",
])
def test_invalid_names_are_never_extracted(name):
    assert ssc.extract_packages("package.json", _pkg_json(dependencies={name: "1.0.0"})) == []
    assert ssc.extract_packages("requirements.txt", f"{name}==1.0\n") == []


@pytest.mark.parametrize("path", ["src/index.js", "README.md", "package-lock.json", "yarn.lock", "node_modules/x/package.json",
                                  "requirements-dev.py", ""])
def test_only_manifest_files_are_read(path):
    text = _pkg_json(dependencies={"x": "1"})
    assert ssc.extract_packages(path, text) == []


def test_requirements_variants_are_recognised():
    assert ssc.extract_packages("requirements-dev.txt", "pytest==8\n")
    assert ssc.extract_packages("backend/requirements.txt", "pytest==8\n")


# ── checking ──────────────────────────────────────────────────────────
async def test_404_is_a_finding_with_the_standard_shape():
    pkgs = [("npm", "totally-made-up-pkg", 7)]

    def handler(req):
        return httpx.Response(404, json={"error": "Not found"})

    found = await ssc.check_packages(pkgs, file="package.json", client_factory=_client(handler))
    assert len(found) == 1
    f = found[0]
    assert f["rule_id"] == "slop_squat_unknown_package" and f["severity"] == "high"
    assert f["file"] == "package.json" and f["line"] == 7
    assert "totally-made-up-pkg" in f["desc"] and f["vuln"] == "supply_chain"


@pytest.mark.parametrize("status", [200, 201, 301, 302, 400, 401, 403, 408, 410, 418, 429, 500, 502, 503, 504])
async def test_only_404_counts_as_missing(status):
    def handler(req):
        return httpx.Response(status, json={})

    assert await ssc.check_packages([("npm", "some-pkg", 1)], file="package.json", client_factory=_client(handler)) == []


@pytest.mark.parametrize("exc", [httpx.ConnectTimeout("t"), httpx.ReadTimeout("t"), httpx.ConnectError("c"),
                                 httpx.RemoteProtocolError("p"), RuntimeError("boom")])
async def test_timeouts_and_network_errors_are_not_findings(exc):
    def handler(req):
        raise exc

    assert await ssc.check_packages([("pypi", "some-pkg", 1)], file="requirements.txt", client_factory=_client(handler)) == []


async def test_registry_hosts_are_fixed_and_names_are_quoted():
    seen = []

    def handler(req):
        seen.append(req.url)
        return httpx.Response(200, json={})

    await ssc.check_packages([("npm", "left-pad", 1), ("pypi", "Flask_Cors", 2)], file="x", client_factory=_client(handler))
    hosts = {u.host for u in seen}
    assert hosts <= {"registry.npmjs.org", "pypi.org"}
    assert any(str(u).endswith("/left-pad") for u in seen)
    assert any(str(u).endswith("/flask-cors/json") for u in seen)  # PyPI canonical name: no redirect needed


async def test_invalid_names_passed_straight_to_check_are_dropped_not_requested():
    seen = []

    def handler(req):
        seen.append(req.url)
        return httpx.Response(404)

    bad = [("npm", "../../x", 1), ("npm", "a/b", 2), ("pypi", "evil.com/x", 3), ("gem", "ok", 4), ("npm", "", 5)]
    assert await ssc.check_packages(bad, file="x", client_factory=_client(handler)) == []
    assert seen == []


async def test_at_most_50_packages_are_looked_up_per_scan():
    calls = []

    def handler(req):
        calls.append(str(req.url))
        return httpx.Response(404)

    pkgs = [("npm", f"pkg-{i}", i + 1) for i in range(80)]
    found = await ssc.check_packages(pkgs, file="package.json", client_factory=_client(handler))
    assert len(calls) == 50 and len(found) == 50


async def test_concurrency_never_exceeds_five():
    inflight = 0
    peak = 0

    async def handler(req):
        nonlocal inflight, peak
        inflight += 1
        peak = max(peak, inflight)
        await asyncio.sleep(0.01)
        inflight -= 1
        return httpx.Response(200, json={})

    pkgs = [("npm", f"pkg-{i}", 1) for i in range(30)]
    await ssc.check_packages(pkgs, file="x", client_factory=_client(handler))
    assert 1 <= peak <= 5


async def test_duplicates_are_looked_up_once():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, json={})

    await ssc.check_packages([("npm", "dup", 1), ("npm", "dup", 9), ("npm", "DUP", 3)], file="x", client_factory=_client(handler))
    assert len(calls) == 2  # npm names are case-sensitive in the grammar we accept: dup and DUP differ


async def test_answers_are_cached_for_24h_but_errors_are_not(monkeypatch):
    calls = []
    answers = {"ok-pkg": 200, "gone-pkg": 404, "flaky-pkg": 503}

    def handler(req):
        name = str(req.url).rsplit("/", 1)[-1]
        calls.append(name)
        return httpx.Response(answers[name], json={})

    pkgs = [("npm", "ok-pkg", 1), ("npm", "gone-pkg", 2), ("npm", "flaky-pkg", 3)]
    first = await ssc.check_packages(pkgs, file="x", client_factory=_client(handler))
    second = await ssc.check_packages(pkgs, file="x", client_factory=_client(handler))
    assert len(first) == len(second) == 1
    assert calls.count("ok-pkg") == 1 and calls.count("gone-pkg") == 1   # cached
    assert calls.count("flaky-pkg") == 2                                  # error: asked again

    monkeypatch.setattr(ssc.time, "time", lambda: 10**10)                 # > 24 h later
    await ssc.check_packages(pkgs, file="x", client_factory=_client(handler))
    assert calls.count("ok-pkg") == 2


async def test_per_request_timeout_is_three_seconds():
    assert ssc.REQUEST_TIMEOUT_SECONDS == 3.0
    assert ssc.MAX_PACKAGES == 50 and ssc.CONCURRENCY == 5 and ssc.CACHE_TTL_SECONDS == 24 * 3600


async def test_empty_input_makes_no_requests():
    def handler(req):
        raise AssertionError("no request expected")

    assert await ssc.check_packages([], file="x", client_factory=_client(handler)) == []


# ── manifests → findings (what the deep scan calls) ───────────────────
async def test_scan_manifests_finds_the_invented_package_in_the_right_file():
    cache = {
        "package.json": _pkg_json(dependencies={"react": "18", "react-ai-magic-hooks-pro": "1.0.0"}),
        "backend/requirements.txt": "requests==2\nfastapi-turbo-guard==0.1\n",
        "src/app.js": "const x = 1;",
    }

    def handler(req):
        missing = str(req.url).rsplit("/json", 1)[0].rsplit("/", 1)[-1] in {"react-ai-magic-hooks-pro", "fastapi-turbo-guard"}
        return httpx.Response(404 if missing else 200, json={})

    found = await ssc.scan_manifests(cache, client_factory=_client(handler))
    got = {(f["file"], f["rule_id"]) for f in found}
    assert got == {("package.json", "slop_squat_unknown_package"), ("backend/requirements.txt", "slop_squat_unknown_package")}


async def test_scan_manifests_never_raises_even_if_the_registry_is_down():
    def handler(req):
        raise httpx.ConnectError("down")

    cache = {"package.json": _pkg_json(dependencies={"a": "1"})}
    assert await ssc.scan_manifests(cache, client_factory=_client(handler)) == []
    assert await ssc.scan_manifests({}, client_factory=_client(handler)) == []
    assert await ssc.scan_manifests(None, client_factory=_client(handler)) == []
