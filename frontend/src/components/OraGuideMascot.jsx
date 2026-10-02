/**
 * OraGuideMascot.jsx — fixed-position ORA guide + help entry point.
 *
 * 2026-08-20 — replaces GlobalHelpFAB (the old "Need help?" pill) at
 * the exact same bottom-right spot. One floating element, not two.
 *
 * Lives permanently once logged in, NEVER moves toward other UI —
 * guidance to a specific button happens via `useGuideSpotlight`
 * (a glow ring on the real element), not by the mascot relocating.
 *
 * Stage-aware auto-open: polls GET /auth/me/funnel-stage (live,
 * no 24h gate — see services/funnel_nudge_cron.py::current_stage_for_user)
 * and opens ONCE per stage-group per browser session (sessionStorage,
 * NOT the email nudge system's permanent Mongo dedup — those are
 * different semantics: "once this session" vs "once ever").
 * Auto-closes if the user completes the step before dismissing.
 *
 * Clicking the mascot any other time opens a general "How can I
 * help?" state with a bridge to the real Advisor panel (kept
 * separate from Advisor by design — Advisor is desktop-only and
 * lives in the top bar; merging would either break mobile help or
 * reintroduce the composer-overlap bug Advisor was moved to avoid).
 */
import React, { useEffect, useRef, useState, useCallback } from "react";
import { useLocation } from "react-router-dom";
import { Sparkles, Bug, CheckCircle2, Zap, ShieldCheck } from "lucide-react";
import { api } from "../lib/api";
import { useGuideSpotlight, GuideSpotlightStyle } from "../hooks/useGuideSpotlight.jsx";
import { shouldHide } from "./GlobalHelpFAB";
import { useActiveProject } from "./TabBar";
import { Z_FLOATING_GUIDE } from "../lib/zIndex";
import { toast } from "./Toast";
import { SLASH_COMMANDS } from "./SlashCommandMenu";

// 2026-08-23 — founder direction: the "ORA GUIDE" suggestions card that
// used to live pinned in the Ask Advisor sidebar now lives HERE instead.
// Once onboarding is done (no pending stage bubble) and a project is
// active, this mascot shows project-scoped suggestions/issues instead
// of the generic "How can I help?" menu — same underlying data source
// PromptStarterPanel used (`/findings/starter-suggestions`).
const SUGGESTION_ICONS = { sparkles: Sparkles, bug: Bug, check: CheckCircle2, zap: Zap, shield: ShieldCheck };
const FIXLIST_SEV_DOT = { critical: "#ef4444", high: "#f97316", medium: "#facc15", low: "#38bdf8", info: "#94a3b8" };
const FALLBACK_SUGGESTIONS = [
  { slug: "build-something-new", icon_hint: "sparkles", label: "Build something new", example: "I want a contact form on my website" },
  { slug: "somethings-broken", icon_hint: "bug", label: "Something's broken", example: "The login button doesn't work, please fix it" },
  { slug: "check-everything-working", icon_hint: "check", label: "Check everything is working", example: "Check my whole website for any bugs" },
  { slug: "security-check", icon_hint: "shield", label: "Security check", example: "Check my code for any security problems" },
];

const STAGE_COPY = {
  // stage1_github_started and stage4_fully_inactive collapse into the
  // same on-screen message — there's nothing different to show a user
  // who never clicked vs one who clicked and didn't finish; same wizard
  // screen either way.
  connect_github: {
    message: "Connect your repo to get started — click Connect GitHub above.",
    target: "connect-github-btn",
  },
  project_pending: {
    message: "One step left — pick a repo above, then click Continue to finish setup.",
    target: "continue-btn",
  },
  no_chat: {
    message: "Your project's ready — try asking ORA to fix a bug or add a feature. A few ideas below.",
    target: null,
  },
};

function stageToGroup(stage) {
  if (stage === "stage1_github_started" || stage === "stage4_fully_inactive") return "connect_github";
  if (stage === "stage2_project_pending") return "project_pending";
  if (stage === "stage3_no_chat") return "no_chat";
  return null;
}

const SEEN_KEY_PREFIX = "ora_guide_seen_v1_";
function alreadyShownThisSession(group) {
  try { return sessionStorage.getItem(SEEN_KEY_PREFIX + group) === "1"; } catch { return false; }
}
function markShownThisSession(group) {
  try { sessionStorage.setItem(SEEN_KEY_PREFIX + group, "1"); } catch { /* ignore */ }
}

const POLL_MS = 15000;

// 2026-10 — "Fix List → robot" spec. The old top-of-chat bar is gone;
// its data (GET /findings/fix-list) now feeds this mascot instead.
// Severity order comes pre-sorted from the backend; this only adds
// the "never start with a test/vendor file" rule the backend has no
// concept of — a stable push-to-the-back of anything that looks like
// a test or third-party path, so the FIRST card shown is always a
// real, fixable issue in the user's own code.
const FIXLIST_NUDGE_KEY_PREFIX = "aurem_fixlist_nudge_v1_";
export function isLowPriorityFixFile(path) {
  if (!path) return false;
  const p = String(path).toLowerCase();
  const base = p.split("/").pop() || "";
  return /(^|\/)(tests?|__tests__|__mocks__|spec|node_modules|vendor|third[-_]?party|dist|build|\.venv|venv|site-packages)(\/|$)/.test(p)
    || /\.(test|spec)\.[jt]sx?$/.test(base)
    || /^test_.*\.py$/.test(base);
}
export function orderFixListItems(items) {
  const primary = [];
  const deprioritized = [];
  for (const it of items) (isLowPriorityFixFile(it.file) ? deprioritized : primary).push(it);
  return [...primary, ...deprioritized];
}

// 2026-09-09 — founder ticket Part 0 (round 2): three separate
// floating nudges (this mascot's own stage bubble, the standalone
// "Try a task"/"Connect repo" EngagementNudge badge, and the
// standalone FirstScanNudge banner) were all circled in one
// screenshot as the same clutter problem — "don't show anything
// twice... only one, through the robot." All three now render
// through this single component. `secondaryNudge` holds whichever
// of the OTHER two (post_skip/post_scan from EngagementNudge via the
// `aurem:external-nudge` event, or first_scan computed here) is
// currently eligible; it only ever auto-opens when the stage system
// above has nothing to say (`autoGroup` null) — never simultaneously.
const FIRST_SCAN_DISMISS_KEY = (pid) => `aurem_first_scan_nudge_dismissed_${pid}`;

export default function OraGuideMascot() {
  const { pathname } = useLocation();
  const [loggedIn, setLoggedIn] = useState(() => !!localStorage.getItem("aurem_token"));
  const [open, setOpen] = useState(false);
  const [autoGroup, setAutoGroup] = useState(null);  // stage-triggered bubble
  const [panelMode, setPanelMode] = useState("stage"); // "stage" | "external" | "first_scan" | "general" | "suggestions" | "escalating" | "escalated"
  const pollRef = useRef(null);
  const activeProject = useActiveProject();
  const effectiveProjectId = activeProject?.project_id || null;
  const [projectSuggestions, setProjectSuggestions] = useState(FALLBACK_SUGGESTIONS);
  // 2026-09-09 — Part 0 round 2: the two other floating nudges
  // (EngagementNudge's post_skip/post_scan, FirstScanNudge) now
  // report their eligibility HERE instead of rendering their own
  // badge/banner. Only one can be true at a time in practice
  // (post_skip needs zero projects, first_scan needs a project with
  // no scan ever run, post_scan needs a scan to already exist) —
  // `secondaryNudge` holds whichever arrives, and only auto-opens
  // when the stage bubble above has nothing pending.
  const [secondaryNudge, setSecondaryNudge] = useState(null);
  const [hasUserMessage, setHasUserMessage] = useState(false);
  const firstScanCheckedForRef = useRef(null); // project_id already checked, so remounts don't re-fire

  // 2026-10 — Fix List state (moved here from the old top-of-chat bar).
  const [fixListItems, setFixListItems] = useState([]);
  const [fixListTotal, setFixListTotal] = useState(0);
  const [fixListIndex, setFixListIndex] = useState(0);
  const oraBusyRef = useRef({ busy: false, typing: false });
  const fixListNudgeTimerRef = useRef(null);

  const loadFixList = useCallback(() => {
    if (!effectiveProjectId) { setFixListItems([]); setFixListTotal(0); return; }
    api.get("/findings/fix-list", { params: { project_id: effectiveProjectId, limit: 20 } })
      .then((r) => {
        const d = r?.data || r;
        setFixListItems(orderFixListItems(Array.isArray(d.items) ? d.items : []));
        setFixListTotal(d.total_open || 0);
        setFixListIndex(0);
      })
      .catch(() => { /* soft-fail — badge just stays hidden */ });
  }, [effectiveProjectId]);
  useEffect(() => { loadFixList(); }, [loadFixList]);

  // Live-remove an item the instant a real fix actually ships — same
  // global event the old bar listened to (fired by FixJobContext on
  // a confirmed commit), so the robot never shows a stale count.
  useEffect(() => {
    const onFixed = (e) => {
      const fid = e?.detail?.finding_id;
      if (!fid) return;
      setFixListItems((prev) => {
        const next = prev.filter((it) => it.finding_id !== fid);
        setFixListIndex((i) => (next.length ? i % next.length : 0));
        return next;
      });
      setFixListTotal((n) => Math.max(0, n - 1));
    };
    window.addEventListener("aurem:finding-fixed", onFixed);
    return () => window.removeEventListener("aurem:finding-fixed", onFixed);
  }, []);

  // Track ORA-busy / user-typing (broadcast by ChatPanel) purely to
  // gate the ONE-TIME auto-nudge below — manual clicks on the
  // mascot are never blocked by this.
  useEffect(() => {
    const onStatus = (e) => { oraBusyRef.current = e?.detail || { busy: false, typing: false }; };
    window.addEventListener("aurem:ora-busy-status", onStatus);
    return () => window.removeEventListener("aurem:ora-busy-status", onStatus);
  }, []);

  useEffect(() => {
    setLoggedIn(!!localStorage.getItem("aurem_token"));
    const onStorage = (e) => { if (e.key === "aurem_token") setLoggedIn(!!e.newValue); };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [pathname]);

  const hidden = !loggedIn || shouldHide(pathname);

  // At most one gentle nudge per session: once Fix List has open
  // items for the active project, wait a moment (so it never wins a
  // race against a page-load burst of other panels), then open IF
  // nothing else is already showing/pending and ORA isn't busy/typing
  // right now. Never auto-opens a second time this session even if
  // conditions become true again later.
  useEffect(() => {
    if (fixListNudgeTimerRef.current) { clearTimeout(fixListNudgeTimerRef.current); fixListNudgeTimerRef.current = null; }
    if (hidden || !effectiveProjectId || fixListItems.length === 0) return undefined;
    const key = FIXLIST_NUDGE_KEY_PREFIX + effectiveProjectId;
    let shown = false;
    try { shown = sessionStorage.getItem(key) === "1"; } catch { /* ignore */ }
    if (shown) return undefined;
    fixListNudgeTimerRef.current = setTimeout(() => {
      try { sessionStorage.setItem(key, "1"); } catch { /* ignore */ }
      if (oraBusyRef.current.busy || oraBusyRef.current.typing) return;
      setOpen((wasOpen) => {
        if (wasOpen || autoGroup || secondaryNudge) return wasOpen;
        setPanelMode("fixlist");
        return true;
      });
    }, 4000);
    return () => { if (fixListNudgeTimerRef.current) clearTimeout(fixListNudgeTimerRef.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hidden, effectiveProjectId, fixListItems.length]);

  // Poll the live stage. Fires the auto-bubble the first time a
  // stage-group is seen this session; auto-closes if the user
  // finishes the step while an auto-bubble is showing.
  useEffect(() => {
    if (hidden) return undefined;
    let cancelled = false;
    async function tick() {
      try {
        const r = await api.get("/auth/me/funnel-stage");
        if (cancelled) return;
        const group = stageToGroup(r.data?.stage);
        if (group && !alreadyShownThisSession(group)) {
          markShownThisSession(group);
          // Stage bubble always wins — drop any secondary nudge so
          // the two can never show at once (founder ticket Part 0).
          setSecondaryNudge(null);
          setAutoGroup(group);
          setPanelMode("stage");
          setOpen(true);
        } else if (autoGroup && group !== autoGroup) {
          // User moved past the stage the open bubble was about —
          // close it automatically, no stale advice lingering.
          setOpen(false);
          setAutoGroup(null);
        }
      } catch { /* best-effort — never blocks the UI */ }
    }
    tick();
    const intervalId = setInterval(tick, POLL_MS);
    pollRef.current = intervalId;
    // 2026-09-09 — bug fix (founder-reported, screenshot): a user who
    // connects a repo DIRECTLY (no skip) still saw the stale
    // "One step left — pick a repo…" bubble for up to POLL_MS
    // afterward, because it was left over from a tick that ran BEFORE
    // the connect finished and this poll hadn't refired yet.
    // NewUserWizard now dispatches this the instant a project is
    // created (fresh connect, scaffold, or picking an already-
    // connected repo) — re-check the live stage right away instead
    // of waiting up to 15s, so a just-connected user never gets
    // asked to connect again.
    const onProjectConnected = () => tick();
    window.addEventListener("aurem:project-connected", onProjectConnected);
    return () => {
      cancelled = true;
      clearInterval(intervalId);
      window.removeEventListener("aurem:project-connected", onProjectConnected);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hidden, pathname]);

  const stageInfo = autoGroup ? STAGE_COPY[autoGroup] : null;
  useGuideSpotlight(open && panelMode === "stage" ? stageInfo?.target : null);

  // Once onboarding no longer has a pending stage bubble, and there's a
  // real project to talk about, pull the same personalized
  // findings-based prompts PromptStarterPanel used to show in the
  // sidebar. Falls back to the generic pool on error/empty/no-project.
  useEffect(() => {
    let cancelled = false;
    if (!effectiveProjectId) { setProjectSuggestions(FALLBACK_SUGGESTIONS); return undefined; }
    api.get(`/findings/starter-suggestions?project_id=${encodeURIComponent(effectiveProjectId)}&limit=4`)
      .then((res) => {
        if (cancelled) return;
        const s = res?.data?.suggestions;
        setProjectSuggestions(Array.isArray(s) && s.length >= 3 ? s.slice(0, 4) : FALLBACK_SUGGESTIONS);
      })
      .catch(() => { if (!cancelled) setProjectSuggestions(FALLBACK_SUGGESTIONS); });
    return () => { cancelled = true; };
  }, [effectiveProjectId]);

  // "post_skip" / "post_scan" — reported by the now-headless
  // EngagementNudge (its own backend caps + eligibility unchanged),
  // handed off here instead of rendering its own badge.
  useEffect(() => {
    function onExternalNudge(e) {
      const d = e?.detail;
      if (!d?.variant) return;
      setSecondaryNudge({ kind: "external", ...d });
      setOpen((wasOpen) => {
        if (wasOpen || autoGroup) return wasOpen; // never preempt what's already showing
        setPanelMode("external");
        return true;
      });
    }
    window.addEventListener("aurem:external-nudge", onExternalNudge);
    return () => window.removeEventListener("aurem:external-nudge", onExternalNudge);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoGroup]);

  // "the user typed anything" — reported by ChatPanel (has no
  // access to it otherwise). Clears the first_scan nudge the moment
  // it's no longer true, same as the original FirstScanNudge's
  // `hasUserMessage` prop gate.
  useEffect(() => {
    function onUserMessage() {
      setHasUserMessage(true);
      setSecondaryNudge((prev) => {
        if (prev?.kind !== "first_scan") return prev;
        setOpen((wasOpen) => (panelMode === "first_scan" ? false : wasOpen));
        return null;
      });
    }
    window.addEventListener("aurem:user-message-sent", onUserMessage);
    return () => window.removeEventListener("aurem:user-message-sent", onUserMessage);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [panelMode]);

  // "first_scan" — folded in from the removed standalone
  // FirstScanNudge banner: shown once a repo's connected but no scan
  // has EVER run for it, and the user hasn't typed anything yet.
  useEffect(() => {
    if (!effectiveProjectId || effectiveProjectId === "home" || hasUserMessage) return undefined;
    if (firstScanCheckedForRef.current === effectiveProjectId) return undefined;
    let dismissed = false;
    try { dismissed = !!localStorage.getItem(FIRST_SCAN_DISMISS_KEY(effectiveProjectId)); } catch { /* ignore */ }
    if (dismissed) return undefined;
    firstScanCheckedForRef.current = effectiveProjectId;
    let cancelled = false;
    api.get("/onboarding/first-scan/status", { params: { project_id: effectiveProjectId } })
      .then((r) => {
        if (cancelled || r?.data?.status !== "skipped") return;
        setSecondaryNudge({
          kind: "first_scan",
          message: "New here? Start with a quick scan of your site — I'll tell you what to fix first.",
          actionLabel: "Scan my site",
        });
        setOpen((wasOpen) => {
          if (wasOpen || autoGroup) return wasOpen;
          setPanelMode("first_scan");
          return true;
        });
      })
      .catch(() => {});
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effectiveProjectId, hasUserMessage]);

  if (hidden) return null;

  function toggleOpen() {
    if (open) { setOpen(false); return; }
    // Manual click shows the onboarding stage bubble if one's still
    // pending; else a secondary nudge if one's eligible; else the
    // Fix List if there's anything open; otherwise once a project is
    // active, project suggestions/issues; else the generic help menu.
    // Manual clicks are NEVER gated by busy/typing — only the
    // one-time auto-nudge is.
    setPanelMode(autoGroup
      ? "stage"
      : (secondaryNudge
        ? secondaryNudge.kind
        : (fixListItems.length > 0
          ? "fixlist"
          : (effectiveProjectId ? "suggestions" : "general"))));
    setOpen(true);
  }

  // "Fix" never writes to the repo by itself — it prefills the same
  // composer the chat uses (identical to a suggestion-chip pick) so
  // the request goes through the exact same scan → propose → ship-
  // approval pipeline as if the user had typed it, never a direct
  // /fix-pipeline call from this component.
  function fixItem(it) {
    if (!it) return;
    const where = it.file ? ` in ${it.file}${it.line ? `:${it.line}` : ""}` : "";
    const prompt = `Fix: ${it.title || it.rule_id}${where}`;
    window.dispatchEvent(new CustomEvent("aurem:starter-pick", { detail: { prompt } }));
    dismiss();
  }
  function fixCurrentItem() { fixItem(fixListItems[fixListIndex]); }
  function nextFixItem() {
    setFixListIndex((i) => (fixListItems.length ? (i + 1) % fixListItems.length : 0));
  }
  function openFixListAll() {
    setPanelMode("fixlist-all");
  }

  function dismiss() {
    setOpen(false);
    setAutoGroup(null);
    if (panelMode === "first_scan" && effectiveProjectId) {
      try { localStorage.setItem(FIRST_SCAN_DISMISS_KEY(effectiveProjectId), "1"); } catch { /* ignore */ }
    }
    if (panelMode === "external" || panelMode === "first_scan") setSecondaryNudge(null);
  }

  // Primary action for the secondary (post_skip / post_scan /
  // first_scan) nudge — same handoffs the removed standalone
  // components used (starter-pick prefill, `/scan` prefill, or the
  // connect wizard via the same window-event pattern `aurem:ora-open`
  // already uses for Advisor).
  function actSecondaryNudge() {
    if (!secondaryNudge) return;
    if (secondaryNudge.variant === "post_skip") {
      // 2026-09-28 "ACTIVATION FIRST" Part 3.2 — offer the 3 starter
      // choices instead of only re-asking for the repo they just
      // skipped (the modal itself also offers "Connect GitHub instead").
      window.dispatchEvent(new Event("aurem:open-starter-choices"));
    } else if (secondaryNudge.kind === "first_scan") {
      window.dispatchEvent(new CustomEvent("aurem:run-scan", { detail: { commandId: "scan" } }));
    } else if (secondaryNudge.prefillTask) {
      window.dispatchEvent(new CustomEvent("aurem:starter-pick", { detail: { prompt: secondaryNudge.prefillTask } }));
    }
    dismiss();
  }

  function openAdvisor() {
    window.dispatchEvent(new Event("aurem:ora-open"));
    dismiss();
  }

  // Same handoff PromptStarterPanel used — prefills the main chat
  // composer (ChatPanel listens for this event), then closes the bubble
  // so the user sees what they picked.
  function pickSuggestion(example) {
    try {
      window.dispatchEvent(new CustomEvent("aurem:starter-pick", { detail: { prompt: example } }));
    } catch { /* noop */ }
    dismiss();
  }

  // 2026-09-21 — one-click real scan trigger for the chips below.
  // Unlike pickSuggestion above (which only prefills text the user
  // still has to send), this dispatches straight to ChatPanel's
  // aurem:run-scan listener, which calls the SAME runSlashCommand()
  // a real "/bug-hunt" dropdown pick uses — the scan actually runs
  // and the user sees real issues, not just a prefilled prompt.
  function runScan(commandId) {
    try {
      window.dispatchEvent(new CustomEvent("aurem:run-scan", { detail: { commandId } }));
    } catch { /* noop */ }
    dismiss();
  }

  async function reportSomethingWrong() {
    setPanelMode("escalating");
    try {
      const stageLabel = autoGroup ? STAGE_COPY[autoGroup].message : "(general help)";
      const body = [
        "[Auto-filed via ORA Guide]",
        `Stage: ${autoGroup || "none"} — "${stageLabel}"`,
        `Page: ${pathname}`,
        `Time: ${new Date().toISOString()}`,
      ].join("\n");
      await api.post("/support/tickets", {
        subject: `ORA Guide — stuck at: ${autoGroup || "general"}`,
        body,
        source: "in_app_guide",
      });
      setPanelMode("escalated");
      toast({ message: "Thanks, we'll take a look.", kind: "success", duration: 3500 });
      setTimeout(() => {
        setOpen((wasOpen) => wasOpen ? false : wasOpen);
        setPanelMode((m) => m === "escalated" ? "stage" : m);
      }, 1400);
    } catch {
      toast({ message: "Couldn't send that just now — try again in a moment.", kind: "error" });
      setPanelMode(autoGroup ? "stage" : "general");
    }
  }

  return (
    <div
      data-testid="ora-guide-mascot-root"
      // 2026-08-20 — was `bottom: 20`, sat directly on top of the chat
      // composer's send button on the chat page (both anchor to the
      // bottom-right corner). Raised clear of the composer footer row.
      style={{ position: "fixed", bottom: 92, right: 20, zIndex: Z_FLOATING_GUIDE /* 2026-06 — must sit ABOVE CookieConsentBanner; at 9990 the banner intercepted every click → founder-reported dead icon */ }}
    >
      <GuideSpotlightStyle />
      <style>{MASCOT_KEYFRAMES}</style>

      {open && (
        <div
          data-testid="ora-guide-bubble"
          style={{
            // 2026-10 — was `bottom: 52`; founder-requested proof check found
            // a 6px overlap between the bubble's bottom-left corner and the
            // chat composer's top-right corner on 1920x900. Bumped for a real
            // margin, not just clearing the measured overlap.
            position: "absolute", bottom: 68, right: 0,
            width: (panelMode === "suggestions" || panelMode === "fixlist-all") ? 300 : 280, background: "#141414",
            border: "1px solid rgba(255,102,8,0.28)",
            borderRadius: 12, padding: "14px 16px",
            boxShadow: "0 12px 40px rgba(0,0,0,0.5)",
            animation: "oraGuideBubbleIn 180ms ease-out",
            // 2026-09-21 — the "suggestions" panel now also lists the
            // 5 scan commands below the project suggestions; cap the
            // height so it scrolls instead of overflowing the
            // viewport on shorter screens. 2026-10 — "fixlist-all" can
            // list up to 20 items, same cap/scroll treatment.
            maxHeight: (panelMode === "suggestions" || panelMode === "fixlist-all") ? "min(70vh, 560px)" : undefined,
            overflowY: (panelMode === "suggestions" || panelMode === "fixlist-all") ? "auto" : undefined,
          }}
        >
          <div style={{
            fontSize: 10, color: "#ff9d5c",
            fontFamily: "var(--font-mono, ui-monospace, monospace)",
            letterSpacing: "0.08em", marginBottom: 8,
          }}>
            ORA GUIDE
          </div>

          {panelMode === "stage" && stageInfo && (
            <>
              <div data-testid="ora-guide-message"
                   style={{ fontSize: 13, color: "#f8fafc", lineHeight: 1.55, marginBottom: 12 }}>
                {stageInfo.message}
              </div>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                <button data-testid="ora-guide-got-it" type="button" onClick={dismiss} style={primaryBtnStyle}>
                  Got it
                </button>
                <button data-testid="ora-guide-something-wrong" type="button"
                        onClick={reportSomethingWrong} style={linkBtnStyle}>
                  Something's wrong
                </button>
              </div>
            </>
          )}

          {(panelMode === "external" || panelMode === "first_scan") && secondaryNudge && (
            <>
              <div data-testid="ora-guide-secondary-message"
                   style={{ fontSize: 13, color: "#f8fafc", lineHeight: 1.55, marginBottom: 12 }}>
                {secondaryNudge.message}
              </div>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                <button data-testid="ora-guide-secondary-action" type="button"
                        onClick={actSecondaryNudge} style={primaryBtnStyle}>
                  {secondaryNudge.actionLabel || "Try it"}
                </button>
                <button data-testid="ora-guide-secondary-dismiss" type="button"
                        onClick={dismiss} style={linkBtnStyle}>
                  Dismiss
                </button>
              </div>
            </>
          )}

          {panelMode === "general" && (
            <>
              <div style={{ fontSize: 13, color: "#f8fafc", lineHeight: 1.55, marginBottom: 12 }}>
                How can I help?
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <button data-testid="ora-guide-open-advisor" type="button" onClick={openAdvisor} style={menuBtnStyle}>
                  Open Advisor
                </button>
                <button data-testid="ora-guide-contact-support" type="button"
                        onClick={reportSomethingWrong} style={menuBtnStyle}>
                  Contact support
                </button>
                <button data-testid="ora-guide-close" type="button" onClick={dismiss} style={linkBtnStyle}>
                  Close
                </button>
              </div>
            </>
          )}

          {panelMode === "suggestions" && (
            <>
              <div data-testid="ora-guide-suggestions-intro"
                   style={{ fontSize: 12, color: "#f8fafc", lineHeight: 1.5, marginBottom: 10 }}>
                {projectSuggestions.some((s) => s.personalized)
                  ? "Here's what I found in your repo — pick one, or type your own:"
                  : "Not sure what to ask? Pick one, or describe what you want:"}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 10 }}>
                {projectSuggestions.map(({ slug, icon_hint, label, example, personalized }, idx) => {
                  const Icon = SUGGESTION_ICONS[icon_hint] || Sparkles;
                  // Backend finding slugs can contain spaces/punctuation —
                  // keep data-testid CSS-selector-safe regardless of source.
                  const safeSlug = String(slug || idx).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "") || `item-${idx}`;
                  return (
                    <button
                      key={`${slug}-${idx}`}
                      type="button"
                      data-testid={`ora-guide-suggestion-${safeSlug}`}
                      onClick={() => pickSuggestion(example)}
                      title={`Click to try: "${example}"`}
                      style={suggestionBtnStyle}
                    >
                      <Icon size={12} strokeWidth={2.2} style={{ flexShrink: 0, marginTop: 2, color: "#ffb37a" }} />
                      <span style={{ minWidth: 0, flex: 1 }}>
                        <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                          <span style={{ fontSize: 12, fontWeight: 600, color: "#f8fafc" }}>{label}</span>
                          {personalized && (
                            <span style={{ fontSize: 8, fontWeight: 700, letterSpacing: "0.04em",
                                           color: "#4ade80", background: "rgba(74,222,128,0.12)",
                                           borderRadius: 4, padding: "1px 4px" }}>
                              FROM YOUR REPO
                            </span>
                          )}
                        </span>
                        <span style={{ display: "block", fontSize: 11, color: "#9aa0aa", fontStyle: "italic",
                                       overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          &ldquo;{example}&rdquo;
                        </span>
                      </span>
                    </button>
                  );
                })}
              </div>
              <div data-testid="ora-guide-scan-intro"
                   style={{ fontSize: 11, color: "#9aa0aa", marginBottom: 6,
                            textTransform: "uppercase", letterSpacing: "0.06em", fontWeight: 600 }}>
                Or scan for real issues
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 10 }}>
                {SLASH_COMMANDS.map(({ id, label, description, icon: Icon, trigger }) => (
                  <button
                    key={id}
                    type="button"
                    data-testid={`ora-guide-scan-${id}`}
                    onClick={() => runScan(id)}
                    title={`Run ${trigger} now`}
                    style={suggestionBtnStyle}
                  >
                    <Icon size={12} strokeWidth={2.2} style={{ flexShrink: 0, marginTop: 2, color: "#ffb37a" }} />
                    <span style={{ minWidth: 0, flex: 1 }}>
                      <span style={{ fontSize: 12, fontWeight: 600, color: "#f8fafc" }}>{label}</span>
                      <span style={{ display: "block", fontSize: 11, color: "#9aa0aa", fontStyle: "italic",
                                     overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {description}
                      </span>
                    </span>
                  </button>
                ))}
              </div>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                <button data-testid="ora-guide-open-advisor" type="button" onClick={openAdvisor} style={linkBtnStyle}>
                  Open Advisor
                </button>
                <button data-testid="ora-guide-close" type="button" onClick={dismiss} style={linkBtnStyle}>
                  Close
                </button>
              </div>
            </>
          )}

          {panelMode === "fixlist" && (
            <>
              {fixListItems[fixListIndex] ? (
                <>
                  <div data-testid="ora-guide-fixlist-sev"
                       style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
                    <span style={{
                      width: 7, height: 7, borderRadius: 999, flexShrink: 0,
                      background: FIXLIST_SEV_DOT[fixListItems[fixListIndex].severity] || FIXLIST_SEV_DOT.medium,
                    }} />
                    <span style={{ fontSize: 10, color: "#9aa0aa", textTransform: "uppercase", letterSpacing: "0.06em" }}>
                      {fixListItems[fixListIndex].severity || "medium"} · {fixListIndex + 1} of {fixListItems.length}
                    </span>
                  </div>
                  <div data-testid="ora-guide-fixlist-title"
                       style={{ fontSize: 13, color: "#f8fafc", fontWeight: 600, lineHeight: 1.4, marginBottom: 4 }}>
                    {fixListItems[fixListIndex].title || fixListItems[fixListIndex].rule_id}
                  </div>
                  {fixListItems[fixListIndex].message && (
                    <div style={{ fontSize: 12, color: "#9aa0aa", lineHeight: 1.45, marginBottom: 6 }}>
                      {fixListItems[fixListIndex].message}
                    </div>
                  )}
                  {fixListItems[fixListIndex].file && (
                    <div data-testid="ora-guide-fixlist-file"
                         style={{ fontSize: 11, color: "#64748b", fontFamily: "'JetBrains Mono', monospace",
                                  marginBottom: 12, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {fixListItems[fixListIndex].file}{fixListItems[fixListIndex].line ? `:${fixListItems[fixListIndex].line}` : ""}
                    </div>
                  )}
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <button data-testid="ora-guide-fixlist-fix" type="button" onClick={fixCurrentItem} style={primaryBtnStyle}>
                      Fix
                    </button>
                    {fixListItems.length > 1 && (
                      <button data-testid="ora-guide-fixlist-next" type="button" onClick={nextFixItem} style={menuBtnStyle}>
                        Next
                      </button>
                    )}
                    <button data-testid="ora-guide-fixlist-seeall" type="button" onClick={openFixListAll} style={linkBtnStyle}>
                      See all ({fixListTotal})
                    </button>
                  </div>
                </>
              ) : (
                <div style={{ fontSize: 13, color: "#f8fafc" }}>Nothing open right now — you're all caught up.</div>
              )}
            </>
          )}

          {panelMode === "fixlist-all" && (
            <>
              <div data-testid="ora-guide-fixlist-all-intro"
                   style={{ fontSize: 12, color: "#9aa0aa", marginBottom: 8 }}>
                {fixListTotal} open issue{fixListTotal === 1 ? "" : "s"}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 10 }}>
                {fixListItems.map((it) => (
                  <div key={it.finding_id} data-testid={`ora-guide-fixlist-all-item-${it.finding_id}`}
                       style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 0",
                                borderTop: "1px solid rgba(148,163,184,0.10)" }}>
                    <span style={{ width: 7, height: 7, borderRadius: 999, flexShrink: 0,
                                   background: FIXLIST_SEV_DOT[it.severity] || FIXLIST_SEV_DOT.medium }} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 12, color: "#e2e8f0", fontWeight: 500 }}>{it.title || it.rule_id}</div>
                      {it.file && (
                        <div style={{ fontSize: 10, color: "#64748b", fontFamily: "'JetBrains Mono', monospace",
                                      overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {it.file}{it.line ? `:${it.line}` : ""}
                        </div>
                      )}
                    </div>
                    <button data-testid={`ora-guide-fixlist-all-fix-${it.finding_id}`} type="button"
                            onClick={() => fixItem(it)} style={{ ...menuBtnStyle, padding: "4px 10px", fontSize: 11 }}>
                      Fix
                    </button>
                  </div>
                ))}
                {fixListTotal > fixListItems.length && (
                  <div style={{ fontSize: 11, color: "#64748b", marginTop: 4 }}>
                    + {fixListTotal - fixListItems.length} more — open Codebase Health to see everything.
                  </div>
                )}
              </div>
              <button data-testid="ora-guide-close" type="button" onClick={dismiss} style={linkBtnStyle}>
                Close
              </button>
            </>
          )}

          {panelMode === "escalating" && (
            <div style={{ fontSize: 12, color: "var(--text-faint, #999)" }}>Sending…</div>
          )}

          {panelMode === "escalated" && (
            <div data-testid="ora-guide-escalated-confirm" style={{ fontSize: 13, color: "#22c55e", fontWeight: 600 }}>
              ✓ Thanks, we'll take a look.
            </div>
          )}
        </div>
      )}

      <button
        data-testid="ora-guide-mascot-avatar"
        type="button"
        aria-label="ORA Guide"
        onClick={toggleOpen}
        style={{
          width: 38, height: 38, borderRadius: "50%",
          background: "linear-gradient(135deg, #ff6608, #ff9d5c)",
          border: "none", cursor: "pointer", position: "relative",
          boxShadow: (autoGroup || secondaryNudge) && !open
            ? "0 4px 20px rgba(255,102,8,0.5), 0 0 0 3px rgba(255,102,8,0.25)"
            : "0 4px 16px rgba(0,0,0,0.4)",
          animation: "oraGuideFloat 3s ease-in-out infinite",
        }}
      >
        <span style={{ position: "absolute", top: 12, left: 9, width: 6, height: 6,
                       background: "#0b0b0b", borderRadius: "50%", animation: "oraGuideBlink 4.5s infinite" }} />
        <span style={{ position: "absolute", top: 12, right: 9, width: 6, height: 6,
                       background: "#0b0b0b", borderRadius: "50%", animation: "oraGuideBlink 4.5s infinite 0.08s" }} />
        <span style={{ position: "absolute", bottom: 10, left: "50%", transform: "translateX(-50%)",
                       width: 12, height: 3, background: "#0b0b0b", borderRadius: 2 }} />
        {fixListTotal > 0 && (
          <span
            data-testid="ora-guide-fixlist-badge"
            aria-label={`${fixListTotal} open issue${fixListTotal === 1 ? "" : "s"}`}
            style={{
              position: "absolute", top: -4, right: -4,
              minWidth: 17, height: 17, padding: "0 4px", borderRadius: 999,
              background: "#ef4444", color: "#fff",
              fontSize: 10, fontWeight: 700, lineHeight: "17px", textAlign: "center",
              border: "2px solid #0b0b0b", fontFamily: "var(--font-mono, ui-monospace, monospace)",
            }}
          >
            {fixListTotal > 99 ? "99+" : fixListTotal}
          </span>
        )}
      </button>
    </div>
  );
}

const primaryBtnStyle = {
  background: "var(--accent, #ff6608)", color: "#0b0b0b",
  border: "none", borderRadius: 6, padding: "6px 14px",
  fontSize: 12, fontWeight: 600, cursor: "pointer",
};
const menuBtnStyle = {
  background: "rgba(255,102,8,0.08)", color: "#f8fafc",
  border: "1px solid rgba(255,102,8,0.22)", borderRadius: 6,
  padding: "8px 12px", fontSize: 12, fontWeight: 500,
  cursor: "pointer", textAlign: "left",
};
const linkBtnStyle = {
  background: "transparent", color: "var(--text-faint, #999)",
  border: "none", fontSize: 11, cursor: "pointer", padding: 4,
};
const suggestionBtnStyle = {
  display: "flex", gap: 8, alignItems: "flex-start", textAlign: "left",
  padding: "8px 10px", borderRadius: 8, width: "100%",
  background: "rgba(255,102,8,0.08)", border: "1px solid rgba(255,102,8,0.22)",
  cursor: "pointer",
};

const MASCOT_KEYFRAMES = `
@keyframes oraGuideFloat {
  0%, 100% { transform: translateY(0); }
  50%      { transform: translateY(-3px); }
}
@keyframes oraGuideBlink {
  0%, 90%, 100% { transform: scaleY(1); }
  95%           { transform: scaleY(0.1); }
}
@keyframes oraGuideBubbleIn {
  from { opacity: 0; transform: translateY(6px); }
  to   { opacity: 1; transform: translateY(0); }
}
`;
