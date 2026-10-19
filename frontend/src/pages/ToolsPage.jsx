/**
 * pages/ToolsPage.jsx — Iter 212m-158, UI-fix C1 (2026-10)
 *
 * "Developer tools" hub. Bug Hunt and Health Scan are real, working
 * pages — link straight to them. Vanguard Scan and Security Scan have
 * no standalone page; they run automatically inside ORA chat and CI,
 * so this just says that plainly instead of showing a disabled
 * "Coming soon" button for tools that already exist elsewhere.
 */
import React from "react";
import { Bug, ShieldCheck, Lock, Activity } from "lucide-react";

// ─── Tool definitions ──────────────────────────────────────────────
// "live": already a real, working page — link straight to it.
// "embedded": runs inside ORA chat / CI, no standalone page to link to.
const TOOLS = [
  { id: "bug-hunt",      Icon: Bug,         name: "Bug Hunt",
    description: "AI finds bugs in your code before users do.",
    accentClass: "coral",  kind: "live",     route: "/bug-hunt" },
  { id: "vanguard",      Icon: ShieldCheck, name: "Vanguard Scan",
    description: "Full security audit of your repo in minutes.",
    accentClass: "purple", kind: "embedded" },
  { id: "security-scan", Icon: Lock,        name: "Security Scan",
    description: "CVE detection and dependency vulnerability analysis.",
    accentClass: "amber",  kind: "embedded" },
  { id: "health-scan",   Icon: Activity,    name: "Health Scan",
    description: "Codebase quality score with actionable fixes.",
    accentClass: "teal",   kind: "live",     route: "/codebase-health" },
];

// ─── Accent color map → CSS variable equivalents ───────────────────
const ACCENT = {
  coral:  { bg: "#FAECE7", border: "#993C1D", text: "#993C1D", icon: "#D85A30" },
  purple: { bg: "#EEEDFE", border: "#534AB7", text: "#534AB7", icon: "#7F77DD" },
  amber:  { bg: "#FAEEDA", border: "#854F0B", text: "#854F0B", icon: "#BA7517" },
  teal:   { bg: "#E1F5EE", border: "#0F6E56", text: "#0F6E56", icon: "#1D9E75" },
};

// ─── Single tool card ──────────────────────────────────────────────
function ToolCard({ tool }) {
  const ac = ACCENT[tool.accentClass];
  const Icon = tool.Icon;
  const isLive = tool.kind === "live";

  return (
    <div
      data-testid={`tools-card-${tool.id}`}
      style={{
        background: "var(--surface-2)",
        border: "0.5px solid var(--border)",
        borderRadius: 12,
        padding: "1.25rem",
        display: "flex",
        flexDirection: "column",
        gap: 12,
      }}
    >
      {/* Header */}
      <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
        <div
          style={{
            width: 40, height: 40, borderRadius: "var(--radius, 8px)",
            background: ac.bg,
            display: "flex", alignItems: "center", justifyContent: "center",
            flexShrink: 0,
          }}
        >
          <Icon size={20} color={ac.icon} aria-hidden="true" />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span style={{
              fontSize: 15, fontWeight: 500,
              color: "var(--text-primary, var(--ink))",
            }}>{tool.name}</span>
            <span
              data-testid={`tools-card-${tool.id}-eta`}
              style={{
                fontSize: 11, fontWeight: 500,
                padding: "2px 8px", borderRadius: 20,
                background: ac.bg, color: ac.text,
                border: `0.5px solid ${ac.border}`,
                whiteSpace: "nowrap",
              }}
            >{isLive ? "Live" : "Runs inside ORA chat and CI"}</span>
          </div>
          <p style={{
            margin: "2px 0 0", fontSize: 13,
            color: "var(--text-secondary, var(--text-dim))",
            lineHeight: 1.5,
          }}>{tool.description}</p>
        </div>
      </div>

      {isLive ? (
        <a
          href={tool.route}
          data-testid={`tools-card-${tool.id}-cta`}
          style={{
            width: "100%", padding: "8px 0",
            borderRadius: "var(--radius, 8px)",
            border: `0.5px solid ${ac.border}`,
            background: ac.bg, color: ac.text,
            fontSize: 14, fontWeight: 500,
            textDecoration: "none", cursor: "pointer",
            display: "inline-flex", alignItems: "center",
            justifyContent: "center", gap: 6,
            boxSizing: "border-box",
          }}
        >
          <Icon size={14} aria-hidden="true" />
          Open {tool.name}
        </a>
      ) : null}
    </div>
  );
}

// ─── Page ───────────────────────────────────────────────────────────
export default function ToolsPage() {
  return (
    <div
      data-testid="tools-page"
      style={{ maxWidth: 720, margin: "0 auto", padding: "2rem 1.5rem" }}
    >
      {/* Page header */}
      <div style={{ marginBottom: "2rem" }}>
        <h1 style={{
          fontSize: 22, fontWeight: 500, margin: 0,
          color: "var(--text-primary, var(--ink))",
        }}>Developer tools</h1>
        <p style={{
          fontSize: 15, lineHeight: 1.6, margin: "6px 0 0",
          color: "var(--text-secondary, var(--text-dim))",
        }}>
          Automated scans and analysis for your repositories.
        </p>
      </div>

      {/* Tool cards grid */}
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
        gap: 16,
      }}>
        {TOOLS.map((tool) => (
          <ToolCard key={tool.id} tool={tool} />
        ))}
      </div>

      {/* Footer note */}
      <p style={{
        marginTop: "2rem", fontSize: 13, textAlign: "center", lineHeight: 1.6,
        color: "var(--text-muted, var(--text-faint))",
      }}>
        Bug Hunt and Health Scan are live. Vanguard and Security Scan run automatically inside ORA chat and CI.{" "}
        <a
          href="/dashboard"
          data-testid="tools-back-to-ora"
          style={{ color: "var(--text-accent, var(--accent-2))", textDecoration: "none" }}
        >Back to ORA</a>
      </p>
    </div>
  );
}
