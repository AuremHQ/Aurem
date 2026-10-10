/**
 * components/marketing/MarketingNav.jsx — 2026-09-26.
 *
 * The ONE real site header (logo image + "ORA by Aurem" wordmark +
 * Features/Pricing/Integrations/Reviews/Bug Hunt/Sign in/Start free),
 * extracted verbatim from Landing.jsx's `<nav className="nav">` so
 * every public marketing page can share the exact same header the
 * homepage uses — instead of each page hand-rolling its own (which is
 * how /why-ora ended up with a plain-text "AUREM · CTO" logo instead
 * of the real logo image + "ORA by Aurem" wordmark, and how /about,
 * /contact, /developers, and the policy pages ended up with no real
 * header at all, just a "← Back to AUREM" text link).
 *
 * Self-contained: ships its own scoped CSS (under `.aurem-marketing-nav`)
 * with the same variable values Landing.jsx's `.ora-landing` scope
 * uses, so this renders identically on a page that doesn't have that
 * wrapper class. Same mobile behaviour too (≤720px collapses every
 * nav-link except "Sign in", keeps the "Start free" CTA visible).
 *
 * The 3 in-page-anchor links (Features/Pricing/Reviews) point at
 * `/#features` etc. so they work correctly from any page — Landing.jsx
 * has a small on-mount effect that scrolls to `location.hash` when
 * you land there from elsewhere (see Landing.jsx `useLocation` effect).
 */
import React from "react";
import { Link } from "react-router-dom";
import { getToken, getUser, isAdminOrFounder } from "../../lib/api";

const CSS = `
.aurem-marketing-nav {
  --mn-line:      rgba(255,255,255,0.08);
  --mn-accent:    #f59e0b;
  --mn-muted-1:   #94a3b8;
  --mn-muted-2:   #94a3b8;
  --mn-font-mono: ui-monospace, SFMono-Regular, "JetBrains Mono", "Fira Code", Menlo, monospace;
  position: sticky; top: 0; z-index: 50;
  backdrop-filter: blur(18px);
  background: rgba(10,14,26,0.72);
  border-bottom: 1px solid var(--mn-line);
  display: flex; align-items: center; justify-content: space-between;
  padding: 14px 32px;
}
.aurem-marketing-nav .mn-left { display: flex; align-items: center; gap: 14px; text-decoration: none; }
.aurem-marketing-nav .mn-logo-img { width: 38px; height: 38px; border-radius: 9px; }
.aurem-marketing-nav .mn-logo-text { font-family: var(--mn-font-mono); font-weight: 800; font-size: 22px; letter-spacing: -1px; color: var(--mn-accent); white-space: nowrap; }
.aurem-marketing-nav .mn-logo-text span { color: var(--mn-muted-2); font-weight: 400; font-size: 13px; margin-left: 6px; letter-spacing: 0; }
.aurem-marketing-nav .mn-links { display: flex; align-items: center; gap: 28px; }
.aurem-marketing-nav .mn-link { font-family: var(--mn-font-mono); font-size: 13px; color: var(--mn-muted-1); text-decoration: none; transition: color 0.15s; white-space: nowrap; padding: 9px 2px; }
.aurem-marketing-nav .mn-link:hover { color: var(--mn-accent); }
.aurem-marketing-nav .mn-cta {
  font-family: var(--mn-font-mono); font-size: 13px;
  background: var(--mn-accent); color: #000; padding: 9px 18px;
  border-radius: 8px; text-decoration: none; font-weight: 600; white-space: nowrap;
  transition: opacity 0.15s, transform 0.08s;
}
.aurem-marketing-nav .mn-cta:hover { opacity: 0.92; }
.aurem-marketing-nav .mn-cta:active { transform: translateY(1px); }
/* Tablets (721-1000px): the full link row plus the logo tagline does not
   fit, so the links used to wrap onto two lines ("Bug / Hunt"). Drop the
   tagline and tighten spacing instead. */
@media (max-width: 1000px) {
  .aurem-marketing-nav { padding: 14px 20px; }
  .aurem-marketing-nav .mn-logo-text span { display: none; }
  .aurem-marketing-nav .mn-links { gap: 14px; }
}
@media (max-width: 720px) {
  .aurem-marketing-nav { padding: 12px 20px; }
  .aurem-marketing-nav .mn-links { gap: 16px; }
  .aurem-marketing-nav .mn-link { display: none; }
  .aurem-marketing-nav .mn-link:last-of-type { display: inline; }
}
`;

export default function MarketingNav() {
  return (
    <nav className="aurem-marketing-nav" data-testid="ora-nav">
      <style>{CSS}</style>
      <Link to="/" className="mn-left" data-testid="marketing-nav-logo">
        <picture>
          <source srcSet="/ora-icon.webp 1x, /ora-icon@2x.webp 2x" type="image/webp" />
          <img src="/ora-icon.png" srcSet="/ora-icon.png 1x, /ora-icon@2x.png 2x" alt="ORA" className="mn-logo-img" width="38" height="38" decoding="async" />
        </picture>
        <div className="mn-logo-text">ORA<span> by Aurem</span></div>
      </Link>
      <div className="mn-links">
        <Link className="mn-link" to="/#features" data-testid="nav-features">Features</Link>
        <Link className="mn-link" to="/#pricing" data-testid="nav-pricing">Pricing</Link>
        <Link className="mn-link" to="/integrations" data-testid="nav-integrations">Integrations</Link>
        <Link className="mn-link" to="/#reviews" data-testid="nav-reviews">Reviews</Link>
        {(!getToken() || isAdminOrFounder(getUser())) && (
          <Link className="mn-link" to="/bug-hunt" data-testid="nav-bughunt">Bug Hunt</Link>
        )}
        <Link className="mn-link" to="/login" data-testid="nav-login">Sign in</Link>
        <Link className="mn-cta" to="/signup" data-testid="nav-signup-cta">Start free</Link>
      </div>
    </nav>
  );
}
