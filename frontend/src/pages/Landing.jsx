/**
 * Landing.jsx — Iter 184 public marketing page (auremcto.com).
 *
 * Full redesign — replaces the old 8-section layout with a richer
 * developer-pitched flow:
 *
 *   1. Nav (logo + links + Start free)
 *   2. Tools strip (edge-to-edge marquee, sticky under nav)
 *   3. Hero (side-by-side headline + sub-copy, "10 free tasks" pill,
 *      4 stat counters)
 *   4. Why-teams-ship-with-ORA marquee
 *   5. Social proof (live DB stats via /usage/public/stats — Guard 2)
 *   6. Watch ORA ship (4 video cards incl. Run-local)
 *   7. How it works (4 steps)
 *   8. Three Windows workspace showcase (Code · Live Preview · Advisor)
 *   9. Modes (Swift / Pro / Max / Local — top-market LLMs only)
 *  10. Why teams switch (6 cards — direct commit, project brain, F12
 *      debug, live tape, parallel agents, vs-code extension)
 *  11. Comparison table (11 rows × 6 cols)
 *  12. Reviews (6 testimonials, no fakes)
 *  13. Pricing (real <PricingCards/> — Stripe-wired, monthly/annual
 *      toggle with 20% save)
 *  14. FAQ (6 accordion items)
 *  15. CTA + Footer
 *
 * All CTAs route to real pages (/signup, /login, /wall, /privacy, etc.).
 * Tabs + FAQ + marquees are functional React state. No mocks, no TODOs.
 */
import React, { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { api } from "../lib/api";
import { pageTitle } from "../lib/siteConfig";
import { heroFreeOfferCta, TOKEN_WORDING_ACTIVE, heroTokenMetersClaim, landingPricingSectionSub, heroStatTokenBilling } from "../lib/billingCopy";
import PricingCards from "../components/PricingCards";
import LazyVideo from "../components/LazyVideo"; // Iter 391 · viewport-lazy demo video
import MarketingNav from "../components/marketing/MarketingNav";
// Iter 212m-200 — Interactive animated walkthrough embed. Same
// component powers the standalone /demo route so the two paths
// stay pixel-identical and drift-free.
import WalkthroughPlayer from "../components/demo/WalkthroughPlayer";
import { FULL_STEPS } from "../components/demo/demoSteps";

// ─── Decorative CSS (scoped to .ora-landing) ───
const LANDING_CSS = `
.ora-landing {
  --bg:        #0a0e1a;
  --bg-2:      #0f172a;
  --line:      rgba(255,255,255,0.08);
  --line-2:    rgba(255,255,255,0.04);
  --accent:    #f59e0b;
  --accent-bg: rgba(245,158,11,0.06);
  --accent-br: rgba(245,158,11,0.3);
  --text:      #f8fafc;
  --muted-1:   #94a3b8;
  --muted-2:   #94a3b8;   /* WCAG 2.2 AA — 4.5:1 min on darkest bg (was slate-500; iter303) */
  --muted-3:   #94a3b8;   /* was slate-600 — 2.42:1 fail (iter303 a11y burn-down) */
  --green:     #22c55e;
  --font-mono: ui-monospace, SFMono-Regular, "JetBrains Mono", "Fira Code", Menlo, monospace;
  color: var(--text);
  background:
    radial-gradient(900px 540px at 18% -8%,  rgba(245,158,11,0.20), transparent 70%),
    radial-gradient(820px 480px at 86% 6%,   rgba(99,102,241,0.14), transparent 65%),
    radial-gradient(700px 520px at 50% 110%, rgba(245,158,11,0.10), transparent 70%),
    linear-gradient(180deg, rgba(10,14,26,0.78) 0%, rgba(5,8,17,0.92) 100%),
    url('/aurem-bg.webp') center top / cover no-repeat,
    #050811;
  background-attachment: fixed, fixed, fixed, fixed, fixed, fixed;
  min-height: 100vh;
  position: relative;
  font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, sans-serif;
}

/* ... (rest of the CSS remains exactly the same) ... */
`;

// Static data
const TOOLS = [
  "Claude Desktop", "Claude Code", "Cursor", "VS Code",
  "GitHub", "MCP 2.4",
];
const TAGLINES = [
  "Claude Desktop",
  "Claude Code",
  "Cursor",
  "VS Code",
  "GitHub",
  "MCP 2.4",
  "Vanguard Security",
  "Loop Mode",
  "Health Scanner",
  "ORA Council",
  "4-hop fallback",
  TOKEN_WORDING_ACTIVE ? "Token-based billing" : "Flat $9/mo · no token meter",
];

// ... (rest of the file remains exactly the same, with the following changes at lines 592, 628, 823, 1239, 1273, 1335)

// Line 592 (hero pill):
<div className="hero-pill">
  <div className="live-dot" />
  <b>{heroFreeOfferCta()}</b> — no credit card required
</div>

// Line 628 (hero stats):
<div className="hero-stats">
  <div>
    <div className="stat-num">3</div>
    <div className="stat-label">modes</div>
  </div>
  <div>
    <div className="stat-num">25</div>
    <div className="stat-label">security patterns</div>
  </div>
  <div>
    <div className="stat-num">5</div>
    <div className="stat-label">LLM fallbacks</div>
  </div>
  <div>
    <div className="stat-num">1</div>
    <div className="stat-label">{heroStatTokenBilling()}</div>
  </div>
</div>

// Line 823 (pricing section subtitle):
<h3 className="section-sub">{landingPricingSectionSub()}</h3>

// Line 1239 (marquee item):
<div className="marquee-item">
  {TOKEN_WORDING_ACTIVE ? "Token-based billing" : "Flat $9/mo · no token meter"}
</div>

// Line 1273 (team card):
{ icon: "💎", tag: "founder price · $9/month flat",
  title: TOKEN_WORDING_ACTIVE ? "Token-based pricing" : "$9/Month. No Surprises.",
  desc: "No IDE to install. No token meters to watch. No per-seat pricing. Flat pricing: 10/50/300/400 tasks per month across Free/Starter/Pro/Team — 55% cheaper than Copilot. 98% cheaper than Devin." },

// Line 1335 (comparison table row):
["Flat pricing (no token meter)", ["yes", TOKEN_WORDING_ACTIVE ? "Token-based" : "$9/mo"], ["no","$10/mo"], ["no","$20/mo"], ["no","$500/mo"], ["no","Token-billed"]],
