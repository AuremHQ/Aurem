/**
 * useActiveProject.js — hook: subscribe to active-project changes from
 * anywhere in the tree. Extracted from TabBar.jsx (2026-08-27,
 * mechanical split — no behaviour change) to keep that file under the
 * platform's file-size guard.
 *
 * 2026-09-08 — Turn-Completion Contract, Section 6 (Approve button
 * mount race). Root cause of a recurring "the Approve button didn't
 * load" bug: `/cto/projects/list` had ZERO retry on failure — one
 * transient network blip (common right when a page full of chat
 * bubbles is loading, high request contention) left `project` null
 * for the entire page session (nothing re-triggers a re-fetch), which
 * cascades into `canShip=false` in MessageBubble.jsx → the Approve
 * button replaced by "Switch to a connected project" — permanently,
 * until a manual page refresh happened to land on a lucky fetch.
 * `fetchProjectsWithRetry` below adds ONE silent retry with a short
 * backoff before giving up, matching the "silently retry once before
 * showing the user a visible Retry" contract — most transient blips
 * never surface to the user at all now.
 */
import { useEffect, useState } from "react";
import { api, getToken } from "../lib/api";
import { getActiveProjectId, setActiveProjectId } from "./activeProject";

async function fetchProjectsWithRetry() {
  try {
    return await api.get("/cto/projects/list");
  } catch (firstErr) {
    await new Promise((r) => setTimeout(r, 1500));
    try {
      return await api.get("/cto/projects/list");
    } catch (retryErr) {
      console.warn(
        "useActiveProject: /cto/projects/list failed twice (silent retry exhausted)",
        retryErr,
      );
      throw retryErr;
    }
  }
}

export function useActiveProject() {
  const [pid, setPid] = useState(() => getActiveProjectId());
  // Iter 212m-105 — Synchronously hydrate from localStorage cache so
  // downstream consumers (AskAdvisor projectId, ChatPanel activeProject,
  // TopBar breadcrumb) get a non-null value on the very first render
  // after login. Without this, the Ask Advisor's tool calls hit the
  // backend with project_id=null and the model replies "No repo
  // connected" even when the user has projects.
  const [project, setProject] = useState(() => {
    if (!pid) return null;
    try {
      const raw = localStorage.getItem("aurem_projects_cache");
      if (!raw) return null;
      const cached = JSON.parse(raw);
      return (Array.isArray(cached) && cached.find((x) => x.project_id === pid)) || null;
    } catch { return null; }
  });

  useEffect(() => {
    function onChange() { setPid(getActiveProjectId()); }
    window.addEventListener("aurem:project-changed", onChange);
    return () => window.removeEventListener("aurem:project-changed", onChange);
  }, []);

  useEffect(() => {
    // 2026-09-09 — this hook can mount on public/unauthenticated routes
    // (e.g. the landing page nav) via shared layout components. Without
    // this guard it fires `/cto/projects/list` for every anonymous
    // visitor, which always 401s — a console error PageSpeed flags and
    // a wasted network request on the landing page's critical path.
    if (!getToken()) return;
    if (!pid) {
      // Iter 212m-190 — even with no saved active id, still fetch the
      // list so we can AUTO-SEED the first wired project. This is the
      // reliable auto-restore path for:
      //   • fresh browsers with empty localStorage
      //   • users whose last active project was deleted while logged out
      //   • incognito sessions
      let cancelled = false;
      fetchProjectsWithRetry()
        .then((r) => {
          if (cancelled) return;
          const list = r.data?.projects || [];
          try { localStorage.setItem("aurem_projects_cache", JSON.stringify(list)); }
          catch { /* quota — ignore */ }
          if (list.length === 0) return;
          const wired = list.filter((p) => p.github_owner && p.github_repo);
          const target = wired[0] || list[0];
          if (target) {
            // setActiveProjectId dispatches `aurem:project-changed`
            // which re-runs this hook with the new pid, so we do not
            // manually call setProject here.
            setActiveProjectId(target.project_id);
          }
        })
        .catch(() => { /* offline / auth failure — silent, retry already exhausted */ });
      return () => { cancelled = true; };
    }
    let cancelled = false;
    fetchProjectsWithRetry()
      .then((r) => {
        if (cancelled) return;
        const list = r.data?.projects || [];
        // Keep the cache fresh for the next mount.
        try { localStorage.setItem("aurem_projects_cache", JSON.stringify(list)); }
        catch { /* quota — ignore */ }
        const p = list.find((x) => x.project_id === pid);
        if (p) {
          setProject(p);
          return;
        }
        // Iter 212m-190 — saved active id points to a project that no
        // longer exists (deleted while logged out, revoked, etc.).
        // Auto-heal by falling back to the first wired project instead
        // of leaving the UI stuck on a ghost.
        if (list.length > 0) {
          const wired = list.filter((x) => x.github_owner && x.github_repo);
          const target = wired[0] || list[0];
          if (target) {
            setActiveProjectId(target.project_id);   // triggers hook re-run
            return;
          }
        }
        // No projects at all — clear the stale pin so consumers render
        // the "no repo" state correctly.
        setProject(null);
        setActiveProjectId(null);
      })
      .catch(() => { /* keep cached project intact — retry already exhausted */ });
    return () => { cancelled = true; };
  }, [pid]);

  return project;
}
