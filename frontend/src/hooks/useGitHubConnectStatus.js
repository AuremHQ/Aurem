/**
 * useGitHubConnectStatus.js — 2026-08 hardening (GitHub Connect: PERMANENT fix).
 *
 * ONE shared hook, used by BOTH AddProjectWizard.jsx and NewUserWizard.jsx,
 * so the connect flow is a PURE FUNCTION of one authoritative endpoint
 * (`GET /github/app/status`) instead of each wizard guessing from its own
 * copy of a postMessage listener + an "installation count went up" poll.
 *
 * That old count-based poll could never detect "user added a repo to an
 * EXISTING installation" (the common case after the first connect) — this
 * hook reads `state` (pending|connected|error) from the live-verified
 * endpoint instead, so it can't miss that case, and it can't get stuck:
 * a real ~60s timeout flips to a designed "didn't finish, try again" state
 * instead of hanging forever.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { api, API_BASE, getToken } from "../lib/api";
import { trackFunnel } from "../lib/githubFunnel";

const POLL_INTERVAL_MS = 2500;
const MAX_WAIT_MS = 60_000;
// 2026-09-01 — CONFIRMED FIX (connect-flow investigation, Bug-2/Bug-3
// shared root): once GitHub confirms the install itself
// (`installation_active`), keep a slow background refresh going for
// up to this long so a repo list that's still propagating on GitHub's
// side (confirmed in real production data to lag well past 60s) fills
// in on its own instead of leaving the picker looking permanently
// empty/broken.
const BACKGROUND_SYNC_INTERVAL_MS = 5_000;
const BACKGROUND_SYNC_MAX_MS = 2 * 60_000;

const IDLE_STATUS = {
  installation_active: false,
  installations: [],
  repos: [],
  connected_repo: null,
  state: "idle",
  error: null,
};

export default function useGitHubConnectStatus() {
  const [status, setStatus] = useState(IDLE_STATUS);
  const [connecting, setConnecting] = useState(false);
  const [timedOut, setTimedOut] = useState(false);
  const [denied, setDenied] = useState(false);
  // 2026-09-09 — "full isolation" fix (founder-reported: a brand new
  // project's connect wizard was showing/auto-picking repos from
  // OLD, unrelated installations e.g. `polaris.butler.org`/`AuronHQ`
  // installed for a completely different earlier project). `status`
  // is authoritative but GLOBAL — every installation this GitHub
  // account has EVER granted, forever. `freshInstallIds` narrows that
  // down to ONLY the installation(s) actually touched during THIS
  // startConnect() session, so a new-project wizard can show nothing
  // at all until the user has freshly gone through GitHub's own
  // picker in the current attempt — never silently reusing history.
  const [freshInstallIds, setFreshInstallIds] = useState([]);
  const popupRef = useRef(null);
  const pollRef = useRef(null);
  const bgSyncRef = useRef(null);
  const startRef = useRef(0);
  const preConnectIdsRef = useRef(new Map());
  // Kept in sync with `status` via the effect below so
  // `startConnect` (a stable useCallback, deliberately NOT
  // re-created on every status change) can read the CURRENT status
  // without a stale-closure bug.
  const statusRef = useRef(status);
  useEffect(() => { statusRef.current = status; }, [status]);

  const fetchStatus = useCallback(async () => {
    try {
      const r = await api.get("/github/app/status");
      setStatus(r.data);
      return r.data;
    } catch {
      return null;
    }
  }, []);

  const stopBackgroundSync = useCallback(() => {
    if (bgSyncRef.current) { clearInterval(bgSyncRef.current); bgSyncRef.current = null; }
  }, []);

  // 2026-09-01 — Bug-2/Bug-3 fix, part 2: `installation_active` means
  // GitHub confirmed the grant — the connect flow itself is DONE.
  // `state !== "connected"` at that point means only the repo LIST is
  // still propagating on GitHub's side (real-world lag observed well
  // past 60s), not that anything failed. Keep quietly re-checking so
  // the picker fills in without the user having to do anything.
  const startBackgroundRepoSync = useCallback(() => {
    if (bgSyncRef.current) return;
    const startedAt = Date.now();
    bgSyncRef.current = setInterval(async () => {
      const data = await fetchStatus();
      if ((data && data.state === "connected") || Date.now() - startedAt > BACKGROUND_SYNC_MAX_MS) {
        stopBackgroundSync();
      }
    }, BACKGROUND_SYNC_INTERVAL_MS);
  }, [fetchStatus, stopBackgroundSync]);

  // Initial check on mount — so a wizard that opens for a user who is
  // ALREADY connected (e.g. adding a 2nd project) sees that immediately,
  // no popup needed.
  useEffect(() => { fetchStatus(); }, [fetchStatus]);

  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  }, []);

  useEffect(() => () => { stopPolling(); stopBackgroundSync(); }, [stopPolling, stopBackgroundSync]);

  const startConnect = useCallback(() => {
    const token = getToken();
    if (!token) return { ok: false, reason: "no_session" };
    const url = `${API_BASE}/github/app/install?auth=${encodeURIComponent(token)}`;
    const w = 720, h = 800;
    const left = Math.max(0, window.screenX + (window.outerWidth - w) / 2);
    const top = Math.max(0, window.screenY + (window.outerHeight - h) / 2);
    popupRef.current = window.open(
      url, "aurem_github_app_install",
      `width=${w},height=${h},left=${left},top=${top}`,
    );
    // 2026-08-27 · Journey Watch Phase 0 — this is the #2 dark click
    // from the signup drop-off investigation: this button previously
    // fired ZERO client-side tracking. Fire connect_repo_click here,
    // AFTER attempting window.open, so `popup_blocked` is captured as
    // real evidence instead of looking identical to "never clicked".
    trackFunnel("connect_repo_click", "wizard", {
      popup_blocked: !popupRef.current,
    });
    if (!popupRef.current) return { ok: false, reason: "popup_blocked" };

    // 2026-08-27 · Journey Watch Phase 0 — popup actually opened, so
    // GitHub's own auth/install screen is showing. Distinct from
    // connect_repo_click (fired above even on popup_blocked) so
    // Journey Watch can tell "clicked" apart from "reached GitHub".
    trackFunnel("github_auth_started", "wizard");

    setTimedOut(false);
    setDenied(false);
    setConnecting(true);
    startRef.current = Date.now();
    // 2026-09-09 — snapshot BEFORE this attempt so the poll-success
    // branch below can tell "brand new installation" apart from
    // "pre-existing installation from a past session" by set-diff.
    // Snapshots repo COUNT per id too (not just the id set) — see the
    // 2026-09-09-b fix below for why.
    preConnectIdsRef.current = new Map(
      (statusRef.current.installations || []).map(
        (i) => [String(i.installation_id), (i.repositories || []).length],
      ),
    );
    stopPolling();
    stopBackgroundSync();
    pollRef.current = setInterval(async () => {
      const data = await fetchStatus();
      // 2026-09-01 — CONFIRMED FIX (connect-flow investigation,
      // Bug-2 Mike Froedge / Bug-3 Michael Pelletier — one shared
      // root, confirmed from real production state pulls): exit on
      // `installation_active`, NOT `state === "connected"`.
      // `installation_active` is GitHub confirming the grant itself;
      // `state === "connected"` ADDITIONALLY requires the repo-list
      // API to have returned a non-empty list, which real production
      // data showed can lag for well over an hour after a genuinely
      // successful install. The old code only checked `state`, so a
      // real success + a closed popup (the bridge auto-closes in
      // 400ms) during that lag fired a FALSE app_install_denied and
      // stranded the user on an empty repo picker with nothing to
      // click — indistinguishable from "nothing happened".
      if (data && data.installation_active) {
        // 2026-09-09-c — CRITICAL fix (3rd founder recording, real
        // production repro): `installation_active` is true the
        // MOMENT any installation exists for this account, globally
        // — including ones from a completely unrelated past session.
        // The code below used to stop polling + force-close the
        // popup + declare success the INSTANT this went true, even
        // if the user was still mid-flow (e.g. still inside GitHub's
        // own "Sign in with Google" sub-flow, picking a Google
        // account, clearing a Windows Hello PIN prompt) authorizing
        // something entirely different. For ANY returning user with
        // an existing installation (this test account has 12+), that
        // condition is true on literally the FIRST poll tick — often
        // while they're still on GitHub/Google's own pages — silently
        // killing their in-progress popup out from under them and
        // declaring "connected" against stale data (matches the
        // recording exactly: popup dies at "about:blank" right after
        // Google account selection, main UI is left showing a stale
        // "Connected to: RepoReady" from an unrelated old install).
        // Fix: compute the SAME new-or-changed diff used for
        // freshInstallIds FIRST, and only treat this as success once
        // something genuinely new/changed is found. A brand-new user
        // (nothing pre-existing at all) still succeeds immediately —
        // there's nothing stale to wait out.
        const currentIds = (data.installations || []).map((i) => String(i.installation_id));
        const newOrChangedIds = currentIds.filter((id) => {
          const before = preConnectIdsRef.current.get(id);
          if (before === undefined) return true; // brand new installation
          const inst = (data.installations || []).find((i) => String(i.installation_id) === id);
          return (inst?.repositories || []).length !== before; // repo grant changed
        });
        if (newOrChangedIds.length === 0 && preConnectIdsRef.current.size > 0) {
          return; // nothing this session has done yet — keep waiting, leave the popup alone
        }
        stopPolling();
        setConnecting(false);
        try { popupRef.current?.close?.(); } catch { /* cross-origin */ }
        trackFunnel("app_install_granted", "wizard");
        // 2026-09-09 — full-isolation fix, part 2: postMessage/claim
        // (below) is the precise source when it arrives, but it can
        // race or drop on some browser/extension combos. This is the
        // guaranteed-to-run fallback, computed above.
        //
        // 2026-09-09-b — founder-reported (2nd recording): the
        // ORIGINAL fallback here was "no new id ⇒ mark ALL currently-
        // active installations fresh", which leaked unrelated sibling
        // orgs. Fixed by comparing repo COUNT per id (above), not
        // just the id set.
        setFreshInstallIds((prev) => {
          const merged = new Set(prev);
          for (const id of newOrChangedIds) merged.add(id);
          return Array.from(merged);
        });
        if (data.state !== "connected") {
          startBackgroundRepoSync();
        }
        return;
      }
      // GitHub Apps have no real "deny" callback (unlike OAuth Apps) —
      // the popup just closing without ever reaching an active
      // installation is the only observable signal that the user
      // backed out (a real success is caught above BEFORE this runs).
      if (popupRef.current?.closed) {
        stopPolling();
        setConnecting(false);
        setDenied(true);
        trackFunnel("app_install_denied", "wizard", { reason: "popup_closed" });
        return;
      }
      if (Date.now() - startRef.current > MAX_WAIT_MS) {
        stopPolling();
        setConnecting(false);
        setTimedOut(true);
        trackFunnel("app_install_denied", "wizard", { reason: "client_timeout" });
      }
    }, POLL_INTERVAL_MS);
    return { ok: true };
  }, [fetchStatus, stopPolling, stopBackgroundSync, startBackgroundRepoSync]);

  const retry = useCallback(() => startConnect(), [startConnect]);

  // 2026-09-05 — founder-reported (with console/network evidence):
  // on some real-world browser/extension combinations, `window.open()`
  // returns a non-null window handle (so we never even hit the
  // `popup_blocked` check) yet no visible tab ever appears — some ad-
  // blockers/privacy extensions neutralize the call silently instead
  // of rejecting it outright. A programmatic `window.open()` call can
  // ALWAYS be intercepted like this; a real user click on a real
  // `<a href target="_blank">` link CANNOT be blocked by any browser
  // or extension (that's the literal distinction browsers use to tell
  // "the user meant to navigate" from "a script is spawning a popup").
  // Expose the exact same install URL so callers can render one as a
  // guaranteed-working fallback, always visible while `connecting`.
  const installUrl = getToken()
    ? `${API_BASE}/github/app/install?auth=${encodeURIComponent(getToken())}`
    : null;

  // 2026-09-04 — claim fallback, part of the GitHub-drops-`state`-on-
  // reconnect fix. `/callback` now self-heals by reusing an already-
  // linked installation, but a row that has NEVER been linked (webhook
  // landed first, before any authenticated browser callback) has
  // nothing to reuse. This tab IS authenticated and DOES know the
  // installation_id GitHub just handed back — claim it directly
  // instead of depending on the state round-trip at all. Best-effort:
  // swallow failures (already-linked / too-old / not-found yet are
  // all fine — the poll below is still authoritative either way).
  const claimInstallation = useCallback(async (installId) => {
    if (!installId) return;
    // 2026-09-09 — precise source: postMessage/redirect hand us the
    // EXACT installation_id GitHub just granted, no diffing needed.
    setFreshInstallIds((prev) => (prev.includes(String(installId)) ? prev : [...prev, String(installId)]));
    try { await api.post("/github/app/claim", { installation_id: Number(installId) }); }
    catch { /* best-effort — polling below remains authoritative */ }
  }, []);

  // Fast-path — if the bridge page's postMessage arrives, poll right
  // away instead of waiting for the next interval tick. Not required
  // for correctness (the interval poll is authoritative either way).
  useEffect(() => {
    async function onMessage(e) {
      const d = e.data;
      if (!d || d.type !== "aurem-app-installed") return;
      if (d.status === "success" && d.install_id) {
        await claimInstallation(d.install_id);
      }
      fetchStatus();
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [fetchStatus, claimInstallation]);

  // 2026-09-04 — non-popup fallback landing: when the guaranteed-
  // working `<a target="_blank" rel="noopener">` link is used instead
  // of `window.open()`, the bridge page has no `opener` to postMessage
  // (that's the whole point of `noopener`) — it meta-refreshes the new
  // tab to `/dashboard?flow=connect-repo&install=success&install_id=`
  // instead. Catch that landing here too, claim, then strip the params
  // so a refresh doesn't re-fire it.
  useEffect(() => {
    const qs = new URLSearchParams(window.location.search);
    if (qs.get("flow") !== "connect-repo") return;
    const installId = qs.get("install_id");
    const isSuccess = qs.get("install") === "success";
    (async () => {
      if (isSuccess && installId) await claimInstallation(installId);
      await fetchStatus();
    })();
    qs.delete("flow"); qs.delete("install"); qs.delete("install_id"); qs.delete("app_pending"); qs.delete("err");
    const rest = qs.toString();
    window.history.replaceState({}, "", window.location.pathname + (rest ? `?${rest}` : ""));
    // Intentionally mount-only — this reads the URL exactly once on
    // load; claimInstallation/fetchStatus are stable useCallback refs.
  }, []);

  return { status, connecting, timedOut, denied, startConnect, retry, refresh: fetchStatus, installUrl, freshInstallIds };
}
