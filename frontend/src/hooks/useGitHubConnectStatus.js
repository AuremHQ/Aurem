/** GitHub grants access; the project wizard separately persists the selected repo. */
import { useCallback, useEffect, useRef, useState } from "react";
import { api, API_BASE, getToken } from "../lib/api";
import { trackFunnel } from "../lib/githubFunnel";

const POLL_INTERVAL_MS = 2500;
// 2026-02 — Part 2.1 (stuck-users fix, "OUR bug" per founder): this
// was 60_000 (60s). Root cause of the `client_timeout` reason logged
// for sidban15 — the GitHub App install popup can genuinely take
// longer than 60s for real flows (org-admin-approval prompts, 2FA,
// reviewing the repo-access picker slowly) WHILE THE POPUP IS STILL
// OPEN and the user is still actively working through it. The old
// 60s budget declared failure on those users even though nothing
// was actually broken. Raised to match this same codebase's own
// evidence-based budget for the closely related repo-access-
// propagation lag (`NewUserWizard.jsx`'s `_REPO_ACCESS_RETRY_BUDGET_MS
// = 2 * 60_000`) — this only extends patience while the popup is
// open; a closed popup still resolves immediately via the separate
// `popup_closed` branch below, unaffected.
const MAX_WAIT_MS = 3 * 60_000;
const SYNC_INTERVAL_MS = 5000;
const SYNC_MAX_MS = 120_000;
const IDLE_STATUS = {
  installation_active: false, installations: [], repos: [],
  connected_repo: null, state: "idle", error: null,
};
const grantSignature = (i) => (i.repositories || [])
  .map((r) => String(r.full_name || r.id).toLowerCase()).sort().join("\n");
const snapshot = (data) => new Map((data.installations || [])
  .map((i) => [String(i.installation_id), grantSignature(i)]));

export default function useGitHubConnectStatus() {
  const [status, setStatus] = useState(IDLE_STATUS);
  const [connecting, setConnecting] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [timedOut, setTimedOut] = useState(false);
  const [denied, setDenied] = useState(false);
  const [pending, setPending] = useState(false);
  const [connectionError, setConnectionError] = useState(null);
  const [freshInstallIds, setFreshInstallIds] = useState([]);
  const popupRef = useRef(null);
  const pollRef = useRef(null);
  const syncRef = useRef(null);
  const statusRef = useRef(null);
  const requestRef = useRef(null);
  const attemptRef = useRef(0);
  const baselineRef = useRef(null);
  const confirmedRef = useRef([]);
  const mountedRef = useRef(true);

  // One in-flight request per hook. Slow GitHub responses must not overlap,
  // arrive out of order, or overwrite state after this wizard has unmounted.
  const fetchStatus = useCallback(async () => {
    if (!requestRef.current) {
      requestRef.current = api.get("/github/app/status").then((r) => {
        if (mountedRef.current) { statusRef.current = r.data; setStatus(r.data); }
        return r.data;
      }).catch(() => null).finally(() => { requestRef.current = null; });
    }
    return requestRef.current;
  }, []);

  const stopPolling = useCallback(() => {
    clearInterval(pollRef.current); pollRef.current = null;
  }, []);
  const stopSync = useCallback(() => {
    clearInterval(syncRef.current); syncRef.current = null;
  }, []);

  // Readiness is per attempted installation, never the global `connected`
  // flag: an unrelated old project's repos cannot finish this synchronization.
  const applyConfirmed = useCallback((data, ids) => {
    const matches = (data?.installations || [])
      .filter((i) => ids.includes(String(i.installation_id)));
    if (matches.length) {
      setFreshInstallIds(matches.map((i) => String(i.installation_id)));
      setConnecting(false); setDenied(false); setTimedOut(false);
    }
    return matches.length === ids.length && ids.length > 0
      && matches.every((i) => (i.repositories || []).length > 0);
  }, []);

  const startRepoSync = useCallback((ids) => {
    stopSync(); setSyncing(true);
    const started = Date.now();
    const attempt = attemptRef.current;
    let busy = false;
    syncRef.current = setInterval(async () => {
      if (busy) return;
      busy = true;
      try {
        const data = await fetchStatus();
        if (!mountedRef.current || attempt !== attemptRef.current) return;
        if (applyConfirmed(data, ids)) {
          stopSync(); setSyncing(false); setConnectionError(null);
        } else if (Date.now() - started >= SYNC_MAX_MS) {
          stopSync(); setSyncing(false); setConnecting(false);
          setConnectionError("GitHub access was reported, but this account's repository list is not ready. Retry the refresh or check the selected GitHub account.");
        }
      } finally { busy = false; }
    }, SYNC_INTERVAL_MS);
  }, [fetchStatus, applyConfirmed, stopSync]);

  const finishGrant = useCallback((data, ids) => {
    stopPolling(); setConnecting(false); setDenied(false); setTimedOut(false);
    setPending(false); setConnectionError(null);
    confirmedRef.current = ids;
    if (applyConfirmed(data, ids)) { stopSync(); setSyncing(false); }
    else startRepoSync(ids);
    trackFunnel("app_install_granted", "wizard");
    try { popupRef.current?.close?.(); } catch { /* cross-origin window */ }
  }, [applyConfirmed, startRepoSync, stopPolling, stopSync]);

  const checkGrant = useCallback((data) => {
    if (!data?.installation_active) return false;
    if (baselineRef.current === null) {
      // Connect may be clicked before the initial status request resolves.
      // Establish a baseline first rather than treating old installs as new.
      baselineRef.current = snapshot(data);
      return false;
    }
    const ids = (data.installations || []).filter((i) => {
      const before = baselineRef.current.get(String(i.installation_id));
      return before === undefined || before !== grantSignature(i);
    }).map((i) => String(i.installation_id));
    if (!ids.length) return false;
    finishGrant(data, ids);
    return true;
  }, [finishGrant]);

  const startConnect = useCallback(() => {
    const token = getToken();
    if (!token) return { ok: false, reason: "no_session" };
    const url = `${API_BASE}/github/app/install?auth=${encodeURIComponent(token)}`;
    const popup = window.open(url, "aurem_github_app_install", "width=720,height=800");
    trackFunnel("connect_repo_click", "wizard", { popup_blocked: !popup });
    if (!popup) return { ok: false, reason: "popup_blocked" };
    popupRef.current = popup;
    stopPolling(); stopSync();
    const attempt = ++attemptRef.current;
    const started = Date.now();
    baselineRef.current = statusRef.current ? snapshot(statusRef.current) : null;
    confirmedRef.current = [];
    setFreshInstallIds([]); setConnecting(true); setSyncing(false);
    setTimedOut(false); setDenied(false); setPending(false); setConnectionError(null);
    trackFunnel("github_auth_started", "wizard");
    let busy = false;
    const current = () => mountedRef.current && attempt === attemptRef.current;
    pollRef.current = setInterval(async () => {
      if (busy || !current()) return;
      busy = true;
      try {
        const data = await fetchStatus();
        if (!current() || checkGrant(data)) return;
        if (popup.closed) {
          stopPolling();
          // A callback can complete during this grace window. Its generation
          // invalidates this continuation, so success cannot become denial.
          for (let i = 0; i < 2; i++) {
            await new Promise((resolve) => setTimeout(resolve, 1000));
            if (!current()) return;
            const next = await fetchStatus();
            if (!current() || checkGrant(next)) return;
          }
          setConnecting(false); setDenied(true);
          trackFunnel("app_install_denied", "wizard", { reason: "popup_closed" });
        } else if (Date.now() - started >= MAX_WAIT_MS) {
          stopPolling(); setConnecting(false); setTimedOut(true);
          trackFunnel("app_install_denied", "wizard", { reason: "client_timeout" });
        }
      } finally { busy = false; }
    }, POLL_INTERVAL_MS);
    return { ok: true };
  }, [fetchStatus, checkGrant, stopPolling, stopSync]);

  const completeCallback = useCallback(async (data) => {
    const attempt = ++attemptRef.current;
    stopPolling(); stopSync(); setSyncing(false); setConnecting(false);
    setDenied(false); setTimedOut(false);
    if (data.status !== "success") {
      setPending(data.status === "pending");
      setConnectionError(data.status === "pending"
        ? "Waiting for your GitHub organization admin to approve access."
        : "GitHub connection could not be confirmed. Please try again.");
      return;
    }
    const id = Number(data.install_id);
    if (!Number.isSafeInteger(id) || id <= 0) {
      setConnectionError("GitHub returned an invalid installation. Please try again.");
      return;
    }
    // Callback IDs are hints, not authorization. Only the authenticated
    // status result can expose repos, and /projects/add verifies them again.
    try { await api.post("/github/app/claim", { installation_id: id }); }
    catch (error) {
      if ([401, 403, 409].includes(error?.response?.status)) {
        setConnectionError(error.response.status === 403
          ? "This installation is linked to a different Aurem account. Use the matching account or contact your admin."
          : "This installation could not be linked. Sign in with the matching Aurem account and retry.");
        return;
      }
      // Delayed webhook / transient request: user-scoped status remains truth.
    }
    const latest = await fetchStatus();
    if (!mountedRef.current || attempt !== attemptRef.current) return;
    finishGrant(latest, [String(id)]);
  }, [fetchStatus, finishGrant, stopPolling, stopSync]);

  useEffect(() => {
    mountedRef.current = true;
    fetchStatus();
    return () => {
      mountedRef.current = false; attemptRef.current += 1;
      stopPolling(); stopSync();
    };
  }, [fetchStatus, stopPolling, stopSync]);

  useEffect(() => {
    const onMessage = (event) => {
      const origin = new URL(API_BASE, window.location.origin).origin;
      if (event.origin !== origin || !popupRef.current || event.source !== popupRef.current) return;
      if (event.data?.type === "aurem-app-installed") completeCallback(event.data);
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [completeCallback]);

  useEffect(() => {
    const qs = new URLSearchParams(window.location.search);
    if (qs.get("flow") !== "connect-repo") return;
    completeCallback({
      status: qs.get("install") === "success" ? "success" : qs.has("app_pending") ? "pending" : "err",
      install_id: qs.get("install_id"), err: qs.get("err"),
    });
    ["flow", "install", "install_id", "app_pending", "err"].forEach((key) => qs.delete(key));
    window.history.replaceState({}, "", window.location.pathname + (qs.size ? `?${qs}` : ""));
  }, [completeCallback]);

  const refresh = useCallback(async () => {
    const latest = await fetchStatus();
    if (confirmedRef.current.length) finishGrant(latest, confirmedRef.current);
    return latest;
  }, [fetchStatus, finishGrant]);
  const token = getToken();
  const installUrl = token ? `${API_BASE}/github/app/install?auth=${encodeURIComponent(token)}` : null;
  return {
    status, connecting, syncing, timedOut, denied, pending, connectionError,
    startConnect, retry: startConnect, refresh, installUrl, freshInstallIds,
  };
}