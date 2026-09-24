/**
 * VisibilityKitPanel.jsx — the AI-Visibility Kit visual panel (spec §6).
 *
 * Backend already existed (services/visibility/*, routers/visibility.py,
 * migrations/003_visibility_kit.py) — this file is the missing visual
 * layer the spec called for 20+ rounds ago. Reuses the existing
 * GET /visibility/projects/{id}/state + POST .../apply endpoints as-is.
 *
 * Apply is R9-gated server-side (`kit_apply_enabled`, default OFF) — the
 * backend returns `apply_enabled`/`apply_disabled_reason` on every state
 * fetch, and this panel disables both the main CTA and every per-row
 * Apply button with that exact reason as a tooltip. No client-side-only
 * gate — even if someone bypassed the UI, the server still blocks it.
 */
import React, { useCallback, useEffect, useState } from "react";
import { X, Sparkles, ShieldCheck, Loader2 } from "lucide-react";
import { api } from "../lib/api";
import { toast } from "../lib/toastApi";

const CARD_BG = "#161616";
const CARD_BORDER = "1px solid #222";
const TEXT = "#F5F5F5";
const MUTED = "#9A9A93";

function chipForItem(item) {
  if (item.mode === "advisory") return "Needs you";
  if (item.status === "pr_merged" || item.status === "live") return "Done";
  if (item.status === "pr_created") return "In review";
  if (item.status === "missing") return "Ready";
  return "Missing";
}

const CHIP_STYLES = {
  Missing:    { bg: "rgba(154,154,147,0.12)", fg: MUTED },
  Ready:      { bg: "rgba(34,197,94,0.12)",   fg: "#4ADE80" },
  "In review": { bg: "rgba(234,179,8,0.14)",  fg: "#FACC15" },
  Done:       { bg: "rgba(34,197,94,0.22)",   fg: "#22C55E" },
  "Needs you": { bg: "rgba(249,115,22,0.14)", fg: "#FB923C" },
};

function StatusChip({ label }) {
  const s = CHIP_STYLES[label] || CHIP_STYLES.Missing;
  return (
    <span data-testid={`kit-chip-${label.toLowerCase().replace(/\s+/g, "-")}`}
      style={{
        background: s.bg, color: s.fg, fontFamily: "Jost, sans-serif",
        fontSize: 11, fontWeight: 600, padding: "3px 10px", borderRadius: 999,
        letterSpacing: "0.02em", whiteSpace: "nowrap",
      }}>
      {label}
    </span>
  );
}

function ScoreDonut({ score }) {
  const r = 46, c = 2 * Math.PI * r;
  const offset = c * (1 - Math.min(100, Math.max(0, score)) / 100);
  const color = score >= 70 ? "#4ADE80" : score >= 35 ? "#FACC15" : "#FB923C";
  return (
    <svg width="120" height="120" viewBox="0 0 120 120" data-testid="kit-readiness-donut">
      <circle cx="60" cy="60" r={r} fill="none" stroke="#2A2A2A" strokeWidth="10" />
      <circle cx="60" cy="60" r={r} fill="none" stroke={color} strokeWidth="10"
        strokeDasharray={c} strokeDashoffset={offset} strokeLinecap="round"
        transform="rotate(-90 60 60)" style={{ transition: "stroke-dashoffset 0.6s ease" }} />
      <text x="60" y="56" textAnchor="middle" fontSize="26" fontWeight="700" fill={TEXT}
        fontFamily="Jost, sans-serif">{score}</text>
      <text x="60" y="76" textAnchor="middle" fontSize="11" fill={MUTED}
        fontFamily="Jost, sans-serif">/100</text>
    </svg>
  );
}

function ApplyConfirmModal({ item, projectId, onClose, onApplied }) {
  const [busy, setBusy] = useState(false);
  const filesByItem = {
    preferred_sources: ["index.html (Preferred Sources badge)"],
    ai_crawler_policy: ["robots.txt (AI crawler policy block)"],
    structured_data: ["index.html (JSON-LD + meta/OG block)"],
    llms_txt: ["llms.txt", "llms-full.txt"],
    sitemap_auto: ["sitemap.xml"],
  };
  // 2026-09-20 — this preview text used to show a made-up branch name
  // (`auremcto/visibility-kit-{today's date}`) that never matched
  // what the backend actually creates — services/visibility/apply.py
  // calls the CENTRALIZED `ship_branch_name(f"visibility-kit-
  // {project_id}")` (services/loop_safety.py), which always resolves
  // to `auremcto/ship-visibility-kit-{project_id}`. Harmless-looking,
  // but it's exactly the kind of "does the UI even reflect the real
  // backend" mismatch that erodes trust once someone checks GitHub
  // and the branch name doesn't match what this modal told them.
  const branch = `auremcto/ship-visibility-kit-${projectId}`;
  const files = item ? filesByItem[item.key] || [] : Object.values(filesByItem).flat();

  const confirm = useCallback(async () => {
    setBusy(true);
    try {
      const items = item ? [item.key] : Object.keys(filesByItem);
      const { data } = await api.post(`/visibility/projects/${projectId}/apply`, { items });
      // 2026-09-12 — live-production bug (TJSNDHU/Aurem): backend
      // returns HTTP 200 even when nothing was applied (e.g. a
      // conflict — robots.txt exists without our managed-block
      // marker). Axios never throws for a 200, so this was silently
      // treated as success before. Must check `ok` explicitly.
      if (!data?.ok) {
        const reason = (data?.conflicts && data.conflicts.join("; ")) || data?.error || "Nothing was applied.";
        toast.error(reason);
        return;
      }
      toast.success(`PR opened: ${data.pr_url || "check GitHub"}`);
      onApplied?.();
    } catch (e) {
      const msg = e?.response?.data?.detail?.message || e?.response?.data?.detail?.error || "Could not apply.";
      toast.error(msg);
    } finally {
      setBusy(false);
      onClose();
    }
  }, [item, projectId, onApplied, onClose]);

  return (
    <div data-testid="kit-apply-confirm-modal" style={{
      position: "fixed", inset: 0, zIndex: 10001, display: "flex",
      alignItems: "center", justifyContent: "center",
      background: "rgba(0,0,0,0.78)", backdropFilter: "blur(6px)",
    }} onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} style={{
        background: CARD_BG, border: CARD_BORDER, borderRadius: 14, padding: 28,
        width: 460, fontFamily: "Jost, sans-serif", color: TEXT,
      }}>
        <h3 style={{ fontSize: 17, fontWeight: 600, marginBottom: 12 }}>
          This will create a PR on your GitHub repo
        </h3>
        <div style={{ fontSize: 13, color: MUTED, marginBottom: 6 }}>Branch</div>
        <code style={{ fontSize: 12, color: TEXT }}>{branch}</code>
        <div style={{ fontSize: 13, color: MUTED, margin: "14px 0 6px" }}>Changes</div>
        <ul style={{ fontSize: 13, paddingLeft: 18, margin: 0 }}>
          {files.map((f) => <li key={f} style={{ marginBottom: 4 }}>{f}</li>)}
        </ul>
        <div style={{ fontSize: 12, color: MUTED, marginTop: 14 }}>
          Revert: delete the branch (or use "Revert" after merge).
        </div>
        <div style={{ display: "flex", gap: 10, marginTop: 22, justifyContent: "flex-end" }}>
          <button data-testid="kit-apply-confirm-cancel" onClick={onClose}
            style={{ background: "transparent", border: "1px solid #333", color: TEXT,
              borderRadius: 8, padding: "8px 16px", fontSize: 13, cursor: "pointer" }}>
            Cancel
          </button>
          <button data-testid="kit-apply-confirm-submit" onClick={confirm} disabled={busy}
            style={{ background: "#4ADE80", color: "#0A0A0A", border: "none",
              borderRadius: 8, padding: "8px 16px", fontSize: 13, fontWeight: 600,
              cursor: busy ? "default" : "pointer", opacity: busy ? 0.7 : 1,
              display: "flex", alignItems: "center", gap: 6 }}>
            {busy && <Loader2 size={14} className="animate-spin" />}
            Confirm
          </button>
        </div>
      </div>
    </div>
  );
}

function FeatureRow({ item, applyEnabled, disabledReason, onApplyClick, onViewReport }) {
  const chip = chipForItem(item);
  const isAdvisory = item.mode === "advisory";
  const canApply = !isAdvisory && chip === "Ready";
  return (
    <div data-testid={`kit-row-${item.key}`} style={{
      display: "flex", alignItems: "flex-start", justifyContent: "space-between",
      padding: "16px 4px", borderBottom: "1px solid #1E1E1E", gap: 16,
    }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
          <span style={{ fontFamily: "Jost, sans-serif", fontSize: 14, fontWeight: 600, color: TEXT }}>
            {item.name}
          </span>
          <StatusChip label={chip} />
        </div>
        <p style={{ fontSize: 13, color: MUTED, lineHeight: 1.5, margin: 0, maxWidth: 560 }}>
          {item.what_why}
        </p>
      </div>
      {isAdvisory ? (
        <button data-testid={`kit-view-report-${item.key}`} onClick={() => onViewReport(item)}
          style={{ background: "transparent", border: "1px solid #333", color: TEXT,
            borderRadius: 8, padding: "7px 14px", fontSize: 12.5, cursor: "pointer",
            whiteSpace: "nowrap", flexShrink: 0 }}>
          View report
        </button>
      ) : (
        <button data-testid={`kit-apply-row-${item.key}`}
          onClick={() => canApply && applyEnabled && onApplyClick(item)}
          disabled={!canApply || !applyEnabled}
          title={!applyEnabled ? disabledReason : (!canApply ? `Status: ${chip}` : "")}
          style={{
            background: canApply && applyEnabled ? "#4ADE80" : "transparent",
            color: canApply && applyEnabled ? "#0A0A0A" : MUTED,
            border: canApply && applyEnabled ? "none" : "1px solid #333",
            borderRadius: 8, padding: "7px 14px", fontSize: 12.5, fontWeight: 600,
            cursor: canApply && applyEnabled ? "pointer" : "not-allowed",
            whiteSpace: "nowrap", flexShrink: 0, opacity: canApply ? 1 : 0.6,
          }}>
          Apply
        </button>
      )}
    </div>
  );
}

export default function VisibilityKitPanel({ projectId, siteDomain, onClose }) {
  const [state, setState] = useState(null);
  const [loading, setLoading] = useState(true);
  const [confirmItem, setConfirmItem] = useState(null); // item object, or "ALL"
  const [reportItem, setReportItem] = useState(null);
  // 2026-09-20 — real bug repro (TJSNDHU/Aurem live testing): a
  // project whose GitHub App auth is actually revoked/unreachable
  // still showed every "auto" item as a green "Ready" chip with an
  // enabled "Apply" CTA, because `apply_enabled` from GET .../state
  // only ever reflected the R9 kill-switch — never this specific
  // project's own connection health. Clicking Apply then hit a
  // 403 from get_repo_token_or_error() (services/pat_vault.py) that
  // surfaced as a toast easy to miss, with the panel's own score/
  // chips staying at 0/100 with no explanation — reads exactly like
  // "the Kit is broken" when it's actually a disconnected repo.
  // Reuses the SAME `GET /cto/projects/connection-status` endpoint
  // ProjectSwitcher already polls (services/repo_status_cache.py) —
  // no new backend endpoint. Only "disconnected" (a real, confirmed
  // revocation) blocks Apply here — "unreachable" (a transient
  // network blip) does NOT, matching ProjectSwitcher's own hard-won
  // fix for over-reacting to transient status (see that file's H1
  // comment) — this panel would otherwise nag on every brief GitHub
  // hiccup even though a retry would likely succeed.
  const [githubDisconnected, setGithubDisconnected] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api.get("/cto/projects/connection-status")
      .then((r) => {
        if (cancelled) return;
        const entry = (r.data?.statuses || []).find((s) => s.project_id === projectId);
        setGithubDisconnected(entry?.status === "disconnected");
      })
      .catch(() => { /* keep assuming connected on a transient blip fetching status itself */ });
    return () => { cancelled = true; };
  }, [projectId]);

  useEffect(() => {
    const onKeyDown = (e) => {
      if (e.key !== "Escape") return;
      if (confirmItem) { setConfirmItem(null); return; }
      if (reportItem) { setReportItem(null); return; }
      onClose?.();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [confirmItem, reportItem, onClose]);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get(`/visibility/projects/${projectId}/state`);
      setState(data);
    } catch {
      toast.error("Could not load the Visibility Kit.");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => { load(); }, [load]);

  if (loading || !state) {
    return (
      <div data-testid="visibility-kit-panel" style={overlayStyle} onClick={onClose}>
        <div onClick={(e) => e.stopPropagation()} style={{ ...cardStyle, textAlign: "center" }}>
          <Loader2 className="animate-spin" size={22} style={{ color: TEXT, margin: "0 auto" }} />
        </div>
      </div>
    );
  }

  const readyCount = state.items.filter((i) => i.mode === "auto" && chipForItem(i) === "Ready").length;
  const badgeItem = state.items.find((i) => i.key === "preferred_sources");
  // Combine BOTH gates: the R9 server kill-switch, and this specific
  // project's own GitHub connection health. Either one blocking is
  // enough to disable Apply — the connection message takes priority
  // since it's the more actionable one for the user right now (the
  // R9 gate is a platform-wide state they can't do anything about).
  const applyEnabled = state.apply_enabled && !githubDisconnected;
  const applyDisabledReason = githubDisconnected
    ? "This project looks disconnected from GitHub — reconnect it in Settings before applying."
    : state.apply_disabled_reason;
  const badgePreview =
    `<script async src="https://news.google.com/swg/js/v1/publisher.js"></script>\n` +
    `<div google-add-preferred-source-btn data-theme="light" data-lang="en"></div>\n` +
    `<a href="https://www.google.com/preferences/source?q=${siteDomain || "your-domain.com"}" ` +
    `target="_blank" rel="noopener">Prefer ${siteDomain || "your site"} in AI answers ↗</a>`;

  return (
    <div data-testid="visibility-kit-panel" style={overlayStyle} onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} style={{ ...cardStyle, width: 720, maxHeight: "88vh", overflowY: "auto" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 20 }}>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <Sparkles size={18} style={{ color: "#4ADE80" }} />
              <h2 style={{ fontFamily: "Jost, sans-serif", fontSize: 19, fontWeight: 600, color: TEXT, margin: 0 }}>
                Visibility Kit
              </h2>
            </div>
            <p data-testid="kit-positioning-line" style={{
              fontFamily: "Jost, sans-serif", fontSize: 12, color: MUTED, margin: "6px 0 0",
            }}>
              Others measure your AI visibility. AUREM fixes it — and we ship the fix.
            </p>
          </div>
          <button data-testid="kit-panel-close" onClick={onClose}
            style={{ background: "transparent", border: "none", color: MUTED, cursor: "pointer" }}>
            <X size={20} />
          </button>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 24, marginBottom: 24 }}>
          <ScoreDonut score={state.score} />
          <div style={{ fontFamily: "Jost, sans-serif" }}>
            <p data-testid="kit-readiness-summary" style={{ color: TEXT, fontSize: 15, margin: "0 0 6px" }}>
              Your site is <strong>{state.score}/100</strong> AI-ready.
            </p>
            <p style={{ color: MUTED, fontSize: 13, margin: 0 }}>
              {readyCount > 0
                ? `${readyCount} fix${readyCount > 1 ? "es" : ""} ready as one PR.`
                : "Everything auto-applicable is already Done or In review."}
            </p>
            <p data-testid="kit-score-note" style={{ color: MUTED, fontSize: 11.5, margin: "6px 0 0" }}>
              This is a preparedness checklist, not live citation tracking.
            </p>
            {state.pricing_note && (
              <p data-testid="kit-pricing-note" style={{ color: "#4ADE80", fontSize: 11.5, margin: "4px 0 0" }}>
                {state.pricing_note}
              </p>
            )}
            {!applyEnabled && (
              <div data-testid="kit-apply-disabled-banner" style={{
                marginTop: 10, fontSize: 12, color: "#FB923C",
                display: "flex", alignItems: "center", gap: 6,
              }}>
                <ShieldCheck size={13} />
                {applyDisabledReason}
              </div>
            )}
          </div>
        </div>

        <button data-testid="kit-apply-all-cta"
          onClick={() => applyEnabled && readyCount > 0 && setConfirmItem("ALL")}
          disabled={!applyEnabled || readyCount === 0}
          title={!applyEnabled ? applyDisabledReason : ""}
          style={{
            width: "100%", background: applyEnabled && readyCount > 0 ? "#4ADE80" : "transparent",
            color: applyEnabled && readyCount > 0 ? "#0A0A0A" : MUTED,
            border: applyEnabled && readyCount > 0 ? "none" : "1px solid #333",
            borderRadius: 10, padding: "12px 0", fontSize: 14, fontWeight: 600,
            cursor: applyEnabled && readyCount > 0 ? "pointer" : "not-allowed",
            marginBottom: 8, opacity: applyEnabled ? 1 : 0.7,
          }}>
          Apply Visibility Kit{readyCount > 0 ? ` (${readyCount})` : ""}
        </button>

        <div style={{ marginTop: 8 }}>
          {state.items.map((item) => (
            <FeatureRow key={item.key} item={item}
              applyEnabled={applyEnabled} disabledReason={applyDisabledReason}
              onApplyClick={setConfirmItem} onViewReport={setReportItem} />
          ))}
        </div>

        {badgeItem && (
          <div data-testid="kit-badge-preview" style={{ marginTop: 22, padding: 14,
            background: "#0F0F0F", border: "1px solid #222", borderRadius: 10 }}>
            <div style={{ fontSize: 12.5, color: MUTED, marginBottom: 8, fontFamily: "Jost, sans-serif" }}>
              What your site will get (Preferred Sources badge):
            </div>
            <pre style={{ fontSize: 11.5, color: "#9ecbff", margin: 0, whiteSpace: "pre-wrap",
              fontFamily: "monospace", lineHeight: 1.6 }}>{badgePreview}</pre>
          </div>
        )}
      </div>

      {(confirmItem) && (
        <ApplyConfirmModal
          item={confirmItem === "ALL" ? null : confirmItem}
          projectId={projectId}
          onClose={() => setConfirmItem(null)}
          onApplied={load}
        />
      )}
      {reportItem && (
        <div data-testid="kit-report-modal" style={overlayStyle} onClick={() => setReportItem(null)}>
          <div onClick={(e) => e.stopPropagation()} style={{ ...cardStyle, width: 480 }}>
            <h3 style={{ fontFamily: "Jost, sans-serif", fontSize: 16, color: TEXT, marginTop: 0 }}>
              {reportItem.name} — advisory report
            </h3>
            <p style={{ fontSize: 13, color: MUTED, lineHeight: 1.5 }}>{reportItem.what_why}</p>
            <p style={{ fontSize: 12.5, color: MUTED }}>
              This item is advisory-only — AUREM lists the gaps, you decide what to change.
              A detailed per-page report lands here once a scan has run.
            </p>
            <button data-testid="kit-report-close" onClick={() => setReportItem(null)}
              style={{ background: "transparent", border: "1px solid #333", color: TEXT,
                borderRadius: 8, padding: "7px 14px", fontSize: 12.5, cursor: "pointer" }}>
              Close
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

const overlayStyle = {
  position: "fixed", inset: 0, zIndex: 10000, display: "flex",
  alignItems: "center", justifyContent: "center",
  background: "rgba(0,0,0,0.78)", backdropFilter: "blur(6px)",
};

const cardStyle = {
  background: "#161616", border: "1px solid #222", borderRadius: 16,
  padding: 28, color: "#F5F5F5",
};
