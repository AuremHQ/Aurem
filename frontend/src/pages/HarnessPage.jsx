/**
 * pages/HarnessPage.jsx — Aurem Loop harness, Phase A.
 *
 * Content ownership (2026-11-10 architecture decision): the page's
 * own copy lives in the separate, private harness repo
 * (AuremHQ/aurem-loop, file web/harness-page-content.json) — not in
 * this file, not in this app's git history — so the exact same
 * content can later move to the harness's own subdomain
 * (loop.auremcto.com) with zero rewrite. This component only ever
 * RENDERS what GET /api/harness/content (routers/harness.py ->
 * services/harness_content_repo.py) returns; it never hardcodes
 * marketing copy itself. Live GitHub stars/forks (wsp-kit repo)
 * overlay on top via the same response's `live_stats`
 * (services/wsp_kit_stats.py) — clones/views stay the dated snapshot
 * baked into the content doc, per the verified-facts rule that only
 * stars/forks are ever live.
 *
 * Visibility: gated by the public GET /api/harness/page-status, same
 * as before — flag off means "not available yet" for everyone, no
 * content fetch even attempted.
 *
 * Phase A rule (do not loosen without a matching backend change):
 * there is NO download CTA anywhere on this page. The only call to
 * action is the waitlist mailto. Phase B (real download,
 * routers/harness_downloads.py) is a separate, later change to both
 * this file and that router.
 */
import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, getToken, healthApi } from "../lib/api";
import MarketingNav from "../components/marketing/MarketingNav";

// 2026-11-11 — founder asked for the exact same background treatment
// as the ORA homepage (Landing.jsx's `.ora-landing` class: layered
// radial-gradient glows + `/aurem-bg.webp` hero image + mobile
// variant). Extracted here as its own scoped class (not reusing
// `.ora-landing` directly — that class also carries ~70 unrelated
// homepage-specific rules this page doesn't want) so only the
// background itself is shared, pixel-identical.
const HARNESS_BG_CSS = `
.aurem-harness-page {
  background:
    radial-gradient(900px 540px at 18% -8%,  rgba(245,158,11,0.20), transparent 70%),
    radial-gradient(820px 480px at 86% 6%,   rgba(99,102,241,0.14), transparent 65%),
    radial-gradient(700px 520px at 50% 110%, rgba(245,158,11,0.10), transparent 70%),
    linear-gradient(180deg, rgba(10,14,26,0.78) 0%, rgba(5,8,17,0.92) 100%),
    url('/aurem-bg.webp') center top / cover no-repeat,
    #050811;
  background-attachment: fixed, fixed, fixed, fixed, fixed, fixed;
  min-height: 100vh;
}
@media (max-width: 720px) {
  .aurem-harness-page { background-image:
    radial-gradient(700px 420px at 50% -4%, rgba(245,158,11,0.18), transparent 70%),
    linear-gradient(180deg, rgba(10,14,26,0.82) 0%, rgba(5,8,17,0.94) 100%),
    url('/aurem-bg-mobile.webp') center top / cover no-repeat; }
}
`;

const ANCHORS = [
  ["what-it-is", "What it is"],
  ["control", "Control"],
  ["honest-numbers", "Honest numbers"],
  ["compare", "Compare"],
  ["stories", "Stories"],
  ["setup", "Setup"],
  ["faq", "FAQ"],
];

const pageStyle = {
  maxWidth: 820, margin: "0 auto", padding: "40px 24px 80px",
  color: "var(--text, #e8e3d3)",
  fontFamily: "Inter, system-ui, -apple-system, sans-serif",
  lineHeight: 1.65,
};
const anchorNavStyle = {
  display: "flex", flexWrap: "wrap", gap: 16, marginBottom: 32,
  paddingBottom: 16, borderBottom: "1px solid var(--border, rgba(255,200,120,0.16))",
};
const anchorLinkStyle = { color: "var(--text-dim, #a39d8a)", fontSize: 13, textDecoration: "none" };
const sectionStyle = { marginBottom: 48 };
const h1Style = { fontSize: 32, fontWeight: 500, letterSpacing: "-0.02em", margin: "16px 0 12px" };
const h2Style = { fontSize: 20, fontWeight: 500, margin: "0 0 12px" };
const pStyle = { fontSize: 14, color: "var(--text-dim, #a39d8a)", margin: "0 0 12px" };
const buttonStyle = {
  display: "inline-block", marginTop: 16, padding: "10px 20px",
  background: "var(--accent, #ff8a2a)", color: "#1a1208",
  border: "none", borderRadius: 8, fontSize: 14, fontWeight: 600,
  cursor: "pointer", textDecoration: "none",
};
const smallButtonStyle = {
  marginTop: 8, padding: "6px 14px", background: "transparent",
  color: "var(--text-dim, #a39d8a)", border: "1px solid var(--border, rgba(255,200,120,0.16))",
  borderRadius: 6, fontSize: 12, cursor: "pointer",
};
const badgeRowStyle = { display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap" };
const badgeStyle = {
  display: "inline-block", padding: "4px 10px", borderRadius: 999,
  background: "rgba(255,138,42,0.14)", color: "var(--accent, #ff8a2a)",
  fontSize: 11, fontWeight: 600, letterSpacing: "0.04em",
};
const tagRowStyle = { display: "flex", gap: 8, flexWrap: "wrap", marginTop: 20 };
const tagStyle = {
  padding: "6px 12px", borderRadius: 6, fontSize: 12,
  background: "rgba(255,255,255,0.05)", border: "1px solid var(--border, rgba(255,200,120,0.16))",
  color: "var(--text-dim, #a39d8a)",
};
const phaseABannerStyle = {
  marginTop: 20, padding: "12px 16px", borderRadius: 8,
  background: "rgba(255,138,42,0.08)", border: "1px solid rgba(255,138,42,0.3)",
  fontSize: 13, color: "var(--text-dim, #a39d8a)",
};
const subCardStyle = {
  padding: 16, borderRadius: 8, border: "1px solid var(--border, rgba(255,200,120,0.16))",
  marginBottom: 16,
};
const statRowStyle = { display: "flex", gap: 16, flexWrap: "wrap", margin: "16px 0" };
const statBoxStyle = {
  flex: "1 1 120px", padding: 16, borderRadius: 8,
  border: "1px solid var(--border, rgba(255,200,120,0.16))", textAlign: "center",
};
const statNumStyle = { fontSize: 24, fontWeight: 600 };
const statLabelStyle = { fontSize: 11, color: "var(--text-dim, #a39d8a)", marginTop: 4 };
const tableStyle = { width: "100%", borderCollapse: "collapse", fontSize: 13, marginTop: 8 };
const tdStyle = { padding: "8px 12px", borderBottom: "1px solid var(--border, rgba(255,200,120,0.16))" };
const summaryStyle = { cursor: "pointer", fontSize: 14, fontWeight: 500 };
const footerStyle = {
  marginTop: 60, paddingTop: 20,
  borderTop: "1px solid var(--border, rgba(255,200,120,0.16))",
  fontSize: 11, color: "var(--text-faint, #7a7462)",
};
// Phase B — download section (2026-11-10). Only ever rendered when
// GET /api/harness/release-status returns `available: true`, i.e. an
// admin has actually published a real release. See routers/harness.py.
const downloadCardStyle = {
  marginTop: 20, padding: "16px 20px", borderRadius: 8,
  background: "rgba(120,200,140,0.06)", border: "1px solid rgba(120,200,140,0.28)",
};
const monoStyle = {
  fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: 12,
  wordBreak: "break-all", color: "var(--text, #e8e3d3)",
};
const errorTextStyle = { fontSize: 12, color: "#e08a6a", marginTop: 8 };
// 2026-11-16 — download success message (founder): after a download
// completes, the card shows "🎊 Congratulations! Successfully
// downloaded" in this green box. Same green family as the card.
const downloadSuccessStyle = {
  marginTop: 12, padding: "12px 16px", borderRadius: 8,
  background: "rgba(120,200,140,0.12)", border: "1px solid rgba(120,200,140,0.45)",
  fontSize: 14, fontWeight: 600, color: "var(--text, #e8e3d3)",
};

// Fix 3 (2026-11-11) — replaces the flat black "Loading…" text with a
// lightweight pulsing skeleton (nav height + hero-shaped bars) so the
// first paint looks like "the real page is arriving" instead of "the
// page is broken/blank". Pure CSS, no extra request.
const SKELETON_CSS = `
@keyframes aurem-skel-pulse { 0%,100% { opacity: 0.35; } 50% { opacity: 0.12; } }
.aurem-harness-skel-bar {
  background: var(--text, #e8e3d3);
  border-radius: 6px;
  animation: aurem-skel-pulse 1.6s ease-in-out infinite;
}
`;

function HarnessSkeleton() {
  return (
    <div style={pageStyle} data-testid="harness-page-skeleton">
      <style>{SKELETON_CSS}</style>
      <div className="aurem-harness-skel-bar" style={{ width: 160, height: 28, marginBottom: 24 }} />
      <div className="aurem-harness-skel-bar" style={{ width: "70%", height: 40, marginBottom: 16 }} />
      <div className="aurem-harness-skel-bar" style={{ width: "90%", height: 16, marginBottom: 10 }} />
      <div className="aurem-harness-skel-bar" style={{ width: "55%", height: 16, marginBottom: 32 }} />
      <div className="aurem-harness-skel-bar" style={{ width: 140, height: 40 }} />
    </div>
  );
}

const waitlistInputStyle = {
  padding: "10px 14px", borderRadius: 8, fontSize: 14, height: 40, boxSizing: "border-box",
  background: "rgba(255,255,255,0.05)", border: "1px solid var(--border, rgba(255,200,120,0.16))",
  color: "var(--text, #e8e3d3)", minWidth: 240,
};
const waitlistFormRowStyle = { display: "flex", gap: 10, flexWrap: "wrap", marginTop: 16, alignItems: "center" };
const waitlistSuccessStyle = {
  marginTop: 16, padding: "10px 16px", borderRadius: 8, fontSize: 13,
  background: "rgba(120,200,140,0.1)", border: "1px solid rgba(120,200,140,0.32)",
  color: "var(--text, #e8e3d3)",
};

// Fix 2 (2026-11-11) — real on-page waitlist form. Previously the
// only CTA was a `mailto:` link, which does nothing visible in a
// browser with no mail client configured (most automated/clean-
// machine test environments, and plenty of real browsers) — looked
// broken even though it "worked" for someone with Outlook/Mail.app
// set up. Posts to POST /api/harness/waitlist (routers/harness.py,
// public, per-IP rate-limited). The mailto is kept as a small
// secondary fallback link, not the primary action.
function WaitlistForm({ idSuffix, mailto, ctaLabel }) {
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState("idle"); // idle|submitting|success|error
  const [error, setError] = useState("");

  async function handleSubmit(e) {
    e.preventDefault();
    const trimmed = email.trim();
    if (!trimmed) return;
    setStatus("submitting");
    setError("");
    try {
      await healthApi.post("/harness/waitlist", { email: trimmed });
      setStatus("success");
    } catch (err) {
      const msg = err?.response?.data?.detail || "Could not join the waitlist — try again.";
      setError(msg);
      setStatus("error");
    }
  }

  if (status === "success") {
    return (
      <div style={waitlistSuccessStyle} data-testid={`harness-waitlist-success-${idSuffix}`}>
        You&apos;re on the waitlist — one email when the download goes live, nothing else.
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} data-testid={`harness-waitlist-form-${idSuffix}`}>
      <div style={waitlistFormRowStyle}>
        <input
          type="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@example.com"
          style={waitlistInputStyle}
          data-testid={`harness-waitlist-email-${idSuffix}`}
          disabled={status === "submitting"}
        />
        <button
          type="submit"
          style={{ ...buttonStyle, marginTop: 0, height: 40, boxSizing: "border-box", display: "inline-flex", alignItems: "center" }}
          data-testid={`harness-waitlist-submit-${idSuffix}`}
          disabled={status === "submitting"}
        >
          {status === "submitting" ? "Joining…" : ctaLabel}
        </button>
      </div>
      {status === "error" && (
        <p style={errorTextStyle} data-testid={`harness-waitlist-error-${idSuffix}`}>{error}</p>
      )}
      <p style={{ ...pStyle, fontSize: 12, marginTop: 8 }}>
        One email when the download goes live, nothing else. Prefer email?{" "}
        <a href={mailto} data-testid={`harness-waitlist-mailto-${idSuffix}`} style={{ color: "inherit" }}>
          Send it directly instead
        </a>.
      </p>
    </form>
  );
}

function waitlistMailto(content) {
  const email = content?.phaseA?.waitlistEmail || "polarisbuiltinc@gmail.com";
  const subject = encodeURIComponent(content?.phaseA?.waitlistSubject || "Aurem Loop waitlist");
  const body = encodeURIComponent(
    content?.phaseA?.waitlistBody ||
    "Please add me to the Aurem Loop waitlist. One email when the download goes live, nothing else.",
  );
  return `mailto:${email}?subject=${subject}&body=${body}`;
}

export default function HarnessPage() {
  const [flagEnabled, setFlagEnabled] = useState(null); // null = loading
  const [content, setContent] = useState(null);
  const [liveStats, setLiveStats] = useState(null);
  const [contentState, setContentState] = useState("loading"); // loading|ready|unavailable|error
  const [release, setRelease] = useState(null); // Phase B readiness — {available:false} until a real release is published
  const [releaseLoading, setReleaseLoading] = useState(true); // avoids a Phase-A-banner flash before this resolves
  const [downloadState, setDownloadState] = useState("idle"); // idle|downloading|error
  const [downloadError, setDownloadError] = useState("");
  // 2026-11-16 — download success message (founder): after a download
  // completes, show "🎊 Congratulations! Successfully downloaded" on
  // the download card. Cleared on the next click.
  const [downloadSuccess, setDownloadSuccess] = useState(false);

  // Fix 3 (2026-11-11) — all 3 public GETs now fire together on mount
  // instead of page-status → (then) content+release. The old shape
  // added one full network round-trip to the critical path before the
  // content fetch even started; this one costs nothing extra (content/
  // release are discarded harmlessly in the rare case the flag turns
  // out to be off) and the page now shows real content in max(3
  // requests) time instead of page-status + max(2 requests).
  //
  // loadContent() is pulled out as its own function (not inlined in
  // the effect below) so the "Refresh now" live-stats button further
  // down the page can re-run the same content+release fetch on demand
  // without re-checking page-status (which never changes mid-session).
  //
  // 2026-11-11 testing-agent finding: a failed REFRESH (content
  // already loaded once) must never wipe the whole page back to the
  // "temporarily unavailable" screen — that would also discard any
  // in-progress/just-submitted waitlist form state. Only the initial
  // load (no content yet) is allowed to flip to the full-page error;
  // a refresh failure instead just leaves the existing content on
  // screen and logs quietly (the stats simply stay at their last-known
  // values until the next successful refresh).
  function loadContent() {
    healthApi.get("/harness/content")
      .then((r) => {
        const c = r.data?.content;
        if (!c || c.available === false) { setContentState("unavailable"); return; }
        setContent(c);
        setLiveStats(r.data?.live_stats || null);
        setContentState("ready");
      })
      .catch(() => {
        setContentState((prev) => (prev === "ready" ? prev : "error"));
      });

    healthApi.get("/harness/release-status")
      .then((r) => setRelease(r.data || { available: false }))
      .catch(() => setRelease({ available: false }))
      .finally(() => setReleaseLoading(false));
  }

  useEffect(() => {
    let active = true;
    // 2026-11-11 testing-agent finding: a transient network error here
    // (e.g. a brief rate-limit hit) was being treated identically to a
    // real "flag is off" — bouncing straight to the permanent "Aurem
    // Loop isn't available yet" screen with no retry. One silent retry
    // after a short delay before giving up distinguishes a one-off
    // blip from an actual flag-off/misconfigured state.
    function fetchPageStatus(isRetry) {
      healthApi.get("/harness/page-status")
        .then((r) => { if (active) setFlagEnabled(r.data?.harness_page_enabled === true); })
        .catch(() => {
          if (!active) return;
          if (!isRetry) { setTimeout(() => fetchPageStatus(true), 1200); return; }
          setFlagEnabled(false);
        });
    }
    fetchPageStatus(false);

    loadContent();

    return () => { active = false; };
  }, []);

  async function handleDownloadClick() {
    if (!getToken()) {
      window.location.href = "/signup?next=/harness";
      return;
    }
    setDownloadState("downloading");
    setDownloadError("");
    setDownloadSuccess(false);
    try {
      const resp = await api.post("/harness/download", {}, { responseType: "blob" });
      const url = URL.createObjectURL(resp.data);
      const a = document.createElement("a");
      a.href = url;
      a.download = `aurem-loop-${release?.version || "latest"}.zip`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setDownloadSuccess(true);
      setDownloadState("idle");
    } catch (e) {
      const status = e?.response?.status;
      let msg = "Could not start the download — try again.";
      if (status === 401) msg = "You're not logged in — please sign in again.";
      else if (status === 409) msg = "No release is published yet — check back shortly.";
      else if (status === 429) msg = "Too many download attempts — try again in a minute.";
      setDownloadError(msg);
      setDownloadState("error");
    }
  }

  useEffect(() => {
    if (!content) return;
    const prevTitle = document.title;
    document.title = content.title || "Aurem Loop";
    const graph = [
      {
        "@type": "FAQPage",
        mainEntity: (content.faq || []).map(({ q, a }) => ({
          "@type": "Question",
          name: q,
          acceptedAnswer: { "@type": "Answer", text: a },
        })),
      },
      {
        "@type": "BreadcrumbList",
        itemListElement: [
          { "@type": "ListItem", position: 1, name: "AUREM", item: "https://auremcto.com/" },
          { "@type": "ListItem", position: 2, name: "Aurem Loop", item: "https://auremcto.com/harness" },
        ],
      },
    ];
    // Phase B — only added once a real release exists (release.available),
    // never speculatively. No "HowTo" block per the Phase A spec even here;
    // this is a plain SoftwareApplication fact sheet.
    if (release?.available) {
      graph.push({
        "@type": "SoftwareApplication",
        name: "Aurem Loop",
        operatingSystem: "Windows",
        applicationCategory: "DeveloperApplication",
        softwareVersion: release.version,
        offers: { "@type": "Offer", price: "0", priceCurrency: "USD" },
        license: "https://opensource.org/licenses/MIT",
      });
    }
    const ld = document.createElement("script");
    ld.type = "application/ld+json";
    ld.id = "ld-harness";
    ld.text = JSON.stringify({ "@context": "https://schema.org", "@graph": graph });
    document.head.appendChild(ld);
    return () => {
      document.title = prevTitle;
      document.getElementById("ld-harness")?.remove();
    };
  }, [content, release]);

  if (flagEnabled === null) {
    return (
      <div data-testid="harness-page-loading" className="aurem-harness-page">
        <style>{HARNESS_BG_CSS}</style>
        <MarketingNav minimal logoText="Aurem Loop" logoSubtext="Harness" />
        <HarnessSkeleton />
      </div>
    );
  }

  if (!flagEnabled) {
    return (
      <div data-testid="harness-page-not-available" className="aurem-harness-page">
        <style>{HARNESS_BG_CSS}</style>
        <MarketingNav minimal logoText="Aurem Loop" logoSubtext="Harness" />
        <div style={pageStyle}>Aurem Loop isn&apos;t available yet.</div>
      </div>
    );
  }

  if (contentState === "loading") {
    return (
      <div data-testid="harness-page-content-loading" className="aurem-harness-page">
        <style>{HARNESS_BG_CSS}</style>
        <MarketingNav minimal logoText="Aurem Loop" logoSubtext="Harness" />
        <HarnessSkeleton />
      </div>
    );
  }

  if (contentState !== "ready" || !content) {
    return (
      <div data-testid="harness-page-content-unavailable" className="aurem-harness-page">
        <style>{HARNESS_BG_CSS}</style>
        <MarketingNav minimal logoText="Aurem Loop" logoSubtext="Harness" />
        <div style={pageStyle}>Aurem Loop&apos;s page content is temporarily unavailable — check back shortly.</div>
      </div>
    );
  }

  const mailto = waitlistMailto(content);
  // 2026-11-16 — download lock visibility (founder): when logged out
  // the button says "Sign in to download" so nobody mistakes the page
  // for a free-for-all download; the click guard in handleDownloadClick
  // still redirects to /signup?next=/harness as the second check.
  const isLoggedIn = !!getToken();
  const stars = liveStats?.stars;
  const forks = liveStats?.forks;

  return (
    <div data-testid="harness-page" className="aurem-harness-page">
      <style>{HARNESS_BG_CSS}</style>
      <MarketingNav minimal logoText="Aurem Loop" logoSubtext="Harness" />
      <div style={pageStyle}>
        <nav data-testid="harness-anchor-nav" style={anchorNavStyle} aria-label="Page sections">
          {ANCHORS.map(([id, label]) => (
            <a key={id} href={`#${id}`} style={anchorLinkStyle} data-testid={`harness-anchor-${id}`}>{label}</a>
          ))}
        </nav>

        <section id="what-it-is" data-testid="harness-hero" style={sectionStyle}>
          <div style={badgeRowStyle}>
            {(content.badges || []).map((b) => (<span key={b} style={badgeStyle}>{b}</span>))}
          </div>
          <h1 data-testid="harness-h1" style={h1Style}>{content.hero?.headline}</h1>
          <p style={pStyle}>{content.hero?.subhead}</p>
          <p style={pStyle}>{content.hero?.body}</p>

          {releaseLoading ? null : release?.available ? (
            <div style={downloadCardStyle} data-testid="harness-download-section">
              <strong>Download available</strong> — v<span data-testid="harness-download-version">{release.version}</span>
              {release.mandatory && <span style={{ marginLeft: 8, ...badgeStyle }}>REQUIRED UPDATE</span>}
              <p style={{ ...pStyle, marginTop: 10 }}>
                SHA-256: <span style={monoStyle} data-testid="harness-download-sha256">{release.sha256}</span>
              </p>
              {release.changelog?.length > 0 && (
                <ul data-testid="harness-download-changelog">
                  {release.changelog.map((item, i) => (<li key={i} style={pStyle}>{item}</li>))}
                </ul>
              )}
              <button
                type="button"
                data-testid="harness-download-button"
                style={buttonStyle}
                disabled={downloadState === "downloading"}
                onClick={handleDownloadClick}
              >
                {downloadState === "downloading" ? "Starting download…" : isLoggedIn ? "Download for Windows" : "Sign in to download"}
              </button>
              {!isLoggedIn && (
                <p style={{ ...pStyle, fontSize: 12, marginTop: 8 }} data-testid="harness-download-signin-note">
                  A free ORA account is required to download — sign in or sign up, it takes a minute.
                </p>
              )}
              {downloadSuccess && (
                <div style={downloadSuccessStyle} data-testid="harness-download-success">
                  🎊 Congratulations! Successfully downloaded
                </div>
              )}
              {downloadState === "error" && (
                <p style={errorTextStyle} data-testid="harness-download-error">{downloadError}</p>
              )}
              <p style={{ ...pStyle, fontSize: 12, marginTop: 8 }}>
                MIT licensed. Requires a free ORA account to sign in locally — no cloud account, no credit card.
              </p>
            </div>
          ) : (
            <>
              <div style={phaseABannerStyle} data-testid="harness-phase-a-banner">
                <strong>PHASE A</strong> — {content.phaseA?.banner}
              </div>

              <WaitlistForm idSuffix="hero" mailto={mailto} ctaLabel="Join the waitlist" />
            </>
          )}

          <div style={tagRowStyle}>
            {(content.featureTags || []).map((t) => (
              <span key={t} data-testid="harness-feature-tag" style={tagStyle}>{t}</span>
            ))}
          </div>
        </section>

        {content.demoVideo?.url && (
          <section data-testid="harness-demo-video" style={sectionStyle}>
            <video
              data-testid="harness-demo-video-el"
              src={content.demoVideo.url}
              controls
              muted
              loop
              playsInline
              style={{ width: "100%", borderRadius: 8, border: "1px solid var(--border, rgba(255,200,120,0.16))" }}
            />
            <p style={pStyle}>{content.demoVideo.caption}</p>
          </section>
        )}

        <section id="control" data-testid="harness-set-apart" style={sectionStyle}>
          <h2 style={h2Style}>{content.setApart?.headline}</h2>
          <div style={subCardStyle} data-testid="harness-wsp-kit">
            <span style={badgeStyle}>{content.setApart?.wspKit?.badge}</span>
            <p style={{ ...pStyle, marginTop: 12 }}>{content.setApart?.wspKit?.body}</p>
          </div>
          <div style={subCardStyle} data-testid="harness-phone-tunnel">
            <span style={badgeStyle}>{content.setApart?.phoneTunnel?.badge}</span>
            <p style={{ ...pStyle, marginTop: 12 }}>{content.setApart?.phoneTunnel?.body}</p>
          </div>
        </section>

        <section id="honest-numbers" data-testid="harness-github-stats" style={sectionStyle}>
          <h2 style={h2Style}>{content.githubStats?.repoLabel}</h2>
          <p style={pStyle}>{content.githubStats?.note}</p>
          <div style={statRowStyle}>
            <div data-testid="harness-stat-stars" style={statBoxStyle}>
              <div style={statNumStyle}>{stars ?? "—"}</div>
              <div style={statLabelStyle}>GitHub stars · live</div>
            </div>
            <div data-testid="harness-stat-forks" style={statBoxStyle}>
              <div style={statNumStyle}>{forks ?? "—"}</div>
              <div style={statLabelStyle}>GitHub forks · live</div>
            </div>
            <div data-testid="harness-stat-clones" style={statBoxStyle}>
              <div style={statNumStyle}>{content.githubStats?.clones}</div>
              <div style={statLabelStyle}>clones — last verified {content.githubStats?.lastVerified}</div>
            </div>
            <div data-testid="harness-stat-views" style={statBoxStyle}>
              <div style={statNumStyle}>{content.githubStats?.views}</div>
              <div style={statLabelStyle}>views — last verified {content.githubStats?.lastVerified}</div>
            </div>
          </div>
          <button type="button" data-testid="harness-refresh-stats" style={smallButtonStyle} onClick={loadContent}>
            Refresh now
          </button>
        </section>

        <section data-testid="harness-what-it-is-not" style={sectionStyle}>
          <h2 style={h2Style}>What it is not</h2>
          <ul>
            {(content.whatItIsNot || []).map((s, i) => (
              <li key={i} data-testid={`harness-not-${i}`} style={pStyle}>{s}</li>
            ))}
          </ul>
        </section>

        <section id="compare" data-testid="harness-compare" style={sectionStyle}>
          <h2 style={h2Style}>{content.compare?.headline}</h2>
          <p style={pStyle}>{content.compare?.body}</p>
          <table style={tableStyle}>
            <tbody>
              {Object.entries(content.compare?.auremRow || {}).map(([k, v]) => (
                <tr key={k}>
                  <td style={tdStyle}>{k}</td>
                  <td style={tdStyle} data-testid={`harness-compare-${k.replace(/\s+/g, "-").toLowerCase()}`}>{v}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p style={{ ...pStyle, fontSize: 12, marginTop: 12 }} data-testid="harness-competitor-note">
            {content.compare?.competitorNote}
          </p>
        </section>

        <section data-testid="harness-who-not" style={sectionStyle}>
          <h2 style={h2Style}>Who should not pick Aurem Loop</h2>
          <ul>
            {(content.whoShouldNotPick || []).map((s, i) => (
              <li key={i} data-testid={`harness-who-not-${i}`} style={pStyle}>{s}</li>
            ))}
          </ul>
        </section>

        <section id="stories" data-testid="harness-users-say" style={sectionStyle}>
          <h2 style={h2Style}>{content.usersSay?.headline}</h2>
          <p style={pStyle}>{content.usersSay?.body}</p>
          <WaitlistForm idSuffix="stories" mailto={mailto} ctaLabel="Be one of the first — join the waitlist" />
        </section>

        <section id="setup" data-testid="harness-install-steps" style={sectionStyle}>
          <h2 style={h2Style}>{content.setup?.headline}</h2>
          <ol>
            {(content.setup?.steps || []).map((s, i) => (
              <li key={i} data-testid={`harness-install-${i}`} style={pStyle}>{s}</li>
            ))}
          </ol>
        </section>

        <section data-testid="harness-troubleshooting" style={sectionStyle}>
          <h2 style={h2Style}>Troubleshooting</h2>
          {(content.troubleshooting || []).map((t, i) => (
            <details key={i} data-testid={`harness-trouble-${i}`} style={{ marginBottom: 8 }}>
              <summary style={summaryStyle}>{t.q}</summary>
              <p style={pStyle}>{t.a}</p>
            </details>
          ))}
        </section>

        <section id="faq" data-testid="harness-faq" style={sectionStyle}>
          <h2 style={h2Style}>FAQ</h2>
          {(content.faq || []).map((item, i) => (
            <details key={i} data-testid={`harness-faq-${i}`} style={{ marginBottom: 8 }}>
              <summary style={summaryStyle}>{item.q}</summary>
              <p style={pStyle}>{item.a}</p>
            </details>
          ))}
        </section>

        <footer style={footerStyle} data-testid="harness-footer">
          <p>{content.footer?.tagline}</p>
          <p>{content.footer?.legal}</p>
          <p>Waitlist: <a href={mailto} style={{ color: "inherit" }}>{content.footer?.waitlistEmail}</a></p>
          <p>{content.footer?.license}</p>
          <div style={{ marginTop: 12 }}>
            <Link to="/about" style={{ color: "inherit", marginRight: 16 }}>About</Link>
            <Link to="/contact" style={{ color: "inherit", marginRight: 16 }}>Contact</Link>
            <Link to="/privacy" style={{ color: "inherit" }}>Privacy</Link>
          </div>
        </footer>
      </div>
    </div>
  );
}
