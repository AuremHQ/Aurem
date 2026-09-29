/**
 * pages/DevelopersPage.jsx — 2026-09-12 agent-readiness fix.
 * Real developer portal (is-agentic.com "Developer portal" check):
 * API keys/auth, docs, quickstart, sandbox — all pointing at real,
 * live endpoints. Content lives in src/data/developerInfo.mjs so
 * this page and the build-time SEO snapshot (seo-prerender.mjs)
 * can never drift, same pattern as AboutPage/ContactPage.
 */
import React, { useEffect } from "react";
import { Link } from "react-router-dom";
import { DEVELOPERS } from "../data/developerInfo";
import MarketingNav from "../components/marketing/MarketingNav";

export default function DevelopersPage() {
  useEffect(() => {
    const prevTitle = document.title;
    document.title = DEVELOPERS.title;
    return () => { document.title = prevTitle; };
  }, []);

  return (
    <div data-testid="developers-page">
      <MarketingNav />
      <div style={{
        maxWidth: 820, margin: "0 auto", padding: "40px 24px 80px",
        color: "var(--text, #e8e3d3)",
        fontFamily: "Inter, system-ui, -apple-system, sans-serif",
        lineHeight: 1.65,
      }}>
      <h1 data-testid="developers-title" style={{
        fontSize: 32, fontWeight: 500, letterSpacing: "-0.02em",
        margin: "16px 0 24px",
      }}>{DEVELOPERS.heading}</h1>

      {DEVELOPERS.intro.map((p, i) => (
        <p key={i} data-testid={`developers-intro-${i}`} style={{
          fontSize: 14, color: "var(--text-dim, #a39d8a)", margin: "0 0 16px",
        }}>{p}</p>
      ))}

      {DEVELOPERS.sections.map((s) => (
        <section key={s.heading} data-testid={`developers-section-${s.testid}`} style={{ margin: "28px 0" }}>
          <h2 style={{ fontSize: 18, fontWeight: 600, margin: "0 0 10px" }}>{s.heading}</h2>
          <p style={{ fontSize: 14, color: "var(--text-dim, #a39d8a)", margin: "0 0 10px" }}>{s.body}</p>
          <ul style={{ margin: 0, paddingLeft: 20 }}>
            {s.links.map((l) => (
              <li key={l.href} style={{ marginBottom: 6 }}>
                <a href={l.href} data-testid={`developers-link-${l.testid}`}
                   target={l.external ? "_blank" : undefined}
                   rel={l.external ? "noopener noreferrer" : undefined}
                   style={{ color: "var(--accent, #ff8a2a)", fontSize: 13 }}>
                  {l.label}
                </a>
              </li>
            ))}
          </ul>
        </section>
      ))}

      <footer style={{
        marginTop: 60, paddingTop: 20,
        borderTop: "1px solid var(--border, rgba(255,200,120,0.16))",
        fontSize: 11, color: "var(--text-faint)",
      }}>
        <Link to="/about" style={{ color: "inherit", marginRight: 16 }}>About</Link>
        <Link to="/contact" style={{ color: "inherit", marginRight: 16 }}>Contact</Link>
        <Link to="/privacy" style={{ color: "inherit" }}>Privacy</Link>
      </footer>
      </div>
    </div>
  );
}
