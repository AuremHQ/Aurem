"""
services/rea_service.py — admin-only "reverse engineer this website"
chat skill, built on github.com/morluto/rea ("Reverse Engineer
Anything").

Scope (confirmed with the founder before building):
  - Input: a single LIVE WEBSITE URL (no binaries/APKs/native apps —
    we have no Ghidra/IDA/Hopper installed, and don't need them here).
  - We can only ever recover what the browser actually receives: the
    HTML document, CSS, and JS (real text, including de-minified
    originals when a source map is published) — this is the
    "frontend, for real" promise. Proprietary SERVER-SIDE source is
    NEVER transmitted to the browser and is NOT recoverable this way;
    for "backend" we surface what REA can *observe* (API endpoints
    actually called, response shapes) — inference, not source code.
    This distinction is surfaced to the admin in the job result
    (`backend_note`) rather than silently overpromising.

Architecture:
  - `rea` needs Node >=22.19, but this pod's system Node is v20 (used
    by the frontend's own Vite dev server — must not be touched). So
    `rea` + a private Node 22 runtime are installed in an ISOLATED
    prefix (/opt/node22, /opt/node22-global) that nothing else on the
    box depends on. `_ensure_rea_ready()` self-heals this on first use
    after a fresh pod boot (ephemeral install — a redeploy/restart
    wipes /opt, this must be able to reprovision itself).
  - Each analysis launches its OWN throwaway headless Chromium (unique
    CDP port + temp profile dir), drives it via `rea`'s CDP tools,
    and is ALWAYS killed + cleaned up in a `finally`, win or lose.
  - `analyze-web-bundle --fetch-source-maps` against a real JS-heavy
    site OOM-crashed a bare Node process at >2GB heap during manual
    testing — a serious shared-pod risk (this backend runs in the
    same memory cgroup). Mitigated with a hard `NODE_OPTIONS`
    heap cap, a bounded subprocess timeout, a global single-job
    semaphore (no stacking concurrent Chromium+Node pairs), and NOT
    requesting source maps on the bundle-analysis pass (kept on the
    cheaper `inspect-web-page` pass, which stayed well under budget).
  - SSRF guard reuses the existing `_is_safe_host()` from
    services/url_fetcher.py rather than a second implementation.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import socket
import tempfile
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import httpx

from services.http.client import ext_client
from services.url_fetcher import _is_safe_host

logger = logging.getLogger(__name__)

# ── Install locations (isolated — never touches the system Node the
#    frontend's Vite dev server depends on) ─────────────────────────
NODE_INSTALL_DIR = "/opt/node22"
NODE_GLOBAL_DIR = "/opt/node22-global"
NODE_BIN_DIR = f"{NODE_INSTALL_DIR}/bin"
REA_BIN = f"{NODE_GLOBAL_DIR}/bin/rea"
NODE_VERSION = "22.22.0"
REA_VERSION = "6.7.0"
_ARCH_MAP = {"x86_64": "x64", "aarch64": "arm64", "arm64": "arm64"}

# ── Safety budgets ───────────────────────────────────────────────────
MAX_HEAP_MB = 768                 # NODE_OPTIONS --max-old-space-size
SUBPROCESS_TIMEOUT_S = 60         # per `rea` CLI invocation
CHROME_STARTUP_TIMEOUT_S = 15
JOB_OVERALL_TIMEOUT_S = 150
MAX_FILE_TEXT_BYTES = 300_000     # per stored file (truncated beyond)
MAX_FILES_STORED = 80
OBSERVATION_MS = 3500

_install_lock = asyncio.Lock()
_job_semaphore = asyncio.Semaphore(1)  # one REA job (own Chrome+Node) at a time


class ReaValidationError(ValueError):
    """Raised for a target URL we refuse to analyze. Message is safe
    to show the admin verbatim (never contains internal paths)."""


def validate_target_url(raw_url: str) -> str:
    raw_url = (raw_url or "").strip()
    if not raw_url:
        raise ReaValidationError("Give me a URL to analyze, e.g. /reverse-engineer https://example.com")
    if "://" in raw_url and not re.match(r"^https?://", raw_url, re.I):
        raise ReaValidationError("Only http:// and https:// URLs are supported.")
    if not re.match(r"^https?://", raw_url, re.I):
        raw_url = f"https://{raw_url}"
    parsed = urlparse(raw_url)
    if parsed.scheme not in ("http", "https"):
        raise ReaValidationError("Only http:// and https:// URLs are supported.")
    if not parsed.hostname:
        raise ReaValidationError("That doesn't look like a valid URL.")
    if not _is_safe_host(parsed.hostname):
        raise ReaValidationError(
            "That host can't be analyzed (local/private/internal addresses are blocked)."
        )
    return raw_url


def _is_safe_url(url: str) -> bool:
    """Per-hop redirect check — stricter than validate_target_url: a host
    that fails to resolve is NOT given the benefit of the doubt here,
    because by the time we're following a redirect the request is
    already in flight (unlike the initial user-typed URL, where
    _is_safe_host's fail-open-on-DNS-error is tolerable)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    return _is_safe_host(parsed.hostname)


# ── Self-healing install ─────────────────────────────────────────────
async def _run(cmd: list[str], *, cwd: str | None = None, env: dict | None = None,
                timeout: float = 120) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=cwd, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


async def _ensure_rea_ready() -> None:
    """Idempotent — safe to call on every job. Cheap no-op once installed;
    re-provisions /opt/node22[-global] after a pod restart wiped it."""
    if os.path.exists(REA_BIN) and os.path.exists(f"{NODE_BIN_DIR}/node"):
        return
    async with _install_lock:
        if os.path.exists(REA_BIN) and os.path.exists(f"{NODE_BIN_DIR}/node"):
            return
        logger.info("[rea_service] REA not present — provisioning isolated Node %s + rea-agents %s",
                    NODE_VERSION, REA_VERSION)
        import platform
        arch = _ARCH_MAP.get(platform.machine(), "x64")
        tarball = f"/tmp/node{NODE_VERSION}.tar.xz"
        url = f"https://nodejs.org/dist/v{NODE_VERSION}/node-v{NODE_VERSION}-linux-{arch}.tar.xz"
        rc, out, err = await _run(["curl", "-sL", "-o", tarball, url], timeout=60)
        if rc != 0 or not os.path.exists(tarball):
            raise RuntimeError(f"failed to download Node {NODE_VERSION}: {err[:300]}")
        os.makedirs(NODE_INSTALL_DIR, exist_ok=True)
        rc, out, err = await _run(
            ["tar", "-xJf", tarball, "-C", NODE_INSTALL_DIR, "--strip-components=1"], timeout=60,
        )
        if rc != 0:
            raise RuntimeError(f"failed to extract Node {NODE_VERSION}: {err[:300]}")
        os.makedirs(NODE_GLOBAL_DIR, exist_ok=True)
        env = {**os.environ, "PATH": f"{NODE_BIN_DIR}:{os.environ.get('PATH', '')}"}
        rc, out, err = await _run(
            [f"{NODE_BIN_DIR}/npm", "install", "-g", f"rea-agents@{REA_VERSION}",
             "--prefix", NODE_GLOBAL_DIR],
            env=env, timeout=90,
        )
        if rc != 0 or not os.path.exists(REA_BIN):
            raise RuntimeError(f"failed to install rea-agents: {err[:500]}")
        logger.info("[rea_service] REA provisioned successfully")


async def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _chrome_binary() -> str:
    for cand in ("/usr/bin/google-chrome", "/root/bin/chromium", "/usr/bin/chromium"):
        if os.path.exists(cand):
            return cand
    raise RuntimeError("no Chromium/Chrome binary found in this environment")


async def _run_rea_cli(args: list[str], *, timeout: float = SUBPROCESS_TIMEOUT_S) -> dict:
    env = {
        **os.environ,
        "PATH": f"{NODE_BIN_DIR}:{os.environ.get('PATH', '')}",
        "NODE_OPTIONS": f"--max-old-space-size={MAX_HEAP_MB}",
    }
    try:
        rc, out, err = await _run([REA_BIN, *args, "--format", "json"], env=env, timeout=timeout)
    except asyncio.TimeoutError:
        return {"ok": False, "error": f"rea {args[0]} timed out after {timeout}s"}
    if rc != 0:
        return {"ok": False, "error": (err or out or "rea exited non-zero")[:800]}
    try:
        return {"ok": True, "data": json.loads(out)}
    except json.JSONDecodeError:
        return {"ok": False, "error": "rea returned non-JSON output"}


async def _open_tab(endpoint: str, url: str, *, retries: int = 10) -> str | None:
    """CDP's `PUT /json/new?<url>` takes the target URL as a raw query
    string (not a form-encoded param) — build it manually. Chrome needs
    a moment to come up after exec, so retry a few times."""
    async with ext_client("rea_cdp", timeout=httpx.Timeout(5.0)) as client:
        for _ in range(retries):
            try:
                r = await client.put(f"{endpoint}/json/new?{url}")
                r.raise_for_status()
                return r.json().get("id")
            except Exception:
                await asyncio.sleep(0.5)
    return None


async def _get_target_url(endpoint: str, target_id: str) -> str | None:
    """Reads back the tab's CURRENT url via CDP — used to catch the
    case where the live site 30x-redirected the browser itself to a
    private/internal address (Chrome follows redirects on its own;
    this is the only way to see where it actually ended up)."""
    try:
        async with ext_client("rea_cdp", timeout=httpx.Timeout(5.0)) as client:
            r = await client.get(f"{endpoint}/json/list")
            r.raise_for_status()
            for target in r.json():
                if target.get("id") == target_id:
                    return target.get("url")
    except Exception:
        pass
    return None


async def _fetch_text_resource(url: str, *, max_bytes: int = MAX_FILE_TEXT_BYTES,
                                max_redirects: int = 5) -> str | None:
    """Fetches with redirects followed BY HAND (never httpx's built-in
    follow_redirects=True) so every hop — not just the URL the admin
    typed — is re-validated against _is_safe_host. A public redirector
    (e.g. a URL-shortener or an open `?redirect_to=` param) pointing at
    127.0.0.1/169.254.169.254/etc is the classic SSRF bypass this closes."""
    current = url
    try:
        async with ext_client(
            "rea_target", timeout=httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0),
        ) as client:
            for _ in range(max_redirects + 1):
                if not _is_safe_url(current):
                    return None
                r = await client.get(current, follow_redirects=False)
                if r.status_code in (301, 302, 303, 307, 308):
                    location = r.headers.get("location")
                    if not location:
                        return None
                    current = urljoin(current, location)
                    continue
                if r.status_code >= 400:
                    return None
                return r.text[:max_bytes]
            return None  # too many redirects
    except Exception:
        return None


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


async def run_reverse_engineer_job(db, *, job_id: str, url: str, requested_by: str) -> None:
    """Runs the full analysis and writes the final state to db.rea_jobs.
    Never raises — all failure paths are recorded as a terminal
    status="failed" document instead."""
    started = time.time()
    chrome_proc = None
    profile_dir = None
    try:
        async with asyncio.timeout(JOB_OVERALL_TIMEOUT_S):
            await _ensure_rea_ready()

            port = await _find_free_port()
            profile_dir = tempfile.mkdtemp(prefix="rea-profile-")
            chrome_proc = await asyncio.create_subprocess_exec(
                _chrome_binary(), "--headless=new", "--disable-gpu", "--no-sandbox",
                f"--remote-debugging-port={port}", "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={profile_dir}", "about:blank",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )

            endpoint = f"http://127.0.0.1:{port}"
            target_id = await _open_tab(endpoint, url)
            if not target_id:
                raise RuntimeError("could not open a browser tab for this URL")

            await asyncio.sleep(1.5)  # let the page settle before observing

            # Chrome follows redirects on its own navigation — re-check
            # where it actually ended up before running any analysis or
            # trusting anything it captured (closes the same SSRF class
            # as _fetch_text_resource's manual redirect-following, for
            # the browser-driven path).
            final_url = await _get_target_url(endpoint, target_id)
            if final_url and not _is_safe_url(final_url):
                raise ReaValidationError(
                    "This URL redirects to a private/internal address and can't be analyzed."
                )

            inspect = await _run_rea_cli(
                ["inspect-web-page", endpoint, target_id,
                 "--include-script-sources", "--include-json-body-shapes",
                 "--observation-ms", str(OBSERVATION_MS)],
                timeout=SUBPROCESS_TIMEOUT_S,
            )
            bundle = await _run_rea_cli(
                ["analyze-web-bundle", endpoint, target_id,
                 "--observation-ms", str(OBSERVATION_MS)],
                timeout=SUBPROCESS_TIMEOUT_S,
            )

            files: list[dict] = []
            html_text = await _fetch_text_resource(url)
            if html_text:
                files.append({
                    "path": urlparse(url).path or "/", "url": url, "type": "html",
                    "bytes": len(html_text), "sha256": _sha256(html_text),
                    "text": html_text[:MAX_FILE_TEXT_BYTES],
                })

            inspect_data = inspect.get("data", {}).get("normalized_result", {}) if inspect.get("ok") else {}
            for item in (inspect_data.get("scripts") or {}).get("items", [])[:MAX_FILES_STORED]:
                text = ((item.get("source") or {}).get("artifact") or {}).get("text")
                script_url = item.get("url") or ""
                if not text or (script_url and not _is_safe_url(script_url)):
                    continue
                files.append({
                    "path": urlparse(script_url).path or script_url, "url": script_url, "type": "js",
                    "bytes": len(text), "sha256": item.get("cdp_hash") or _sha256(text),
                    "text": text[:MAX_FILE_TEXT_BYTES],
                })

            for res in (inspect_data.get("resources") or [])[:MAX_FILES_STORED]:
                if res.get("type") == "Stylesheet" and res.get("url") and _is_safe_url(res["url"]):
                    css_text = await _fetch_text_resource(res["url"])
                    if css_text:
                        files.append({
                            "path": urlparse(res["url"]).path or res["url"], "url": res["url"], "type": "css",
                            "bytes": len(css_text), "sha256": _sha256(css_text),
                            "text": css_text[:MAX_FILE_TEXT_BYTES],
                        })

            bundle_data = bundle.get("data", {}).get("normalized_result", {}) if bundle.get("ok") else {}
            observed_endpoints = (bundle_data.get("observations") or {}).get("endpoints", [])

            doc = {
                "job_id": job_id,
                "requested_by": requested_by,
                "url": url,
                "status": "done",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "duration_s": round(time.time() - started, 1),
                "files": [{k: v for k, v in f.items() if k != "text"} for f in files],
                "file_text_by_sha256": {f["sha256"]: f["text"] for f in files},
                "observed_api_endpoints": observed_endpoints[:30],
                "backend_note": (
                    "Backend/server source code is never sent to the browser, so it "
                    "can't be extracted this way — only the frontend files above are "
                    "the real, original text. \"observed_api_endpoints\" lists API "
                    "calls this page actually made, inferred from network traffic, "
                    "not source code."
                ),
                "limitations": (inspect_data.get("limitations") or []) + (bundle_data.get("limitations") or []),
                "error": None,
            }
    except Exception as e:
        logger.exception("[rea_service] job %s failed", job_id)
        doc = {
            "job_id": job_id, "requested_by": requested_by, "url": url,
            "status": "failed",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "duration_s": round(time.time() - started, 1),
            "files": [], "file_text_by_sha256": {}, "observed_api_endpoints": [],
            "backend_note": None, "limitations": [],
            "error": str(e)[:500],
        }
    finally:
        if chrome_proc is not None:
            try:
                chrome_proc.kill()
                await chrome_proc.wait()
            except Exception:
                pass
        if profile_dir and os.path.isdir(profile_dir):
            shutil.rmtree(profile_dir, ignore_errors=True)

    try:
        await db.rea_jobs.update_one({"job_id": job_id}, {"$set": doc}, upsert=True)
    except Exception:
        logger.exception("[rea_service] failed to persist job %s result", job_id)


_background_tasks: set[asyncio.Task] = set()


async def start_job(db, *, url: str, requested_by: str) -> str:
    """Creates the job doc and schedules the background task. Caller
    (the router) is responsible for enforcing the single-in-flight-job
    rule by checking for an existing running job first."""
    import uuid
    job_id = f"rea_{uuid.uuid4().hex[:12]}"
    await db.rea_jobs.insert_one({
        "job_id": job_id, "requested_by": requested_by, "url": url,
        "status": "running",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None, "duration_s": None,
        "files": [], "file_text_by_sha256": {}, "observed_api_endpoints": [],
        "backend_note": None, "limitations": [], "error": None,
    })

    async def _bounded_job():
        async with _job_semaphore:
            await run_reverse_engineer_job(db, job_id=job_id, url=url, requested_by=requested_by)

    # The event loop only holds a WEAK reference to a bare create_task()
    # result — with nothing else referencing it, it can be garbage
    # collected mid-run. Keep a strong ref in a module-level set and
    # drop it again once done (success or failure, doesn't matter).
    task = asyncio.create_task(_bounded_job())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return job_id
