// MobileMenuSheet.jsx — 2026-11-25 · Mobile nav fix (Teji's phone screenshot).
//
// The bug: on a phone the 56px DESKTOP icon rail (RailShell — logo, Chat,
// Ship, Insights, Settings, Admin, AUTO, logout) showed AND the bottom tab
// bar (Chat / Projects / Ship / Menu) showed — Chat and Ship twice, the
// layout squeezed, "Previous Sessions" cut off at the right edge.
//
// The fix: Dashboard renders RailShell only when !isMobile now, and
// everything that lived ONLY on the rail moves into this bottom sheet:
//   • "projects" sheet — the repo switcher (same repos RailShell's Chat
//     flyout showed) + Add repository + Manage repositories.
//   • "menu" sheet — Insights + Settings + Security + New run + AUTO
//     toggle + Log out.
// Chat and Ship are deliberately NOT duplicated here — they live on the
// bottom tab bar. Renders in a React portal to document.body (same
// pattern as marketing/MobileNavSheet), so `position: fixed` resolves
// against the viewport.
import React, { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import {
  BarChart3,
  FolderGit2,
  Gift,
  Lock,
  LogOut,
  Play,
  Plug,
  Plus,
  Receipt,
  ShieldCheck,
  User as UserIcon,
  Users as UsersIcon,
  X,
} from "lucide-react";
import { api, getToken, logout as apiLogout, fetchMyAdminAccess } from "../../lib/api";
import { setPageFlags, isAnalyticsPageEnabled } from "../../lib/pageFlags";

const BG = "rgb(7,8,13)";
const ACCENT = "#ff8a2a";
const BORDER = "rgba(255,200,120,.1)";
const ITEM_FONT = "'Inter', 'Jost', sans-serif";
// Same key RailShell uses for the rail auto-hide preference, so the
// toggle here and the desktop rail stay in sync (one is mounted at a
// time: rail on desktop, this sheet on mobile).
const AUTO_HIDE_KEY = "aurem_rail_autohide";

const rowStyle = {
  display: "flex", alignItems: "center", gap: 10,
  width: "100%", padding: "12px 14px", borderRadius: 10, border: "none",
  background: "transparent", color: "rgba(226,232,240,0.85)",
  fontFamily: ITEM_FONT, fontSize: 14, fontWeight: 500,
  cursor: "pointer", textAlign: "left", textDecoration: "none",
  minHeight: 48,
};

const sectionHeaderStyle = {
  fontFamily: ITEM_FONT, fontSize: 10, fontWeight: 700,
  letterSpacing: "0.14em", textTransform: "uppercase",
  color: "rgba(148,163,184,0.7)", padding: "14px 14px 6px",
};

export default function MobileMenuSheet({
  sheet, // "projects" | "menu" | null
  open,
  onClose,
  repos = [],
  onSelectRepo,
  onAddRepo,
  onNewRun,
  onLogout,
}) {
  // Fail CLOSED until the server confirms (same pattern RailShell uses):
  // Team / Admin rows never render from a guess.
  const [canManageTeam, setCanManageTeam] = useState(false);
  const [showAdmin, setShowAdmin] = useState(false);
  const [autoHideEnabled, setAutoHideEnabled] = useState(() => {
    try {
      const v = localStorage.getItem(AUTO_HIDE_KEY);
      return v == null ? true : v === "1";
    } catch { return true; }
  });

  useEffect(() => {
    if (!open) return undefined;
    if (!getToken()) return undefined;
    let dead = false;
    // Mirror RailShell's /auth/me handling (founder check + page-flag
    // mirror) so the Analytics row gates exactly like the desktop rail.
    api.get("/auth/me")
      .then((r) => {
        if (dead) return;
        const me = r.data?.user || r.data || {};
        if (me.tier === "founder" || me.is_admin === true) setShowAdmin(true);
        try { setPageFlags(r.data); } catch { /* ignore */ }
      })
      .catch(() => {});
    api.get("/team/me")
      .then((r) => {
        if (dead) return;
        const perms = r.data?.permissions || {};
        const hasRole = r.data?.is_owner === true ||
          ["write", "read_write"].includes(perms.team_management);
        setCanManageTeam(hasRole && r.data?.team_feature_enabled === true);
      })
      .catch(() => {});
    fetchMyAdminAccess()
      .then((result) => {
        if (dead) return;
        if (result && (result.is_owner || Object.keys(result.screens || {}).length > 0)) {
          setShowAdmin(true);
        }
      })
      .catch(() => {});
    return () => { dead = true; };
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => { if (e.key === "Escape") onClose?.(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const toggleAutoHide = useCallback(() => {
    setAutoHideEnabled((v) => {
      const next = !v;
      try { localStorage.setItem(AUTO_HIDE_KEY, next ? "1" : "0"); } catch { /* ignore */ }
      return next;
    });
  }, []);

  const handleLogout = useCallback(() => {
    // Same sign-out as the rail: shared helper clears token + user
    // cache and redirects to /login; the Dashboard onLogout prop is
    // the fallback path if that helper ever throws.
    try { apiLogout(); } catch { /* ignore */ }
    try { onLogout?.(); } catch { /* ignore */ }
  }, [onLogout]);

  if (!open || !sheet) return null;

  const title = sheet === "projects" ? "Projects" : "Menu";

  const linkRow = (to, label, Icon, testid) => (
    <Link key={to} to={to} data-testid={testid} onClick={() => onClose?.()} style={rowStyle}>
      <Icon size={17} style={{ flexShrink: 0, opacity: 0.85 }} />
      {label}
    </Link>
  );

  return createPortal(
    <div data-testid="mobile-menu-sheet" data-sheet={sheet}>
      <div
        data-testid="mobile-menu-sheet-backdrop"
        onClick={() => onClose?.()}
        style={{
          position: "fixed", inset: 0, zIndex: 1400,
          background: "rgba(0,0,0,0.6)",
        }}
      />
      <div
        role="dialog" aria-label={title}
        style={{
          position: "fixed", left: 0, right: 0, bottom: 0, zIndex: 1401,
          maxHeight: "82dvh", overflowY: "auto",
          background: BG, borderTop: `1px solid ${BORDER}`,
          borderRadius: "16px 16px 0 0",
          padding: "8px 12px calc(12px + env(safe-area-inset-bottom, 0px))",
          boxShadow: "0 -12px 40px rgba(0,0,0,0.55)",
          animation: "mobileSheetUp 180ms ease-out",
        }}
      >
        <style>{`@keyframes mobileSheetUp { from { transform: translateY(24px); opacity: 0.6; } to { transform: translateY(0); opacity: 1; } }`}</style>
        <div style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "6px 2px 4px",
        }}>
          <span style={{
            fontFamily: "'Jost', sans-serif", fontSize: 16, fontWeight: 600,
            color: "#e2e8f0",
          }}>{title}</span>
          <button type="button" data-testid="mobile-menu-sheet-close"
            onClick={() => onClose?.()} aria-label={`Close ${title}`}
            style={{
              border: "none", background: "transparent",
              color: "rgba(148,163,184,0.8)", cursor: "pointer",
              width: 40, height: 40, display: "flex",
              alignItems: "center", justifyContent: "center",
            }}>
            <X size={18} />
          </button>
        </div>

        {sheet === "projects" && (
          <>
            <div style={sectionHeaderStyle}>Repositories</div>
            {repos.length === 0 && (
              <div style={{ fontFamily: ITEM_FONT, fontSize: 13, color: "rgba(148,163,184,0.6)", padding: "6px 14px" }}>
                No repositories connected yet.
              </div>
            )}
            {repos.map((r) => (
              <button key={r.id} type="button"
                data-testid={`mobile-sheet-repo-${r.id}`}
                onClick={() => { onSelectRepo?.(r); onClose?.(); }}
                style={{
                  ...rowStyle,
                  background: r.active ? "rgba(255,138,42,0.09)" : "transparent",
                  color: r.active ? ACCENT : "rgba(226,232,240,0.8)",
                }}>
                <span style={{
                  width: 7, height: 7, borderRadius: "50%", flexShrink: 0,
                  background: r.active ? ACCENT : "rgba(148,163,184,0.45)",
                }} />
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {r.owner ? `${r.owner}/` : ""}{r.name}
                </span>
              </button>
            ))}
            {onAddRepo && (
              <button type="button" data-testid="mobile-sheet-add-repo"
                onClick={() => { onAddRepo(); onClose?.(); }}
                style={rowStyle}>
                <Plus size={17} style={{ flexShrink: 0, opacity: 0.85 }} />
                Add repository
              </button>
            )}
            <Link to="/projects" data-testid="mobile-sheet-manage-repos"
              onClick={() => onClose?.()} style={rowStyle}>
              <FolderGit2 size={17} style={{ flexShrink: 0, opacity: 0.85 }} />
              Manage repositories
            </Link>
          </>
        )}

        {sheet === "menu" && (
          <>
            <div style={sectionHeaderStyle}>Insights</div>
            {isAnalyticsPageEnabled() && linkRow("/analytics", "Analytics", BarChart3, "mobile-sheet-link-analytics")}
            {linkRow("/wrapped", "Wrapped", Gift, "mobile-sheet-link-wrapped")}

            <div style={sectionHeaderStyle}>Settings</div>
            {linkRow("/settings?tab=profile", "Profile", UserIcon, "mobile-sheet-link-profile")}
            {linkRow("/settings?tab=plans", "Plans & usage", Receipt, "mobile-sheet-link-plans")}
            {linkRow("/settings?tab=integrations", "Integrations", Plug, "mobile-sheet-link-integrations")}
            {linkRow("/integrations", "IDE setup (MCP)", Plug, "mobile-sheet-link-mcp")}
            {canManageTeam && linkRow("/settings?tab=team", "Team", UsersIcon, "mobile-sheet-link-team")}

            <div style={sectionHeaderStyle}>Security</div>
            {linkRow("/settings?tab=vault", "Secrets vault", Lock, "mobile-sheet-link-vault")}
            {showAdmin && linkRow("/admin", "Admin", ShieldCheck, "mobile-sheet-link-admin")}

            <div style={sectionHeaderStyle}>Actions</div>
            {onNewRun && (
              <button type="button" data-testid="mobile-sheet-new-run"
                onClick={() => { onNewRun(); onClose?.(); }}
                style={rowStyle}>
                <Play size={17} style={{ flexShrink: 0, opacity: 0.85 }} />
                New run
              </button>
            )}
            <button type="button" data-testid="mobile-sheet-auto-toggle"
              aria-label={autoHideEnabled ? "Disable rail auto-hide" : "Enable rail auto-hide"}
              onClick={toggleAutoHide}
              style={{ ...rowStyle, justifyContent: "space-between" }}>
              <span style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span style={{
                  display: "inline-flex", alignItems: "center", justifyContent: "center",
                  width: 34, height: 22, borderRadius: 6,
                  border: `1px solid ${BORDER}`,
                  background: autoHideEnabled ? "rgba(255,138,42,0.14)" : "transparent",
                  color: autoHideEnabled ? ACCENT : "rgba(148,163,184,0.65)",
                  fontFamily: "'JetBrains Mono', monospace", fontSize: 9, fontWeight: 700,
                  letterSpacing: "0.08em",
                }}>
                  {autoHideEnabled ? "AUTO" : "OFF"}
                </span>
                Rail auto-hide
              </span>
              <span style={{ fontSize: 12, color: "rgba(148,163,184,0.6)" }}>
                {autoHideEnabled ? "On" : "Off"}
              </span>
            </button>
            <button type="button" data-testid="mobile-sheet-logout"
              onClick={handleLogout}
              style={{ ...rowStyle, color: "#f87171" }}>
              <LogOut size={17} style={{ flexShrink: 0, opacity: 0.85 }} />
              Log out
            </button>
          </>
        )}
      </div>
    </div>,
    document.body,
  );
}
