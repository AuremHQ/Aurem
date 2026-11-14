/**
 * DotPage.jsx — ORA dot homepage, 2026-11-14.
 *
 * ORA dot is a NEW standalone product (local-first AI workspace,
 * open-source models, on the user's own machine — like OpenDots and
 * like Muse), NOT an Aurem feature and NOT the ORA agency product.
 * Its homepage lives at /dot on the same auremcto.com domain.
 *
 * There is NO download yet (product Phase 0-1 not built), so this
 * page does two things only:
 *   1. Explain what ORA dot is, in simple words.
 *   2. Collect a list of interested people (name + email) via
 *      POST /api/dot/signup — the list Teji sees at /admin.
 *
 * The page ALSO links to the EXISTING account signup/signin — with
 * ?next=/dot carried through (Fix 7 gating, already generic in
 * Signup.jsx/Login.jsx and the GitHub/Google OAuth routers), so a
 * Dot visitor always lands back on /dot, never on ORA or /harness.
 * No new auth system, no payment, no download, no public claims
 * beyond what this file says. Copy lives inline here for now (no
 * product repo exists yet); it can move to a content doc later, the
 * way /harness did.
 *
 * Visual shell: the same background treatment as /harness
 * (radial-gradient glows + /aurem-bg.webp + mobile variant) and the
 * same MarketingNav, so the two product pages look like siblings.
 */
import React, { useState } from "react";
import { Link } from "react-router-dom";
import { healthApi } from "../lib/api";
import MarketingNav from "../components/marketing/MarketingNav";

const DOT_BG_CSS = `
.aurem-dot-page {
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
  .aurem-dot-page { background-image:
    radial-gradient(700px 420px at 50% -4%, rgba(245,158,11,0.18), transparent 70%),
    linear-gradient(180deg, rgba(10,14,26,0.82) 0%, rgba(5,8,17,0.94) 100%),
    url('/aurem-bg-mobile.webp') center top / cover no-repeat; }
}
`;

const pageStyle = {
  maxWidth: 820, margin: "0 auto", padding: "40px 24px 80px",
  color: "var(--text, #e8e3d3)",
  fontFamily: "Inter, system-ui, -apple-system, sans-serif",
  lineHeight: 1.65,
};
const sectionStyle = { marginBottom: 48 };
const h1Style = { fontSize: 32, fontWeight: 500, letterSpacing: "-0.02em", margin: "16px 0 12px" };
const h2Style = { fontSize: 20, fontWeight: 500, margin: "0 0 12px" };
const pStyle = { fontSize: 14, color: "var(--text-dim, #a39d8a)", margin: "0 0 12px" };
const badgeRowStyle = { display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap" };
const badgeStyle = {
  display: "inline-block", padding: "4px 10px", borderRadius: 999,
  background: "rgba(255,138,42,0.14)", color: "var(--accent, #ff8a2a)",
  fontSize: 11, fontWeight: 600, letterSpacing: "0.04em",
};
const cardStyle = {
  background: "rgba(255,255,255,0.05)", border: "1px solid var(--border, rgba(255,200,120,0.16))",
  borderRadius: 10, padding: "16px 18px", marginBottom: 12,
};
const inputStyle = {
  width: "100%", boxSizing: "border-box", padding: "10px 12px", marginBottom: 10,
  background: "rgba(255,255,255,0.06)", border: "1px solid var(--border, rgba(255,200,120,0.16))",
  borderRadius: 8, color: "var(--text, #e8e3d3)", fontSize: 14, outline: "none",
};
const buttonStyle = {
  display: "inline-block", marginTop: 6, padding: "10px 20px",
  background: "var(--accent, #ff8a2a)", color: "#1a1208",
  border: "none", borderRadius: 8, fontSize: 14, fontWeight: 600,
  cursor: "pointer", textDecoration: "none",
};
const noteStyle = { fontSize: 12.5, color: "var(--text-dim, #a39d8a)", marginTop: 12 };

const FAQ = [
  {
    q: "What is ORA dot?",
    a: "ORA dot is your own AI workspace, on your own machine — chat, documents, coding and research with open-source models running locally. Your files stay on your PC.",
  },
  {
    q: "When can I download it?",
    a: "There is no download yet — ORA dot is being built now. Join the list below and we will tell you when the download is ready.",
  },
  {
    q: "Does it send my files to the cloud?",
    a: "No. ORA dot runs on your own machine and your files stay there. A cloud model is only used if you later connect one yourself with your own API key — and then only what you send leaves your machine.",
  },
  {
    q: "Is ORA dot the same as ORA by Aurem?",
    a: "No. ORA by Aurem is the AI app agency; ORA dot is a separate product — your own AI workspace on your own machine. One account works for both.",
  },
];

function DotSignupForm() {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState("idle"); // idle|submitting|success|error
  const [error, setError] = useState("");

  async function submit(e) {
    e.preventDefault();
    const cleanName = name.trim();
    const cleanEmail = email.trim();
    if (!cleanName) {
      setError("Please enter your name.");
      setStatus("error");
      return;
    }
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(cleanEmail)) {
      setError("Please enter a valid email address.");
      setStatus("error");
      return;
    }
    setStatus("submitting");
    setError("");
    try {
      await healthApi.post("/dot/signup", { name: cleanName, email: cleanEmail });
      setStatus("success");
    } catch (err) {
      setStatus("error");
      setError(err?.response?.data?.detail || "Something went wrong. Please try again.");
    }
  }

  if (status === "success") {
    return (
      <div data-testid="dot-signup-success" style={{ ...cardStyle, borderColor: "rgba(120,200,140,0.32)", background: "rgba(120,200,140,0.1)" }}>
        <strong>You&rsquo;re on the list.</strong> We&rsquo;ll email you when the ORA dot download is ready.
      </div>
    );
  }

  return (
    <form onSubmit={submit} data-testid="dot-signup-form" style={cardStyle}>
      <label htmlFor="dot-signup-name" style={{ display: "block", fontSize: 13, marginBottom: 6 }}>Your name</label>
      <input
        id="dot-signup-name"
        data-testid="dot-signup-name"
        type="text"
        autoComplete="name"
        placeholder="Your name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        style={inputStyle}
      />
      <label htmlFor="dot-signup-email" style={{ display: "block", fontSize: 13, marginBottom: 6 }}>Your email</label>
      <input
        id="dot-signup-email"
        data-testid="dot-signup-email"
        type="email"
        autoComplete="email"
        placeholder="you@example.com"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        style={inputStyle}
      />
      {status === "error" && error && (
        <p data-testid="dot-signup-error" style={{ ...pStyle, color: "#f87171" }}>{error}</p>
      )}
      <button type="submit" data-testid="dot-signup-submit" style={buttonStyle} disabled={status === "submitting"}>
        {status === "submitting" ? "Joining…" : "Join the list"}
      </button>
      <p style={noteStyle}>Joining only saves your name and email — nothing else.</p>
    </form>
  );
}

export default function DotPage() {
  return (
    <div className="aurem-dot-page" data-testid="dot-page">
      <style>{DOT_BG_CSS}</style>
      <MarketingNav minimal logoText="ORA" logoSubtext="dot" homeHref="/dot" next="/dot" hidePricing />
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: JSON.stringify({
            "@context": "https://schema.org",
            "@type": "WebPage",
            name: "ORA dot — your AI workspace, on your own machine",
            url: "https://auremcto.com/dot",
            description: "ORA dot is a local-first AI workspace — chat, documents, coding and research with open-source models running on your own machine.",
            mainEntity: {
              "@type": "FAQPage",
              mainEntity: FAQ.map((f) => ({
                "@type": "Question",
                name: f.q,
                acceptedAnswer: { "@type": "Answer", text: f.a },
              })),
            },
          }),
        }}
      />
      <main style={pageStyle}>
        <section style={sectionStyle}>
          <div style={badgeRowStyle}>
            <span style={badgeStyle}>LOCAL-FIRST</span>
            <span style={badgeStyle}>OPEN-SOURCE MODELS</span>
            <span style={badgeStyle}>COMING SOON</span>
          </div>
          <h1 data-testid="dot-h1" style={h1Style}>Your AI workspace, on your own machine.</h1>
          <p style={pStyle}>
            Chat, documents, coding and research — with open-source models running on your PC.
            Your files stay on your machine. Nothing leaves it unless you say so.
          </p>
          <p style={pStyle}>
            No download yet — we&rsquo;re building it now. Join the list and we&rsquo;ll tell you
            when it&rsquo;s ready.
          </p>
        </section>

        <section style={sectionStyle}>
          <h2 style={h2Style}>What it is</h2>
          <div style={cardStyle}>
            <strong>Runs on your PC.</strong>
            <p style={pStyle}>No cloud account needed. The app and your files live on your own machine.</p>
          </div>
          <div style={cardStyle}>
            <strong>Open-source models.</strong>
            <p style={pStyle}>Built on open models you can inspect — not a locked black box.</p>
          </div>
          <div style={cardStyle}>
            <strong>You approve every action.</strong>
            <p style={pStyle}>Nothing happens without your say-so. Every action is reviewed, approved, and logged.</p>
          </div>
          <p style={pStyle}>
            The first version does chat, documents, coding, research and scheduled tasks — with
            your approval. No unrestricted desktop control, no purchases on its own, no
            arbitrary commands.
          </p>
        </section>

        <section style={sectionStyle}>
          <h2 style={h2Style}>Join the list</h2>
          <DotSignupForm />
          <p style={noteStyle}>
            Already have an ORA account?{" "}
            <Link to="/login?next=%2Fdot" data-testid="dot-signin-link">Sign in</Link>
            {" "}— you&rsquo;ll come straight back here. New here?{" "}
            <Link to="/signup?next=%2Fdot" data-testid="dot-signup-link">Create your free account</Link>
            {" "}— you&rsquo;ll come straight back here too. One account works for both ORA and ORA dot.
          </p>
        </section>

        <section style={sectionStyle}>
          <h2 style={h2Style}>Questions</h2>
          {FAQ.map((f, i) => (
            <div key={i} data-testid={`dot-faq-${i}`} style={cardStyle}>
              <strong>{f.q}</strong>
              <p style={pStyle}>{f.a}</p>
            </div>
          ))}
        </section>

        <footer style={{ ...pStyle, fontSize: 12, borderTop: "1px solid var(--border, rgba(255,200,120,0.16))", paddingTop: 16 }}>
          © 2026 Polaris Built Inc. — ORA dot, made by AUREM ·{" "}
          <Link to="/privacy" data-testid="dot-privacy-link">Privacy</Link>
        </footer>
      </main>
    </div>
  );
}
