/**
 * pages/HarnessPage.jsx — Aurem Loop harness, Midnight Gold homepage
 * (2026-11-17, built from the attached harness-homepage-sign-in-
 * preview.html design).
 *
 * Replaces the prior content-doc-driven Phase A/B page. This one is
 * fully self-contained — no fetch to the private AuremHQ/aurem-loop
 * content repo — except for two real, live backend reads:
 *   - GET /harness/page-status   — same kill-switch flag as before.
 *   - GET /harness/release-status — version/sha256/size_bytes, never
 *     hardcoded (the design mockup hardcodes "0.8.0" / the SHA / the
 *     byte count as placeholder values; this reads the real ones).
 *
 * The attached file's sign-up/sign-in forms were DEMO ONLY (an
 * in-memory `accounts = {}` object, reset on every reload). Every
 * form here calls the REAL ORA auth endpoints — the same
 * /auth/signup, /auth/login, /auth/login/2fa-verify,
 * /auth/forgot-password used by Signup.jsx/Login.jsx. Signups from
 * this page are tagged `source: "harness_homepage"` (see
 * routers/auth.py::_VALID_SIGNUP_SOURCES) so the admin Harness panel
 * can tell them apart. No separate auth system, no new user table.
 *
 * Two deliberate deviations from the literal static mockup, flagged
 * here rather than silently done:
 *   1. Password strength reuses the REAL site-wide scorer
 *      (lib/passwordStrength.js — the same one Signup.jsx's own
 *      submit gate uses) instead of the mockup's simplified 3-rule
 *      local scorer, so the minimum-acceptable-password bar is
 *      identical everywhere on the site, not a second, looser one
 *      just for this page.
 *   2. The mockup's footer "Cookie preferences" button opens its own
 *      static, non-functional toggle panel (Analytics/Preferences
 *      switches that don't actually gate anything). This page instead
 *      fires the SAME real, already-site-wide consent reopen used by
 *      every other page's footer (`aurem:reopen-consent`, handled by
 *      the globally-mounted <CookieConsentBanner/> in App.jsx) — a
 *      fake toggle panel that doesn't control real tracking would be
 *      exactly the kind of simulated UI this rebuild was asked to
 *      remove, not add.
 *
 * All static copy (hero, features, FAQ, legal, footer) lives in
 * src/data/harnessContent.mjs, imported by BOTH this component AND
 * scripts/seo-prerender.mjs — same drift-proof pattern as
 * competitors.mjs/companyInfo.mjs — so the crawler-facing static
 * /harness snapshot can never show different copy than this live
 * page. Fields named `*Html` there are pre-built, developer-authored
 * HTML strings (bold/link markup only, no user input) rendered via
 * dangerouslySetInnerHTML — the same pattern PolicyPage.jsx already
 * uses for its own static markdown-derived content.
 */
import React, { useEffect, useState } from "react";
import { api, healthApi, getToken, setToken, setUser } from "../lib/api";
import { useSearchParams } from "react-router-dom";
import { scorePassword, MIN_ACCEPTABLE_SCORE } from "../lib/passwordStrength";
import {
  HERO, POINTS, BENCH, FEAT_ITEMS, FLOW_STEPS, REQUIREMENTS, INSTALL, FAQ_ITEMS,
  LEGAL_ARTICLES, FOOTER_COLS, FOOTER_TAGLINE, FOOTER_COPY, FOOTER_TRADEMARK,
} from "../data/harnessContent.mjs";
import { LOOP_JSON_LD } from "../data/aaoJsonLd";
import { ErrorState, FieldError, SuccessState } from "../components/ui-states/UiStates.jsx";

const CSS = `
:root {
  --bg: #101114;
  --surface: #1a1c21;
  --surface-2: #26282f;
  --ink: #f6f1e2;
  --ink-2: #cfc7ae;
  --ink-3: #9a937f;
  --line: #383b44;
  --accent: #e3ac2f;
  --accent-strong: #f2c14e;
  --accent-ink: #1a1400;
  --accent-soft: #33290e;
  --ok-bg: #1d3320;
  --ok-ink: #93e2a4;
  --warn-bg: #372a0f;
  --warn-ink: #f2d07c;
  --warn-line: #5d4a1a;
  --err: #ff9d94;
  --shadow: 0 18px 44px rgba(0, 0, 0, 0.55);
  --radius: 14px;
}
.aurem-harness-v2 * { box-sizing: border-box; }
.aurem-harness-v2 {
  background: var(--bg); color: var(--ink);
  font-family: "Inter", "Segoe UI", system-ui, -apple-system, "Helvetica Neue", Arial, sans-serif;
  line-height: 1.55; min-height: 100vh;
}
.aurem-harness-v2 h1, .aurem-harness-v2 h2, .aurem-harness-v2 .serif { font-family: "Playfair Display", Georgia, "Times New Roman", serif; }
.aurem-harness-v2 button, .aurem-harness-v2 input, .aurem-harness-v2 select { font: inherit; }
.aurem-harness-v2 button, .aurem-harness-v2 [role="button"], .aurem-harness-v2 select, .aurem-harness-v2 input:not([type="checkbox"]) { min-height: 2.75rem; }
.aurem-harness-v2 a { color: var(--accent-strong); }
.aurem-harness-v2 section, .aurem-harness-v2 footer, .aurem-harness-v2 .card { scroll-margin-top: 24px; }
.aurem-harness-v2 .wrap { max-width: 1180px; margin: 0 auto; padding: 28px 20px 0; }
.aurem-harness-v2 .split { display: grid; grid-template-columns: 1.05fr 0.95fr; gap: 44px; align-items: start; }
@media (max-width: 860px) { .aurem-harness-v2 .split { grid-template-columns: 1fr; gap: 28px; } }
.aurem-harness-v2 .brand-line { font-size: 0.85rem; font-weight: 700; letter-spacing: 0.22em; text-transform: uppercase; color: var(--accent); margin: 6px 0 16px; }
.aurem-harness-v2 h1 { font-size: clamp(2.3rem, 5.2vw, 3.6rem); line-height: 1.05; margin: 0 0 18px; font-weight: 800; }
.aurem-harness-v2 h1 .gold { color: var(--accent-strong); }
.aurem-harness-v2 .sub { font-size: 1.14rem; color: var(--ink-2); margin: 0 0 28px; max-width: 34rem; }
.aurem-harness-v2 .points { list-style: none; margin: 0 0 28px; padding: 0; display: grid; gap: 16px; }
.aurem-harness-v2 .points li { display: flex; gap: 12px; align-items: flex-start; }
.aurem-harness-v2 .points .tick { flex: 0 0 auto; width: 26px; height: 26px; border-radius: 50%; background: var(--accent-soft); color: var(--accent-strong); display: grid; place-items: center; margin-top: 1px; }
.aurem-harness-v2 .points b { display: block; font-size: 1rem; }
.aurem-harness-v2 .points span { color: var(--ink-2); font-size: 0.95rem; }
.aurem-harness-v2 .score { background: var(--surface); border: 1px solid var(--line); border-top: 4px solid var(--accent); border-radius: var(--radius); padding: 18px 20px; margin-bottom: 18px; }
.aurem-harness-v2 .score .row { display: flex; flex-wrap: wrap; gap: 10px 28px; align-items: baseline; }
.aurem-harness-v2 .score .big { font-size: 1.9rem; font-weight: 800; color: var(--accent-strong); }
.aurem-harness-v2 .score .lbl { color: var(--ink-3); font-size: 0.85rem; }
.aurem-harness-v2 .score p { margin: 8px 0 0; color: var(--ink-2); font-size: 0.92rem; }
.aurem-harness-v2 .release { display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center; color: var(--ink-2); font-size: 0.95rem; }
.aurem-harness-v2 .release .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--accent); display: inline-block; margin-right: 6px; }
.aurem-harness-v2 .card { background: var(--surface); border: 1px solid var(--line); border-radius: 18px; box-shadow: var(--shadow); padding: 26px 26px 22px; position: sticky; top: 20px; }
@media (max-width: 860px) { .aurem-harness-v2 .card { position: static; } }
.aurem-harness-v2 .tabs { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; background: var(--surface-2); border-radius: 10px; padding: 5px; margin-bottom: 22px; }
.aurem-harness-v2 .tabs button { border: 0; background: transparent; color: var(--ink-2); border-radius: 7px; cursor: pointer; font-weight: 600; padding: 8px 6px; }
.aurem-harness-v2 .tabs button[aria-selected="true"] { background: var(--bg); color: var(--ink); box-shadow: 0 2px 8px rgba(0,0,0,.45); }
.aurem-harness-v2 .card h2 { font-size: 1.45rem; margin: 0 0 4px; }
.aurem-harness-v2 .card .hint { color: var(--ink-2); font-size: 0.93rem; margin: 0 0 18px; }
.aurem-harness-v2 .field { margin-bottom: 14px; }
.aurem-harness-v2 .field label { display: block; font-weight: 600; font-size: 0.92rem; margin-bottom: 6px; }
.aurem-harness-v2 .field input { width: 100%; padding: 10px 12px; border-radius: 9px; border: 1px solid var(--line); background: var(--bg); color: var(--ink); }
.aurem-harness-v2 .field input::placeholder { color: var(--ink-3); }
.aurem-harness-v2 .pw-wrap { position: relative; }
.aurem-harness-v2 .pw-wrap input { padding-right: 76px; }
.aurem-harness-v2 .pw-toggle { position: absolute; right: 4px; top: 50%; transform: translateY(-50%); border: 0; background: transparent; color: var(--accent-strong); font-weight: 600; font-size: 0.88rem; cursor: pointer; padding: 6px 10px; min-height: 2.25rem; }
.aurem-harness-v2 .strength { display: none; gap: 5px; margin-top: 8px; }
.aurem-harness-v2 .strength.on { display: flex; }
.aurem-harness-v2 .strength i { height: 5px; flex: 1; border-radius: 3px; background: var(--line); }
.aurem-harness-v2 .strength i.f1 { background: #d9534f; }
.aurem-harness-v2 .strength i.f2 { background: #e0a23c; }
.aurem-harness-v2 .strength i.f3 { background: var(--accent); }
.aurem-harness-v2 .strength-note { font-size: 0.82rem; color: var(--ink-3); margin-top: 5px; min-height: 1em; }
.aurem-harness-v2 .submit { width: 100%; border: 0; border-radius: 10px; cursor: pointer; background: var(--accent); color: var(--accent-ink); font-weight: 700; font-size: 1.02rem; padding: 12px; margin-top: 4px; }
.aurem-harness-v2 .submit:hover, .aurem-harness-v2 .dl-btn:hover { filter: brightness(1.08); }
.aurem-harness-v2 .submit:disabled { opacity: 0.65; cursor: not-allowed; }
.aurem-harness-v2 .legal-note { font-size: 0.82rem; color: var(--ink-3); margin: 12px 0 0; text-align: center; }
.aurem-harness-v2 .alt-row { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px 14px; margin-top: 14px; font-size: 0.92rem; align-items: center; }
.aurem-harness-v2 .alt-row button.link { background: none; border: 0; padding: 6px 2px; cursor: pointer; color: var(--accent-strong); font-weight: 600; min-height: 2.25rem; }
.aurem-harness-v2 .divider { display: flex; align-items: center; gap: 12px; margin: 20px 0 14px; color: var(--ink-3); font-size: 0.85rem; }
.aurem-harness-v2 .divider::before, .aurem-harness-v2 .divider::after { content: ""; flex: 1; height: 1px; background: var(--line); }
.aurem-harness-v2 .oauth { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.aurem-harness-v2 .oauth button { border: 1px solid var(--line); background: var(--surface); color: var(--ink); border-radius: 10px; cursor: pointer; font-weight: 600; padding: 10px; display: flex; align-items: center; justify-content: center; gap: 8px; }
.aurem-harness-v2 .oauth button:hover { border-color: var(--accent); }
.aurem-harness-v2 .form-msg { display: none; border-radius: 10px; padding: 12px 14px; margin-bottom: 16px; font-size: 0.94rem; }
.aurem-harness-v2 .form-msg.show { display: block; }
.aurem-harness-v2 .form-msg.ok { background: var(--ok-bg); color: var(--ok-ink); }
.aurem-harness-v2 .form-msg.bad { background: var(--warn-bg); color: var(--warn-ink); border: 1px solid var(--warn-line); }
.aurem-harness-v2 .done .check { width: 52px; height: 52px; border-radius: 50%; background: var(--ok-bg); color: var(--ok-ink); display: grid; place-items: center; margin-bottom: 14px; }
.aurem-harness-v2 .done h2 { font-size: 1.5rem; margin: 0 0 6px; }
.aurem-harness-v2 .done p { color: var(--ink-2); margin: 0 0 16px; }
.aurem-harness-v2 .dl-btn { display: block; width: 100%; text-align: center; text-decoration: none; background: var(--accent); color: var(--accent-ink); font-weight: 700; border-radius: 10px; padding: 13px; font-size: 1.02rem; margin-bottom: 10px; border: 0; cursor: pointer; }
.aurem-harness-v2 .dl-btn:disabled { opacity: 0.65; cursor: not-allowed; }
.aurem-harness-v2 .done .meta { font-size: 0.86rem; color: var(--ink-3); word-break: break-all; margin-bottom: 16px; }
.aurem-harness-v2 .done .again { background: none; border: 0; color: var(--accent-strong); font-weight: 600; cursor: pointer; padding: 8px 2px; }
.aurem-harness-v2 section.below { margin-top: 64px; }
.aurem-harness-v2 section.below > h2 { font-size: 1.8rem; margin: 0 0 8px; }
.aurem-harness-v2 section.below .lede { color: var(--ink-2); margin: 0 0 24px; max-width: 46rem; }
.aurem-harness-v2 .kicker { font-size: 0.8rem; font-weight: 700; letter-spacing: 0.2em; text-transform: uppercase; color: var(--accent); margin: 0 0 8px; }
.aurem-harness-v2 .feat-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
@media (max-width: 900px) { .aurem-harness-v2 .feat-grid { grid-template-columns: 1fr 1fr; } }
@media (max-width: 560px) { .aurem-harness-v2 .feat-grid { grid-template-columns: 1fr; } }
.aurem-harness-v2 .feat { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 20px; }
.aurem-harness-v2 .feat .ic { width: 42px; height: 42px; border-radius: 10px; background: var(--accent-soft); color: var(--accent-strong); display: grid; place-items: center; margin-bottom: 12px; }
.aurem-harness-v2 .feat b { display: block; font-size: 1.02rem; margin-bottom: 5px; }
.aurem-harness-v2 .feat p { margin: 0; color: var(--ink-2); font-size: 0.93rem; }
.aurem-harness-v2 .flow { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
@media (max-width: 860px) { .aurem-harness-v2 .flow { grid-template-columns: 1fr 1fr; } }
@media (max-width: 520px) { .aurem-harness-v2 .flow { grid-template-columns: 1fr; } }
.aurem-harness-v2 .flow .step { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 18px; }
.aurem-harness-v2 .flow .num { font-size: 0.8rem; font-weight: 800; letter-spacing: 0.1em; color: var(--accent); text-transform: uppercase; margin: 0 0 6px; }
.aurem-harness-v2 .flow b { display: block; margin-bottom: 4px; }
.aurem-harness-v2 .flow p { margin: 0; color: var(--ink-2); font-size: 0.9rem; }
.aurem-harness-v2 .req { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 20px 22px; }
.aurem-harness-v2 .req ul { margin: 0; padding-left: 20px; color: var(--ink-2); display: grid; gap: 7px; }
.aurem-harness-v2 .req b { color: var(--ink); }
.aurem-harness-v2 .faq { display: grid; gap: 10px; }
.aurem-harness-v2 .faq-item { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); overflow: hidden; }
.aurem-harness-v2 .faq-q { width: 100%; display: flex; justify-content: space-between; align-items: center; gap: 14px; background: none; border: 0; color: var(--ink); text-align: left; cursor: pointer; font-weight: 700; font-size: 1rem; padding: 16px 20px; min-height: 3.4rem; }
.aurem-harness-v2 .faq-q .chev { flex: 0 0 auto; color: var(--accent-strong); transition: transform .25s ease; display: grid; place-items: center; }
.aurem-harness-v2 .faq-q[aria-expanded="true"] .chev { transform: rotate(180deg); }
.aurem-harness-v2 .faq-a { max-height: 0; overflow: hidden; transition: max-height .3s ease; }
.aurem-harness-v2 .faq-a .inner { padding: 0 20px 18px; color: var(--ink-2); font-size: 0.95rem; }
.aurem-harness-v2 .faq-a p { margin: 0 0 10px; }
.aurem-harness-v2 .faq-a p:last-child { margin-bottom: 0; }
@media (prefers-reduced-motion: reduce) { .aurem-harness-v2 .faq-a, .aurem-harness-v2 .faq-q .chev { transition: none; } }
.aurem-harness-v2 .legal-grid { display: grid; grid-template-columns: 1fr; gap: 14px; }
.aurem-harness-v2 .legal { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 24px 26px; }
.aurem-harness-v2 .legal h3 { font-family: "Playfair Display", Georgia, serif; font-size: 1.35rem; margin: 0 0 4px; }
.aurem-harness-v2 .legal .updated { color: var(--ink-3); font-size: 0.85rem; margin: 0 0 14px; }
.aurem-harness-v2 .legal h4 { margin: 16px 0 5px; font-size: 0.98rem; color: var(--accent-strong); }
.aurem-harness-v2 .legal p, .aurem-harness-v2 .legal li { color: var(--ink-2); font-size: 0.94rem; }
.aurem-harness-v2 .legal p { margin: 0 0 10px; }
.aurem-harness-v2 .legal ul { margin: 0 0 6px; padding-left: 20px; display: grid; gap: 6px; }
.aurem-harness-v2 .legal b { color: var(--ink); }
.aurem-harness-v2 footer.site { margin-top: 72px; border-top: 1px solid var(--line); background: #0b0c0e; color: var(--ink-2); }
.aurem-harness-v2 .foot-wrap { max-width: 1180px; margin: 0 auto; padding: 44px 20px 30px; }
.aurem-harness-v2 .foot-header { margin: 0 0 32px; padding-bottom: 24px; border-bottom: 1px solid var(--line); }
.aurem-harness-v2 .foot-header p { margin: 0; font-size: 1.02rem; font-weight: 600; color: var(--ink); letter-spacing: 0.01em; }
.aurem-harness-v2 .foot-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 28px; }
@media (max-width: 960px) { .aurem-harness-v2 .foot-grid { grid-template-columns: repeat(3, 1fr); } }
@media (max-width: 640px) { .aurem-harness-v2 .foot-grid { grid-template-columns: 1fr 1fr; } }
@media (max-width: 420px) { .aurem-harness-v2 .foot-grid { grid-template-columns: 1fr; } }
.aurem-harness-v2 .foot-col h3 { font-size: 0.8rem; letter-spacing: 0.16em; text-transform: uppercase; color: var(--accent); margin: 0 0 14px; font-family: "Inter", sans-serif; font-weight: 700; }
.aurem-harness-v2 .foot-col ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 9px; }
.aurem-harness-v2 .foot-col a { color: var(--ink-2); text-decoration: none; font-size: 0.93rem; overflow-wrap: anywhere; }
.aurem-harness-v2 .foot-col a:hover { color: var(--accent-strong); text-decoration: underline; }
.aurem-harness-v2 .foot-bottom { margin-top: 36px; padding-top: 20px; border-top: 1px solid var(--line); display: grid; grid-template-columns: 1fr auto 1fr; gap: 12px 24px; align-items: start; font-size: 0.84rem; color: var(--ink-3); line-height: 1.5; }
.aurem-harness-v2 .foot-bottom .foot-copy { text-align: left; }
.aurem-harness-v2 .foot-bottom .foot-trademark { text-align: center; max-width: 34rem; }
.aurem-harness-v2 .foot-bottom .foot-action { text-align: right; justify-self: end; }
@media (max-width: 860px) {
  .aurem-harness-v2 .foot-bottom { grid-template-columns: 1fr; }
  .aurem-harness-v2 .foot-bottom .foot-copy, .aurem-harness-v2 .foot-bottom .foot-trademark, .aurem-harness-v2 .foot-bottom .foot-action { text-align: left; justify-self: start; max-width: none; }
}
.aurem-harness-v2 .cookie-btn { border: 1px solid var(--line); background: transparent; color: var(--ink-2); border-radius: 8px; cursor: pointer; font-size: 0.86rem; font-weight: 600; padding: 8px 14px; min-height: 2.5rem; white-space: nowrap; }
.aurem-harness-v2 .cookie-btn:hover { border-color: var(--accent); color: var(--accent-strong); }
`;

const CHECK_PATH = "M20 6 9 17l-5-5";
const CHEVRON_PATH = "m6 9 6 6 6-6";

function Tick() {
  return (
    <span className="tick" aria-hidden="true">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round"><path d={CHECK_PATH} /></svg>
    </span>
  );
}

function FeatIcon({ iconHtml }) {
  return (
    <span
      className="ic"
      aria-hidden="true"
      dangerouslySetInnerHTML={{
        __html: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${iconHtml}</svg>`,
      }}
    />
  );
}

function emailOk(v) { return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v); }

function fmtBytes(n) {
  if (!n) return null;
  return n.toLocaleString();
}

function fmtReleaseDate(iso) {
  // published_at is Unix seconds (Python time.time()); Date expects milliseconds.
  if (iso == null || iso === "" || iso === 0) return null;
  const numeric = typeof iso === "number" ? iso : Number(iso);
  const ms = Number.isFinite(numeric) ? (Math.abs(numeric) < 1e12 ? numeric * 1000 : numeric) : Date.parse(iso);
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

export default function HarnessPage() {
  const [flagEnabled, setFlagEnabled] = useState(null);
  const [release, setRelease] = useState({ available: false });
  const releaseDate = fmtReleaseDate(release?.published_at);
  const [authChecked, setAuthChecked] = useState(false);

  const [searchParams] = useSearchParams();
const [view, setView] = useState(searchParams.get("tab") === "signin" ? "signin" : "signup"); // signup | signin | reset | done
  const [formMsg, setFormMsg] = useState(null); // { text, kind }
  const [busy, setBusy] = useState(false);

  const [suName, setSuName] = useState("");
  const [suEmail, setSuEmail] = useState("");
  const [suPhone, setSuPhone] = useState("");
  const [suPass, setSuPass] = useState("");
  const [suShowPw, setSuShowPw] = useState(false);
  const [suErrors, setSuErrors] = useState({});

  const [siEmail, setSiEmail] = useState("");
  const [siPass, setSiPass] = useState("");
  const [siShowPw, setSiShowPw] = useState(false);
  const [siErrors, setSiErrors] = useState({});
  const [mfaState, setMfaState] = useState(null);
  const [mfaCode, setMfaCode] = useState("");

  const [rsEmail, setRsEmail] = useState("");
  const [rsErrors, setRsErrors] = useState({});

  const [doneTitle, setDoneTitle] = useState("");
  const [doneText, setDoneText] = useState("");
  const [downloadState, setDownloadState] = useState("idle"); // idle | downloading | error
  const [downloadError, setDownloadError] = useState("");
  const [downloadSuccess, setDownloadSuccess] = useState(false);
  const [deliveredSha256, setDeliveredSha256] = useState(null);

  const [faqOpen, setFaqOpen] = useState(null);

  useEffect(() => {
    healthApi.get("/harness/page-status")
      .then((r) => setFlagEnabled(r.data?.harness_page_enabled === true))
      .catch(() => setFlagEnabled(false));
    healthApi.get("/harness/release-status")
      .then((r) => setRelease(r.data || { available: false }))
      .catch(() => setRelease({ available: false }));
  }, []);

  // AAO: Aurem Loop JSON-LD (machine-readable-blocks.md). Static block; no aggregateRating.
  useEffect(() => {
    const ld = document.createElement("script");
    ld.type = "application/ld+json";
    ld.id = "ld-aurem-loop";
    ld.text = JSON.stringify(LOOP_JSON_LD);
    document.head.appendChild(ld);
    return () => {
      document.getElementById("ld-aurem-loop")?.remove();
    };
  }, []);

  useEffect(() => {
    const token = getToken();
    if (!token) { setAuthChecked(true); return; }
    api.get("/auth/me")
      .then((r) => {
        const u = r.data?.user;
        // 2026-11-20 - the cached aurem_user must always be the account
      // this token belongs to. It used to be left untouched here, so
      // a previous account stayed cached - and displayed - after
      // signing in as someone else.
      if (u) {
        try {
          setUser({
            user_id: u.user_id,
            email: u.email,
            name: u.name,
            tier: u.tier,
            tokens_remaining: u.tokens_remaining,
            created_at: u.created_at,
          });
        } catch {
          // Cache refresh is best-effort; the done view below still uses the fresh response.
        }
      }
      const first = (u?.name || "").split(" ")[0];
        setDoneTitle(`Welcome back${first ? ", " + first : ""}!`);
        setDoneText("You are signed in and still on the same page — your download is unlocked below, exactly as it should be.");
        setView("done");
      })
      .catch(() => {
      // No valid session - clear any cached user left over from a
      // previous account so the page never shows the wrong account.
      try {
        setUser(null);
      } catch {
        // best-effort
      }
    })
      .finally(() => setAuthChecked(true));
  }, []);

  function switchView(v) {
    setView(v);
    setFormMsg(null);
    setMfaState(null);
    setSuErrors({});
    setSiErrors({});
    setRsErrors({});
  }

  async function handleSignup(e) {
    e.preventDefault();
    const name = suName.trim();
    const email = suEmail.trim();
    const errors = {};
    if (name.length < 2) errors.suName = true;
    if (!emailOk(email)) errors.suEmail = true;
    const strength = scorePassword(suPass);
    if (strength.score < MIN_ACCEPTABLE_SCORE) errors.suPass = true;
    setSuErrors(errors);
    if (Object.keys(errors).length) {
      setFormMsg({ text: "Please fix the highlighted fields above and try again.", kind: "bad" });
      return;
    }
    setBusy(true); setFormMsg(null);
    try {
      const r = await api.post("/auth/signup", {
        email, password: suPass, name,
        phone: suPhone.trim() || undefined,
        source: "harness_homepage",
      });
      setToken(r.data.token);
      setUser({
        user_id: r.data.user_id, email: r.data.email, name: r.data.name,
        tier: r.data.tier, tokens_remaining: r.data.tokens_remaining,
        created_at: r.data.created_at,
      });
      const first = (r.data.name || "there").split(" ")[0];
      setDoneTitle(`Congratulations, ${first} — your account is ready.`);
      setDoneText("Your free account has been created in the ORA account system and your download is unlocked below. The same sign-in works across ORA and Aurem Loop.");
      setView("done");
    } catch (err) {
      setFormMsg({ text: err?.response?.data?.detail || "Sign up failed. Try again.", kind: "bad" });
    } finally {
      setBusy(false);
    }
  }

  async function handleSignin(e) {
    e.preventDefault();
    const email = siEmail.trim();
    const errors = {};
    if (!emailOk(email)) errors.siEmail = true;
    if (!siPass) errors.siPass = true;
    setSiErrors(errors);
    if (Object.keys(errors).length) {
      setFormMsg({ text: "Please check your email and password.", kind: "bad" });
      return;
    }
    setBusy(true); setFormMsg(null);
    try {
      const r = await api.post("/auth/login", { email, password: siPass });
      if (r.data?.mfa_required && r.data?.mfa_token) {
        setMfaState({ mfa_token: r.data.mfa_token, email: r.data.email || email });
        setBusy(false);
        return;
      }
      setToken(r.data.token);
      setUser({
        user_id: r.data.user_id, email: r.data.email, name: r.data.name,
        tier: r.data.tier, tokens_remaining: r.data.tokens_remaining,
      });
      const first = (r.data.name || "").split(" ")[0];
      setDoneTitle(`Welcome back${first ? ", " + first : ""}!`);
      setDoneText("You are signed in and still on the same page — your download is unlocked below, exactly as it should be.");
      setView("done");
    } catch (err) {
      setFormMsg({ text: err?.response?.data?.detail || "Sign in failed. Try again.", kind: "bad" });
    } finally {
      setBusy(false);
    }
  }

  async function handleMfaSubmit(e) {
    e.preventDefault();
    setBusy(true); setFormMsg(null);
    try {
      const r = await api.post("/auth/login/2fa-verify", {
        mfa_token: mfaState.mfa_token,
        code: mfaCode.replace(/\D/g, ""),
      });
      setToken(r.data.token);
      setUser({
        user_id: r.data.user_id, email: r.data.email, name: r.data.name,
        tier: r.data.tier, tokens_remaining: r.data.tokens_remaining,
      });
      const first = (r.data.name || "").split(" ")[0];
      setDoneTitle(`Welcome back${first ? ", " + first : ""}!`);
      setDoneText("You are signed in and still on the same page — your download is unlocked below, exactly as it should be.");
      setView("done");
      setMfaState(null);
    } catch (err) {
      setFormMsg({ text: err?.response?.data?.detail || "Invalid 2FA code. Try again.", kind: "bad" });
    } finally {
      setBusy(false);
    }
  }

  async function handleReset(e) {
    e.preventDefault();
    const email = rsEmail.trim();
    if (!emailOk(email)) {
      setRsErrors({ rsEmail: true });
      setFormMsg({ text: "Please enter the email address you signed up with.", kind: "bad" });
      return;
    }
    setRsErrors({});
    setBusy(true); setFormMsg(null);
    try {
      await api.post("/auth/forgot-password", { email });
      setFormMsg({ text: `If that email exists, a reset link has been sent to ${email}. The link expires in 30 minutes.`, kind: "ok" });
    } catch (err) {
      setFormMsg({ text: err?.response?.data?.detail || "Could not send reset link. Try again.", kind: "bad" });
    } finally {
      setBusy(false);
    }
  }

  function startOAuth(provider) {
    const base = window.location.origin;
    const next = encodeURIComponent("/harness");
    if (provider === "google") {
      window.location.href = `${base}/api/aurem-dev/google/oauth/start?intent=signup&next=${next}`;
      return;
    }
    // GitHub goes straight to github.com, exactly like Google above.
  // The funnel click is fire-and-forget telemetry: it used to be
  // awaited (dynamic import + up to 5 s POST) before the browser
  // left the page, so a slow or failing telemetry call could delay
  // - or, when its promise chain rejected, silently swallow - the
  // OAuth start, and the return then landed on /login?next=/harness
  // still signed out. Navigate first; telemetry must never gate it.
  try {
    import("../lib/githubFunnel")
      .then(({ trackFunnel }) => {
        trackFunnel("cta_click", "harness_homepage", { intent: "signup" }).catch(() => {});
      })
      .catch(() => {});
  } catch {
    // Telemetry is best-effort; the navigation below still happens.
  }
  window.location.href = `${base}/api/aurem-dev/github/oauth/connect?signup=1&intent=signup&next=${next}`;
  }

  async function handleDownload() {
    setDownloadState("downloading"); setDownloadError(""); setDownloadSuccess(false); setDeliveredSha256(null);
    try {
      const resp = await api.post("/harness/download", {}, { responseType: "blob" });
      const delivered = resp.headers?.["x-aurem-package-sha256"] || null;
      if (delivered) setDeliveredSha256(delivered);
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
    } catch (err) {
      const status = err?.response?.status;
      let msg = "Could not start the download — try again.";
      if (status === 401) msg = "You're not logged in — please sign in again.";
      else if (status === 409) msg = "No release is published yet";
      else if (status === 429) msg = "Too many download attempts — try again in a minute.";
      setDownloadError(msg);
      setDownloadState("error");
    }
  }

async function handleDownloadExe() {
  setDownloadState("downloading"); setDownloadError(""); setDownloadSuccess(false); setDeliveredSha256(null);
  try {
    const resp = await api.post("/harness/download-exe", {}, { responseType: "blob" });
    const delivered = resp.headers?.["x-aurem-package-sha256"] || null;
    if (delivered) setDeliveredSha256(delivered);
    const url = URL.createObjectURL(resp.data);
    const a = document.createElement("a");
    a.href = url;
    a.download = `aurem-loop-${release?.version || "latest"}.exe`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    setDownloadSuccess(true);
    setDownloadState("idle");
  } catch (err) {
    const status = err?.response?.status;
    let msg = "Could not start the download — try again.";
    if (status === 401) msg = "You're not logged in — please sign in again.";
    else if (status === 409) msg = "No Windows installer has been attached to the latest release yet — use the .zip download below.";
    else if (status === 429) msg = "Too many download attempts — try again in a minute.";
    setDownloadError(msg);
    setDownloadState("error");
  }
}

  async function signOutAndShowForm() {
  const tok = getToken();
  // Clear the local session and show the sign-in form FIRST, synchronously.
  // 2026-11-20 - the server logout used to be awaited before the clear:
  // on an expired token it 401s, and the api interceptor fires
  // aurem:session-expired before the await resolves, so the App listener
  // bounced to /login?next=/harness and the form never appeared on the
  // first click. The form must appear immediately; the server logout is
  // fire-and-forget below, with the captured token in the header.
  setToken(null);
  setUser(null);
  setDownloadSuccess(false);
  setDeliveredSha256(null);
  switchView("signin");
  if (tok) {
    try {
      api.post("/auth/logout", {}, { headers: { Authorization: `Bearer ${tok}` } }).catch(() => {});
    } catch {
      // best-effort — the local session is already cleared above.
    }
  }
  }

  function openCookiePreferences() {
    try { localStorage.removeItem("aurem_consent"); } catch { /* private mode */ }
    window.dispatchEvent(new CustomEvent("aurem:reopen-consent"));
  }

  if (flagEnabled === null) {
    return (
      <div className="aurem-harness-v2" data-testid="harness-page-loading">
        <style>{CSS}</style>
      </div>
    );
  }
  if (!flagEnabled) {
    return (
      <div className="aurem-harness-v2" data-testid="harness-page-not-available">
        <style>{CSS}</style>
        <div className="wrap"><p style={{ padding: "60px 0" }}>Aurem Loop isn&apos;t available yet.</p></div>
      </div>
    );
  }

  return (
    <div className="aurem-harness-v2" data-testid="harness-page">
      <style>{CSS}</style>
      <div className="wrap">
        <div className="split">
          <div>
            <p className="brand-line">{HERO.brandLine}</p>
            <h1>{HERO.headlineLines.map((line) => <React.Fragment key={line}>{line}<br /></React.Fragment>)}<span className="gold">{HERO.headlineGold}</span></h1>
            <p className="sub">{HERO.sub}</p>
            <ul className="points">
              {POINTS.map((pt) => (
                <li key={pt.b}><Tick /><div><b>{pt.b}</b><span>{pt.t}</span></div></li>
              ))}
            </ul>
            <div className="score" id="bench" data-testid="harness-bench">
              <div className="row">
                <div><span className="big">{BENCH.passed}</span> <span className="lbl">{BENCH.passedLabel}</span></div>
                <div><span className="lbl">{BENCH.bareLabel}</span> <b>{BENCH.bare}</b></div>
                <div><span className="lbl">{BENCH.falseAcceptsLabel}</span> <b>{BENCH.falseAccepts}</b></div>
              </div>
              <p>{BENCH.note}</p>
            </div>
            <p className="release" data-testid="harness-release-line">
              {release?.available ? (
                <>
                  <span><span className="dot" aria-hidden="true"></span>Available now — free download, v{release.version}{releaseDate ? <> · Live since {releaseDate}</> : null}</span>
                  <span>For {release.platform === "windows" ? "Windows" : release.platform}</span>
                  <span>Free with your account</span>
                </>
              ) : (
                <span>Download not available yet</span>
              )}
            </p>
          </div>

          <div className="card" id="download">
            {view !== "done" ? (
              <div id="formView">
                {view !== "reset" && (
                  <div className="tabs" role="tablist" aria-label="Sign up or sign in">
                    <button type="button" role="tab" aria-selected={view === "signup"} data-testid="harness-tab-signup" onClick={() => switchView("signup")}>New user — Sign up</button>
                    <button type="button" role="tab" aria-selected={view === "signin"} data-testid="harness-tab-signin" onClick={() => switchView("signin")}>Existing user — Sign in</button>
                  </div>
                )}
                {formMsg && <div className={`form-msg show ${formMsg.kind}`} role="status" data-testid="harness-form-msg">{formMsg.text}</div>}

                {view === "signup" && (
                  <form onSubmit={handleSignup} noValidate>
                    <h2>Create your free account</h2>
                    <p className="hint">One account for ORA and the Aurem Loop download. It takes less than a minute.</p>
                    <div className="field">
                      <label htmlFor="suName">Full name</label>
                      <input id="suName" data-testid="harness-signup-name" className={suErrors.suName ? "ora-ui-field-error" : undefined} aria-invalid={!!suErrors.suName} aria-describedby="suName-error" type="text" autoComplete="name" placeholder="Your name" value={suName} onChange={(e) => setSuName(e.target.value)} />
                      <FieldError id="suName-error" message={suErrors.suName ? "Please enter your name." : ""} />
                    </div>
                    <div className="field">
                      <label htmlFor="suEmail">Email</label>
                      <input id="suEmail" data-testid="harness-signup-email" className={suErrors.suEmail ? "ora-ui-field-error" : undefined} aria-invalid={!!suErrors.suEmail} aria-describedby="suEmail-error" type="email" autoComplete="email" placeholder="you@example.com" value={suEmail} onChange={(e) => setSuEmail(e.target.value)} />
                      <FieldError id="suEmail-error" message={suErrors.suEmail ? "Please enter a valid email address, like you@example.com." : ""} />
                    </div>
                    <div className="field">
                      <label htmlFor="suPhone">Phone <span style={{ color: "var(--ink-3)", fontWeight: 400 }}>(optional)</span></label>
                      <input id="suPhone" data-testid="harness-signup-phone" type="tel" autoComplete="tel" placeholder="+1 …" value={suPhone} onChange={(e) => setSuPhone(e.target.value)} />
                    </div>
                    <div className="field">
                      <label htmlFor="suPass">Password</label>
                      <div className="pw-wrap">
                        <input id="suPass" data-testid="harness-signup-password" className={suErrors.suPass ? "ora-ui-field-error" : undefined} aria-invalid={!!suErrors.suPass} aria-describedby="suPass-error" type={suShowPw ? "text" : "password"} autoComplete="new-password" placeholder="At least 8 characters" value={suPass} onChange={(e) => setSuPass(e.target.value)} />
                        <button type="button" className="pw-toggle" onClick={() => setSuShowPw((v) => !v)}>{suShowPw ? "Hide" : "Show"}</button>
                      </div>
                      {suPass && (
                        <div className="strength on" aria-hidden="true">
                          {[1, 2, 3].map((i) => <i key={i} className={i <= Math.min(3, Math.max(1, scorePassword(suPass).score - 1)) ? `f${i}` : ""} />)}
                        </div>
                      )}
                      <p className="strength-note">{suPass ? scorePassword(suPass).label : ""}</p>
                      <FieldError id="suPass-error" message={suErrors.suPass ? "Your password must be at least 8 characters long." : ""} />
                    </div>
                    <button className="submit" type="submit" data-testid="harness-signup-submit" disabled={busy}>{busy ? "Creating account…" : "Create account & get the download"}</button>
                    <p className="legal-note">By creating an account you agree to the <a href="#terms">Terms of Use</a> and <a href="#privacy">Privacy Policy</a>.</p>
        <div className="divider">or</div>
        <div className="oauth">
          <button type="button" data-testid="harness-oauth-google" onClick={() => startOAuth("google")}>Continue with Google</button>
          <button type="button" data-testid="harness-oauth-github" onClick={() => startOAuth("github")}>Continue with GitHub</button>
        </div>
                    <div className="alt-row">
                      <span style={{ color: "var(--ink-2)" }}>Already have an account?</span>
                      <button type="button" className="link" onClick={() => switchView("signin")}>Sign in →</button>
                    </div>
                  </form>
                )}

                {view === "signin" && !mfaState && (
                  <form onSubmit={handleSignin} noValidate>
                    <h2>Welcome back</h2>
                    <p className="hint">Sign in and the download unlocks right here on this page — you will not be sent anywhere else.</p>
                    <div className="field">
                      <label htmlFor="siEmail">Email</label>
                      <input id="siEmail" data-testid="harness-signin-email" className={siErrors.siEmail ? "ora-ui-field-error" : undefined} aria-invalid={!!siErrors.siEmail} aria-describedby="siEmail-error" type="email" autoComplete="email" placeholder="you@example.com" value={siEmail} onChange={(e) => setSiEmail(e.target.value)} />
                      <FieldError id="siEmail-error" message={siErrors.siEmail ? "Please enter a valid email address, like you@example.com." : ""} />
                    </div>
                    <div className="field">
                      <label htmlFor="siPass">Password</label>
                      <div className="pw-wrap">
                        <input id="siPass" data-testid="harness-signin-password" className={siErrors.siPass ? "ora-ui-field-error" : undefined} aria-invalid={!!siErrors.siPass} aria-describedby="siPass-error" type={siShowPw ? "text" : "password"} autoComplete="current-password" placeholder="Your password" value={siPass} onChange={(e) => setSiPass(e.target.value)} />
                        <button type="button" className="pw-toggle" onClick={() => setSiShowPw((v) => !v)}>{siShowPw ? "Hide" : "Show"}</button>
                      </div>
                      <FieldError id="siPass-error" message={siErrors.siPass ? "Please enter your password." : ""} />
                    </div>
                    <button className="submit" type="submit" data-testid="harness-signin-submit" disabled={busy}>{busy ? "Signing in…" : "Sign in & get the download"}</button>
                    <div className="alt-row">
                      <button type="button" className="link" data-testid="harness-forgot-btn" onClick={() => switchView("reset")}>Forgot your password?</button>
                      <button type="button" className="link" onClick={() => switchView("signup")}>Create an account →</button>
                    </div>
                    <div className="divider">or</div>
                    <div className="oauth">
                      <button type="button" data-testid="harness-oauth-google" onClick={() => startOAuth("google")}>Continue with Google</button>
                      <button type="button" data-testid="harness-oauth-github" onClick={() => startOAuth("github")}>Continue with GitHub</button>
                    </div>
                  </form>
                )}

                {view === "signin" && mfaState && (
                  <form onSubmit={handleMfaSubmit} noValidate>
                    <h2>Two-factor code</h2>
                    <p className="hint">Enter the 6-digit code from your authenticator app for {mfaState.email}.</p>
                    <div className="field">
                      <label htmlFor="mfaCode">Code</label>
                      <input id="mfaCode" data-testid="harness-mfa-code" type="text" inputMode="numeric" placeholder="123456" value={mfaCode} onChange={(e) => setMfaCode(e.target.value)} />
                    </div>
                    <button className="submit" type="submit" data-testid="harness-mfa-submit" disabled={busy}>{busy ? "Verifying…" : "Verify & sign in"}</button>
                    <div className="alt-row">
                      <button type="button" className="link" onClick={() => setMfaState(null)}>← Back</button>
                    </div>
                  </form>
                )}

                {view === "reset" && (
                  <form onSubmit={handleReset} noValidate>
                    <h2>Reset your password</h2>
                    <p className="hint">Enter the email you signed up with and we will send you a secure reset link.</p>
                    <div className="field">
                      <label htmlFor="rsEmail">Email</label>
                      <input id="rsEmail" data-testid="harness-reset-email" className={rsErrors.rsEmail ? "ora-ui-field-error" : undefined} aria-invalid={!!rsErrors.rsEmail} aria-describedby="rsEmail-error" type="email" autoComplete="email" placeholder="you@example.com" value={rsEmail} onChange={(e) => setRsEmail(e.target.value)} />
                      <FieldError id="rsEmail-error" message={rsErrors.rsEmail ? "Please enter a valid email address, like you@example.com." : ""} />
                    </div>
                    <button className="submit" type="submit" data-testid="harness-reset-submit" disabled={busy}>{busy ? "Sending…" : "Send reset link"}</button>
                    <div className="alt-row">
                      <button type="button" className="link" data-testid="harness-back-to-signin" onClick={() => switchView("signin")}>← Back to sign in</button>
                    </div>
                  </form>
                )}
              </div>
            ) : (
              <div className="done show" data-testid="harness-done-view">
                <div className="check" aria-hidden="true"><svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><path d={CHECK_PATH} /></svg></div>
                <h2 data-testid="harness-done-title">{doneTitle}</h2>
                <p data-testid="harness-done-text">{doneText}</p>
{release?.available ? (
  release.exe_available ? (
    <>
      <button type="button" className="dl-btn" data-testid="harness-download-exe-btn" onClick={handleDownloadExe} disabled={downloadState === "downloading"}>
        {downloadState === "downloading" ? "Starting download…" : `Download for Windows (.exe) — v${release.version}`}
      </button>
      <button type="button" className="again" data-testid="harness-download-zip-link" onClick={handleDownload} disabled={downloadState === "downloading"} style={{ marginTop: 8 }}>
        Advanced: download the .zip instead
      </button>
    </>
  ) : (
    <button type="button" className="dl-btn" data-testid="harness-download-btn" onClick={handleDownload} disabled={downloadState === "downloading"}>
      {downloadState === "downloading" ? "Starting download…" : `Download for Windows — v${release.version}`}
    </button>
  )
) : (
  <p style={{ color: "var(--ink-3)", fontSize: "0.9rem" }}>The download details could not be loaded — please refresh the page and try again.</p>
)}
                {release?.available && (
                  <p className="meta">
                    {release.exe_available ? (
                      <>File: {fmtBytes(release.exe_size_bytes)} bytes · SHA-256: {release.exe_sha256}{releaseDate ? <> · Released {releaseDate}</> : null}<br /></>
                    ) : (
                      <>File: {fmtBytes(release.size_bytes)} bytes · SHA-256: {release.sha256}{releaseDate ? <> · Released {releaseDate}</> : null}<br /></>
                    )}
        Ships qwen2.5-coder:1.5b · Windows · Runs entirely on your PC<br />
                    Verify the SHA-256 after downloading — it must match exactly before you run the installer.
                  </p>
                )}
                {downloadSuccess && (
                  <div data-testid="harness-download-success"><SuccessState title={"\u{1F389} Congratulations! Successfully downloaded"} reference={deliveredSha256 ? "Delivered file SHA-256: " + deliveredSha256 : undefined} />
                  </div>
                )}
                {downloadState === "error" && (
                  <div data-testid="harness-download-error"><ErrorState title="Download failed" message={downloadError} /></div>
                )}
                <button type="button" className="again" data-testid="harness-signout-again" onClick={signOutAndShowForm}>← Sign out and show the form again</button>
              </div>
            )}
          </div>
        </div>

        <section className="below" id="features" aria-labelledby="featH">
          <p className="kicker">Why Aurem Loop</p>
          <h2 id="featH">Built to do real work, on your machine</h2>
          <p className="lede">Most AI coding tools rent you a model in the cloud and send your code to it. Aurem Loop works the other way round: the model lives on your PC, learns from your tasks, and gets measurably better — while your code stays exactly where it belongs.</p>
          <div className="feat-grid">
            {FEAT_ITEMS.map((f) => (
              <div className="feat" key={f.b}>
                <FeatIcon iconHtml={f.iconHtml} />
                <b>{f.b}</b>
                <p>{f.p}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="below" id="how" aria-labelledby="howH">
          <p className="kicker">Getting started</p>
          <h2 id="howH">From sign-up to first run in four steps</h2>
          <p className="lede">Your account is created in the same ORA account system used across Aurem products, so one sign-in works everywhere — and your download unlocks on this page the moment you sign in.</p>
          <div className="flow">
            {FLOW_STEPS.map((s) => (
              <div className="step" key={s.num}><p className="num">{s.num}</p><b>{s.b}</b><p>{s.p}</p></div>
            ))}
          </div>
        </section>

        <section className="below" id="requirements" aria-labelledby="reqH">
          <p className="kicker">Before you download</p>
          <h2 id="reqH">System requirements</h2>
          <div className="req">
            <ul>
              {REQUIREMENTS.map((itemHtml, i) => (
                <li key={i} dangerouslySetInnerHTML={{ __html: itemHtml }} />
              ))}
            </ul>
          </div>
        </section>

<section className="below" id="install" aria-labelledby="installH">
  <p className="kicker">{INSTALL.kicker}</p>
  <h2 id="installH">{INSTALL.title}</h2>
  <p className="lede">{INSTALL.lede}</p>
  <p><b>{INSTALL.oneCommandLabel}</b></p>
  <pre style={{ whiteSpace: "pre-wrap", wordBreak: "break-all", overflowX: "auto" }}>{INSTALL.oneCommand}</pre>
  <p><b>{INSTALL.manualTitle}</b></p>
  <ol>
    {INSTALL.manualSteps.map((stepHtml, i) => (
      <li key={i} dangerouslySetInnerHTML={{ __html: stepHtml }} />
    ))}
  </ol>
</section>

        <section className="below" id="faq" aria-labelledby="faqH">
          <p className="kicker">Questions</p>
          <h2 id="faqH">Frequently asked questions</h2>
          <p className="lede">Everything most people ask before downloading. Tap a question to open the answer.</p>
          <div className="faq">
            {FAQ_ITEMS.map((item, i) => (
              <div className="faq-item" key={item.q}>
                <button type="button" className="faq-q" aria-expanded={faqOpen === i} data-testid={`harness-faq-${i}`} onClick={() => setFaqOpen(faqOpen === i ? null : i)}>
                  {item.q}
                  <span className="chev" aria-hidden="true"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d={CHEVRON_PATH} /></svg></span>
                </button>
                <div className="faq-a" style={{ maxHeight: faqOpen === i ? "420px" : "0px" }}>
                  <div className="inner"><p dangerouslySetInnerHTML={{ __html: item.aHtml }} /></div>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="below" id="legal" aria-labelledby="legalH">
          <p className="kicker">The fine print, in plain English</p>
          <h2 id="legalH">Licensing &amp; policies</h2>
          <p className="lede">Short, readable summaries of the rules that govern Aurem Loop. They are written to be understood, not to hide anything.</p>
          <div className="legal-grid">
            {LEGAL_ARTICLES.map((art) => (
              <article className="legal" id={art.id} data-testid={`harness-legal-${art.id}`} key={art.id}>
                <h3>{art.title}</h3>
                <p className="updated">{art.updated}</p>
                <div dangerouslySetInnerHTML={{ __html: art.bodyHtml }} />
              </article>
            ))}
          </div>
        </section>
      </div>

      <footer className="site" data-testid="harness-footer">
        <div className="foot-wrap">
          <div className="foot-header"><p>{FOOTER_TAGLINE}</p></div>
          <div className="foot-grid">
            {FOOTER_COLS.map((col) => (
              <nav className="foot-col" aria-label={col.label} key={col.label}>
                <h3>{col.label}</h3>
                <ul>
                  {col.links.map((l) => (
                    <li key={l.t}><a href={l.href}>{l.t}</a></li>
                  ))}
                </ul>
              </nav>
            ))}
          </div>
          <div className="foot-bottom">
            <span className="foot-copy">{FOOTER_COPY}</span>
            <span className="foot-trademark">{FOOTER_TRADEMARK}</span>
            <span className="foot-action">
              <button type="button" className="cookie-btn" data-testid="harness-cookie-prefs" onClick={openCookiePreferences}>Cookie preferences</button>
            </span>
          </div>
        </div>
      </footer>
    </div>
  );
}
