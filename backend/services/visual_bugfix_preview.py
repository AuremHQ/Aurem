"""
services/visual_bugfix_preview.py — Phase 3 Isolated Preview Worker.

Zero Live Server Mutation (founder guardrail): every preview build
happens in its own throwaway directory under /tmp, on its own
OS-assigned ephemeral port, with its own headless Chromium process.
The shared dev server (supervisor-managed, port 3000) is NEVER
touched, restarted, or hot-patched.

Caching strategy (founder-approved 2026-09-12): a cold `yarn/npm
install` on an un-cached repo realistically takes well past 45s — no
package manager on earth reliably beats that from a cold cache. So:
  - 1st preview ever run against a given repo+branch+lockfile: runs
    the real install, may take longer, reports status="installing_deps"
    (UI shows "Building dependency cache — first run only").
  - Every later preview against the SAME repo+branch+lockfile-hash:
    node_modules is symlinked in from the cache — no reinstall, build
    + screenshot alone comfortably fit under 45s.
Cache lives under /app/.aurem_cache/preview_deps/{key}, LRU-evicted by
mtime when the cache directory would exceed _DEPS_CACHE_CAP_BYTES —
this pod's disk is small (~2.6GB free observed), so we can't let this
grow unbounded.

Fail-Safe Degradation (founder guardrail): ANY failure at ANY stage
(unsupported build tooling, install timeout, build error, headless
timeout) returns {ok: False, preview_error: "..."} — never raises,
never leaves the caller without at least the already-computed code
diff. Clean Teardown (founder guardrail): the ephemeral build dir,
the static-file-server subprocess, and the headless browser are ALL
torn down in a `finally` block — including on timeout.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import socket
import time
import uuid

logger = logging.getLogger(__name__)

_PREVIEW_ROOT = "/tmp/aurem_previews"
_DEPS_CACHE_ROOT = "/app/.aurem_cache/preview_deps"
_DEPS_CACHE_CAP_BYTES = 1_500_000_000          # ~1.5GB — leaves headroom on a small pod
_INSTALL_TIMEOUT_S = 90.0
_BUILD_TIMEOUT_S = 30.0
_SERVER_READY_TIMEOUT_S = 8.0
_SCREENSHOT_TIMEOUT_S = 15.0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _find_package_json_dir(root: str, hint_paths: list[str]) -> str | None:
    """Find the nearest ancestor of a patched file that contains a
    package.json (handles monorepos like this very codebase:
    /app/frontend + /app/backend). Falls back to the repo root, then
    a `frontend/` subdir if present (the single most common AUREM
    target-app layout)."""
    for hp in hint_paths:
        d = os.path.dirname(os.path.join(root, hp))
        while d and d.startswith(root):
            if os.path.isfile(os.path.join(d, "package.json")):
                return d
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
    if os.path.isfile(os.path.join(root, "package.json")):
        return root
    fe = os.path.join(root, "frontend")
    if os.path.isfile(os.path.join(fe, "package.json")):
        return fe
    return None


def _lockfile_hash(pkg_dir: str) -> str:
    h = hashlib.sha256()
    for name in ("yarn.lock", "package-lock.json", "package.json"):
        p = os.path.join(pkg_dir, name)
        if os.path.isfile(p):
            with open(p, "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()[:20]


def _evict_deps_cache_if_needed() -> None:
    """LRU-evict oldest cache dirs (by mtime) until under the cap.
    Best-effort — never raises."""
    try:
        if not os.path.isdir(_DEPS_CACHE_ROOT):
            return
        entries = []
        total = 0
        for name in os.listdir(_DEPS_CACHE_ROOT):
            p = os.path.join(_DEPS_CACHE_ROOT, name)
            size = sum(
                os.path.getsize(os.path.join(dp, f))
                for dp, _, fs in os.walk(p) for f in fs
                if os.path.exists(os.path.join(dp, f))
            )
            total += size
            entries.append((os.path.getmtime(p), size, p))
        entries.sort()  # oldest first
        i = 0
        while total > _DEPS_CACHE_CAP_BYTES and i < len(entries):
            _, size, p = entries[i]
            shutil.rmtree(p, ignore_errors=True)
            total -= size
            i += 1
    except Exception as e:                                    # noqa: BLE001
        logger.warning("[visual_bugfix_preview] cache eviction failed: %r", e)


async def _run_cmd(args: list[str], cwd: str, timeout: float) -> tuple[bool, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=cwd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return False, f"`{' '.join(args)}` timed out after {timeout:.0f}s"
        text = (out or b"").decode("utf-8", errors="replace")[-4000:]
        if proc.returncode != 0:
            return False, text or f"`{' '.join(args)}` exited {proc.returncode}"
        return True, text
    except FileNotFoundError as e:
        return False, f"command not found: {e}"
    except Exception as e:                                    # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


async def run_preview(*, owner: str, repo: str, branch: str, token: str,
                       patch_files: list[dict], report_id: str,
                       user_id: str, route: str = "/") -> dict:
    """Returns:
      {ok, preview_image_bytes, preview_image_content_type,
       stage, log, cold_install}
      or {ok: False, preview_error, stage, cold_install} on any
      degrade — NEVER raises."""
    from services.local_tools import _ensure_repo_snapshot

    work_dir = os.path.join(_PREVIEW_ROOT, f"{report_id}-{uuid.uuid4().hex[:6]}")
    cold_install = False

    try:
        snap_dir, snap_err = await _ensure_repo_snapshot(owner, repo, branch, token)
        if not snap_dir:
            return {"ok": False, "stage": "snapshot",
                    "preview_error": f"Could not fetch repo snapshot ({snap_err})",
                    "cold_install": False}

        # Copy (never symlink the whole tree — patch writes must land
        # on OUR copy, the shared snapshot cache stays clean for other
        # searches/previews running concurrently).
        os.makedirs(_PREVIEW_ROOT, exist_ok=True)
        await asyncio.to_thread(shutil.copytree, snap_dir, work_dir,
                                 ignore=shutil.ignore_patterns("node_modules", ".git"))

        for f in patch_files:
            full = os.path.join(work_dir, f["path"])
            if not full.startswith(work_dir):
                continue  # defence in depth against a stray ../ path
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as fh:
                fh.write(f["new_content"])

        pkg_dir = _find_package_json_dir(work_dir, [f["path"] for f in patch_files])
        if not pkg_dir:
            return {"ok": False, "stage": "detect_build_tool",
                    "preview_error": "No package.json found near the patched file(s) — "
                                      "unsupported build tooling for a live preview.",
                    "cold_install": False}

        # ── Dependency install, symlinked-cache-or-cold ──────────────
        lock_key = f"{owner}__{repo}__{os.path.basename(pkg_dir)}__{_lockfile_hash(pkg_dir)}"
        lock_key = "".join(c if c.isalnum() or c in "_-" else "_" for c in lock_key)
        cache_dir = os.path.join(_DEPS_CACHE_ROOT, lock_key)
        node_modules_path = os.path.join(pkg_dir, "node_modules")

        if os.path.isdir(cache_dir):
            os.symlink(cache_dir, node_modules_path)
        else:
            cold_install = True
            use_yarn = os.path.isfile(os.path.join(pkg_dir, "yarn.lock"))
            install_cmd = (["yarn", "install", "--frozen-lockfile"] if use_yarn
                            else ["npm", "ci"] if os.path.isfile(os.path.join(pkg_dir, "package-lock.json"))
                            else ["npm", "install"])
            ok, log = await _run_cmd(install_cmd, cwd=pkg_dir, timeout=_INSTALL_TIMEOUT_S)
            if not ok:
                return {"ok": False, "stage": "install", "preview_error": log[-800:],
                        "cold_install": True}
            os.makedirs(_DEPS_CACHE_ROOT, exist_ok=True)
            _evict_deps_cache_if_needed()
            try:
                await asyncio.to_thread(shutil.copytree, node_modules_path, cache_dir)
            except Exception as e:                            # noqa: BLE001
                logger.warning("[visual_bugfix_preview] failed to seed deps cache: %r", e)

        # ── Build (Vite/CRA-style static output assumed — matches
        # this codebase's own stack and the founder's spec, which
        # scopes this to "standard UI components") ───────────────────
        build_cmd = (["yarn", "build"] if os.path.isfile(os.path.join(pkg_dir, "yarn.lock"))
                     else ["npm", "run", "build"])
        ok, log = await _run_cmd(build_cmd, cwd=pkg_dir, timeout=_BUILD_TIMEOUT_S)
        if not ok:
            return {"ok": False, "stage": "build", "preview_error": log[-800:],
                    "cold_install": cold_install}

        dist_dir = next(
            (os.path.join(pkg_dir, d) for d in ("dist", "build")
             if os.path.isdir(os.path.join(pkg_dir, d))),
            None,
        )
        if not dist_dir:
            return {"ok": False, "stage": "build",
                    "preview_error": "Build succeeded but no dist/ or build/ output directory was found.",
                    "cold_install": cold_install}

        # ── Serve the static build on an ephemeral port ───────────────
        port = _free_port()
        server_proc = await asyncio.create_subprocess_exec(
            "python3", "-m", "http.server", str(port), "--directory", dist_dir,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            ready = False
            deadline = time.monotonic() + _SERVER_READY_TIMEOUT_S
            while time.monotonic() < deadline:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                        ready = True
                        break
                except OSError:
                    await asyncio.sleep(0.2)
            if not ready:
                return {"ok": False, "stage": "serve",
                        "preview_error": "Static preview server did not become ready in time.",
                        "cold_install": cold_install}

            # ── Headless capture ──────────────────────────────────────
            img_bytes = await asyncio.wait_for(
                _capture_screenshot(f"http://127.0.0.1:{port}{route}"),
                timeout=_SCREENSHOT_TIMEOUT_S,
            )
            return {"ok": True, "stage": "done",
                    "preview_image_bytes": img_bytes,
                    "preview_image_content_type": "image/png",
                    "cold_install": cold_install}
        finally:
            server_proc.kill()
            try:
                await asyncio.wait_for(server_proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass
    except asyncio.TimeoutError:
        return {"ok": False, "stage": "timeout",
                "preview_error": "Preview generation timed out.",
                "cold_install": cold_install}
    except Exception as e:                                    # noqa: BLE001
        logger.warning("[visual_bugfix_preview] unexpected failure: %r", e)
        return {"ok": False, "stage": "unexpected",
                "preview_error": f"{type(e).__name__}: {e}",
                "cold_install": cold_install}
    finally:
        await asyncio.to_thread(shutil.rmtree, work_dir, ignore_errors=True)


async def _capture_screenshot(url: str) -> bytes:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            page = await browser.new_page(viewport={"width": 1440, "height": 900})
            await page.goto(url, wait_until="domcontentloaded", timeout=10_000)
            await page.wait_for_timeout(1200)  # let a client-rendered SPA paint
            return await page.screenshot(type="png")
        finally:
            await browser.close()
