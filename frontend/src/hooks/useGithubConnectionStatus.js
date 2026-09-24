/**
 * useGithubConnectionStatus.js — 2026-09-12
 *
 * Extracted from the former RevokedRepoBanner.jsx. Same polling +
 * reconnect logic, but exposes it as data/actions instead of
 * rendering a banner. Per the founder's clarified spec: GitHub
 * connection state must NEVER surface as a banner (onboarding gate,
 * transient "retrying" banner, or this revoked-access banner) — the
 * ONLY UI signal is the color of the GitHub toggle in the chat
 * composer (green = connected, amber = transient issue / retrying,
 * red = disconnected / failed). This hook is the toggle's data
 * source; ChatPanel.jsx owns the actual color + click-to-reconnect
 * wiring.
 *
 * Polls the SAME `/connection-status` endpoint the sidebar uses
 * (backend caches it 8s, safe to double-poll), filtered to the
 * active project.
 */
import { useEffect, useRef, useState, useCallback } from "react";
import { api, getToken, API_BASE } from "../lib/api";

const REASON_LABEL = {
  github_rejected:        "token rejected — access revoked or expired",
  no_token:               "no GitHub credential on file",
  repo_not_found:         "repo not found — renamed, deleted, or access removed",
  installation_suspended: "GitHub App installation suspended — an org admin paused it",
  installation_deleted:   "GitHub App installation removed — reinstall to reconnect",
  // 2026-06 PAT-removal — App-only typed codes:
  app_installation_missing: "not connected via the GitHub App — reconnect to continue",
  app_installation_revoked: "GitHub App installation revoked — reinstall to reconnect",
};

// App-level states fixed on GitHub's OWN settings page (unsuspend) or
// via a fresh install — the popup reconnect flow can't fix these, so
// the caller routes these to the GitHub settings deep-link instead.
const APP_LEVEL_REASONS = new Set(["installation_suspended", "installation_deleted"]);

export function useGithubConnectionStatus(activeProject) {
  const [status, setStatus]     = useState(null); // null | "connected" | "disconnected" | "unreachable"
  const [reason, setReason]     = useState(null);
  const [installationId, setInstallationId] = useState(null);
  const [reconnecting, setReconnecting] = useState(false);
  const [popupBlocked, setPopupBlocked] = useState(false);
  const popupRef = useRef(null);

  const projectId = activeProject?.project_id;

  useEffect(() => {
    if (!projectId) { setStatus(null); return undefined; }
    let cancelled = false;
    const check = async () => {
      try {
        const token = getToken();
        const res = await fetch(`${API_BASE}/cto/projects/connection-status`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
          signal: AbortSignal.timeout(15000),
        });
        if (!res.ok || cancelled) return;
        const j = await res.json();
        const mine = (j.statuses || []).find((s) => s.project_id === projectId);
        if (!cancelled && mine) {
          // "unreachable" (network) is a TEMPORARY state and must
          // never be treated the same as revoked.
          setStatus(mine.status);
          setReason(mine.error || null);
          setInstallationId(mine.installation_id || null);
        }
      } catch { /* leave last known state on a transient blip */ }
    };
    check();
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") check();
    }, 30000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [projectId]);

  const reconnect = useCallback(async () => {
    const token = getToken();
    if (!token) return;
    setPopupBlocked(false);
    setReconnecting(true);
    const url = `${API_BASE}/github/app/install?auth=${encodeURIComponent(token)}`;
    const w = 720, h = 800;
    const left = Math.max(0, window.screenX + (window.outerWidth  - w) / 2);
    const top  = Math.max(0, window.screenY + (window.outerHeight - h) / 2);
    popupRef.current = window.open(
      url, "aurem_github_app_reconnect",
      `width=${w},height=${h},left=${left},top=${top}`,
    );

    // No silent failures. A blocked popup makes `window.open` return
    // null/undefined synchronously with no thrown error — this is the
    // only reliable place to detect it.
    if (!popupRef.current) {
      setReconnecting(false);
      setPopupBlocked(true);
      return;
    }

    const finishIfCovered = async () => {
      try {
        const r = await api.get("/github/app/installations");
        const owner = (activeProject.github_owner || "").toLowerCase();
        const repo  = (activeProject.github_repo  || "").toLowerCase();
        for (const inst of (r.data?.installations || [])) {
          const hit = (inst.repositories || []).find(
            (x) => (x.full_name || "").toLowerCase() === `${owner}/${repo}`,
          );
          if (hit) {
            await api.patch(`/cto/projects/${projectId}`, {
              installation_id: inst.installation_id,
            });
            setStatus("connected");
            setReason(null);
            return true;
          }
        }
      } catch { /* keep polling */ }
      return false;
    };

    const started = Date.now();
    const poll = setInterval(async () => {
      if (popupRef.current?.closed) {
        clearInterval(poll);
        await finishIfCovered();
        setReconnecting(false);
        return;
      }
      if (Date.now() - started > 180_000) {
        clearInterval(poll);
        setReconnecting(false);
        return;
      }
      if (await finishIfCovered()) {
        clearInterval(poll);
        try { popupRef.current?.close?.(); } catch { /* xorigin */ }
        setReconnecting(false);
      }
    }, 1500);
  }, [activeProject, projectId]);

  const appLevel = APP_LEVEL_REASONS.has(reason);
  const suspended = reason === "installation_suspended";
  const neverConnected = reason === "app_installation_missing";
  const reasonLabel = reason ? (REASON_LABEL[reason] || reason) : null;

  return {
    status, reason, reasonLabel, installationId,
    reconnecting, popupBlocked, setPopupBlocked,
    appLevel, suspended, neverConnected,
    reconnect,
  };
}
