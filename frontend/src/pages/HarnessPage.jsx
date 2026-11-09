/**
 * pages/HarnessPage.jsx — Aurem Loop harness, Phase B, §4.5 structure.
 *
 * Reuses the site's real header (MarketingNav — same component every
 * other public marketing page uses) and the same inline-footer
 * convention AboutPage.jsx/ContactPage.jsx use. No new nav, no new
 * footer, no new look — same CSS vars/typography as those pages.
 *
 * Visibility: checked via the PUBLIC, unauthenticated
 * GET /api/harness/page-status (routers/harness.py) — NOT
 * pageFlags.js's isHarnessPageEnabled(), which only ever resolves
 * after a successful /auth/me call and so stays stuck at its
 * hardcoded `false` default for a signed-out visitor regardless of
 * the real flag value (the "pre-login flag gap" flagged in the Phase
 * B report). This endpoint reads the exact same `harness_page`
 * feature-flag doc /auth/me does — one flag, one source of truth,
 * checked the same way for everyone. Flag off -> "not available yet",
 * for every visitor, logged in or not.
 *
 * Copy: all approved text lives in src/data/harnessPage.mjs — the
 * SAME module scripts/seo-prerender.mjs imports, so the server-
 * rendered snapshot and this live page can never say different
 * things. Do not hardcode new copy here; edit that file.
 *
 * download button: signed out -> "Create a free account to download"
 * (a Link, no API call). Signed in -> "Download Aurem Loop", calls
 * the real POST /api/aurem-dev/harness/download and saves the blob.
 */
import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, healthApi, getToken } from "../lib/api";
import MarketingNav from "../components/marketing/MarketingNav";
import { HARNESS } from "../data/harnessPage.mjs";

function downloadBlobAsFile(blob, filename) {
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  window.URL.revokeObjectURL(url);
}

function filenameFromContentDisposition(headerValue, fallback) {
  if (!headerValue) return fallback;
  const match = /filename="?([^"]+)"?/.exec(headerValue);
  return match ? match[1] : fallback;
}

const pageStyle = {
  maxWidth: 820, margin: "0 auto", padding: "40px 24px 80px",
  color: "var(--text, #e8e3d3)",
  fontFamily: "Inter, system-ui, -apple-system, sans-serif",
  lineHeight: 1.65,
};
const h1Style = { fontSize: 32, fontWeight: 500, letterSpacing: "-0.02em", margin: "16px 0 12px" };
const h2Style = { fontSize: 20, fontWeight: 500, margin: "40px 0 12px" };
const pStyle = { fontSize: 14, color: "var(--text-dim, #a39d8a)", margin: "0 0 12px" };
const buttonStyle = {
  display: "inline-block", marginTop: 16, padding: "10px 20px",
  background: "var(--accent, #ff8a2a)", color: "#1a1208",
  border: "none", borderRadius: 8, fontSize: 14, fontWeight: 600,
  cursor: "pointer", textDecoration: "none",
};
const tableStyle = { width: "100%", borderCollapse: "collapse", fontSize: 13, marginTop: 8 };
const tdStyle = { padding: "8px 12px", borderBottom: "1px solid var(--border, rgba(255,200,120,0.16))" };
const footerStyle = {
  marginTop: 60, paddingTop: 20,
  borderTop: "1px solid var(--border, rgba(255,200,120,0.16))",
  fontSize: 11, color: "var(--text-faint)",
};

export default function HarnessPage() {
  const [flagEnabled, setFlagEnabled] = useState(null); // null = loading
  const [downloadState, setDownloadState] = useState("idle");
  const loggedIn = !!getToken();

  useEffect(() => {
    document.title = HARNESS.title;
    let active = true;
    healthApi.get("/harness/page-status")
      .then((r) => { if (active) setFlagEnabled(r.data?.harness_page_enabled === true); })
      .catch(() => { if (active) setFlagEnabled(false); });
    return () => { active = false; };
  }, []);

  async function handleDownload() {
    setDownloadState("downloading");
    try {
      const resp = await api.post("/harness/download", null, { responseType: "blob" });
      const filename = filenameFromContentDisposition(
        resp.headers?.["content-disposition"], "aurem-loop.zip",
      );
      downloadBlobAsFile(resp.data, filename);
      setDownloadState("idle");
    } catch (e) {
      setDownloadState(e?.response?.status === 409 ? "no_release" : "error");
    }
  }

  if (flagEnabled === null) {
    return (
      <div data-testid="harness-page-loading">
        <MarketingNav />
        <div style={pageStyle}>Loading…</div>
      </div>
    );
  }

  if (!flagEnabled) {
    return (
      <div data-testid="harness-page-not-available">
        <MarketingNav />
        <div style={pageStyle}>Aurem Loop isn&apos;t available yet.</div>
      </div>
    );
  }

  return (
    <div data-testid="harness-page">
      <MarketingNav />
      <div style={pageStyle}>
        <section data-testid="harness-hero">
          <h1 data-testid="harness-h1" style={h1Style}>{HARNESS.title}</h1>
          <p style={pStyle}>{HARNESS.heroLine}</p>
          {loggedIn ? (
            <button
              type="button"
              data-testid="harness-download-button"
              style={buttonStyle}
              onClick={handleDownload}
              disabled={downloadState === "downloading"}
            >
              {downloadState === "downloading" ? "Preparing your download…" : "Download Aurem Loop"}
            </button>
          ) : (
            <Link to="/signup" data-testid="harness-signup-cta" style={buttonStyle}>
              Create a free account to download
            </Link>
          )}
          {downloadState === "no_release" && (
            <p data-testid="harness-no-release" style={pStyle}>No build is published yet — check back soon.</p>
          )}
          {downloadState === "error" && (
            <p data-testid="harness-download-error" style={pStyle}>Couldn&apos;t start the download — try again shortly.</p>
          )}
        </section>

        <section data-testid="harness-how-it-works">
          <h2 style={h2Style}>How it works</h2>
          <ol>
            {HARNESS.howItWorks.map((step, i) => (
              <li key={i} data-testid={`harness-step-${i}`} style={pStyle}>{step}</li>
            ))}
          </ol>
        </section>

        <section data-testid="harness-what-it-does-not-do">
          <h2 style={h2Style}>What it does — and doesn&apos;t</h2>
          <ul>
            {HARNESS.whatItDoes.map((item, i) => (
              <li key={i} data-testid={`harness-does-${i}`} style={pStyle}>{item}</li>
            ))}
          </ul>
        </section>

        <section data-testid="harness-tiers">
          <h2 style={h2Style}>Free vs. subscriber</h2>
          <p style={pStyle}>There is one download for everyone — subscriber extras unlock automatically on your account, nothing to reinstall.</p>
          <table style={tableStyle}>
            <tbody>
              {HARNESS.tiers.map((row, i) => (
                <tr key={i}>
                  <td style={tdStyle}>{row.feature}</td>
                  <td style={tdStyle} data-testid={`harness-tier-free-${i}`}>{row.free}</td>
                  <td style={tdStyle} data-testid={`harness-tier-sub-${i}`}>{row.subscriber}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section data-testid="harness-install-steps">
          <h2 style={h2Style}>Install (Windows)</h2>
          <ol>
            {HARNESS.installSteps.map((step, i) => (
              <li key={i} data-testid={`harness-install-${i}`} style={pStyle}>{step}</li>
            ))}
          </ol>
        </section>

        <section data-testid="harness-faq">
          <h2 style={h2Style}>FAQ</h2>
          {HARNESS.faq.map((item, i) => (
            <details key={i} data-testid={`harness-faq-${i}`} style={{ marginBottom: 8 }}>
              <summary style={{ cursor: "pointer", fontSize: 14, fontWeight: 500 }}>{item.q}</summary>
              <p style={pStyle}>{item.a}</p>
            </details>
          ))}
        </section>

        <footer style={footerStyle} data-testid="harness-footer">
          {HARNESS.footerLinks.map((l) => (
            <Link key={l.to} to={l.to} style={{ color: "inherit", marginRight: 16 }}>{l.label}</Link>
          ))}
        </footer>
      </div>
    </div>
  );
}
