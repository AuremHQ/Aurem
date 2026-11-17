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
import { scorePassword, MIN_ACCEPTABLE_SCORE } from "../lib/passwordStrength";
import {
  HERO, POINTS, BENCH, FEAT_ITEMS, FLOW_STEPS, REQUIREMENTS, FAQ_ITEMS,
  LEGAL_ARTICLES, FOOTER_COLS, FOOTER_TAGLINE, FOOTER_COPY, FOOTER_TRADEMARK,
} from "../data/harnessContent.mjs";

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
.aurem-harness-v2 .field .err-msg { display: none; color: var(--err); font-size: 0.85rem; margin-top: 5px; }
.aurem-harness-v2 .field.invalid input { border-color: var(--err); }
.aurem-harness-v2 .field.invalid .err-msg { display: block; }
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

export default function HarnessPage() {
  const [flagEnabled, setFlagEnabled] = useState(null);
  const [release, setRelease] = useState({ available: false });
  const [authChecked, setAuthChecked] = useState(false);

  const [view, setView] = useState("signup"); // signup | signin | reset | done
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

  useEffect(() => {
    const token = getToken();
    if (!token) { setAuthChecked(true); return; }
    api.get("/auth/me")
      .then((r) => {
        const u = r.data?.user;
        const first = (u?.name || "").split(" ")[0];
        setDoneTitle(`Welcome back${first ? ", " + first : ""}!`);
        setDoneText("You are signed in and still on the same page — your download is unlocked below, exactly as it should be.");
        setView("done");
      })
      .catch(() => { /* no valid session — stay on the sign-up/sign-in form */ })
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
    import("../lib/githubFunnel")
      .then(({ trackFunnel, withFunnelParams }) =>
        trackFunnel("cta_click", "harness_homepage", { intent: "signup" })
          .catch(() => {})
          .then(() => {
            window.location.href = withFunnelParams(
              `${base}/api/aurem-dev/github/oauth/connect?signup=1&next=${next}`,
              "harness_homepage",
            );
          }))
      .catch(() => {
        window.location.href = `${base}/api/aurem-dev/github/oauth/connect?signup=1&next=${next}`;
      });
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
      else if (status === 409) msg = "No release is published yet — check back shortly.";
      else if (status === 429) msg = "Too many download attempts — try again in a minute.";
      setDownloadError(msg);
      setDownloadState("error");
    }
  }

  async function signOutAndShowForm() {
    const tok = getToken();
    if (tok) {
      try {
        await api.post("/auth/logout");
      } catch {
        // best-effort — the local token clear below is what actually matters.
      }
    }
    setToken(null);
    setUser(null);
    setDownloadSuccess(false);
    setDeliveredSha256(null);
    switchView("signin");
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
            <p className="brand-line">Aurem Loop</p>
            <h1>YOUR CODE.<br />YOUR PC.<br /><span className="gold">YOUR MODEL.</span></h1>
            <p className="sub">Aurem Loop is a coding harness that runs entirely on your own computer. Its built-in weight-space refine loop makes your local model better at your work with every run — no cloud, no per-run charges, no code leaving your machine.</p>
            <ul className="points">
              {POINTS.map((pt) => (
                <li key={pt.b}><Tick /><div><b>{pt.b}</b><span>{pt.t}</span></div></li>
              ))}
            </ul>
            <div className="score" id="bench" data-testid="harness-bench">
              <div className="row">
                <div><span className="big">46/48</span> <span className="lbl">tasks passed with Aurem Loop</span></div>
                <div><span className="lbl">same model without the harness:</span> <b>31/48</b></div>
                <div><span className="lbl">false accepts:</span> <b>0/48</b></div>
              </div>
              <p>On our 48-task coding benchmark, the harness lifts the same local model from 31 to 46 correct tasks — and it never waves through broken work: zero false accepts.</p>
            </div>
            <p className="release" data-testid="harness-release-line">
              {release?.available ? (
                <>
                  <span><span className="dot" aria-hidden="true"></span>Download available — v{release.version}</span>
                  <span>For {release.platform === "windows" ? "Windows" : release.platform}</span>
                  <span>Free with your account</span>
                </>
              ) : (
                <span>Download not available yet — check back shortly.</span>
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
                    <div className={`field${suErrors.suName ? " invalid" : ""}`}>
                      <label htmlFor="suName">Full name</label>
                      <input id="suName" data-testid="harness-signup-name" type="text" autoComplete="name" placeholder="Your name" value={suName} onChange={(e) => setSuName(e.target.value)} />
                      <p className="err-msg">Please enter your name.</p>
                    </div>
                    <div className={`field${suErrors.suEmail ? " invalid" : ""}`}>
                      <label htmlFor="suEmail">Email</label>
                      <input id="suEmail" data-testid="harness-signup-email" type="email" autoComplete="email" placeholder="you@example.com" value={suEmail} onChange={(e) => setSuEmail(e.target.value)} />
                      <p className="err-msg">Please enter a valid email address, like you@example.com.</p>
                    </div>
                    <div className="field">
                      <label htmlFor="suPhone">Phone <span style={{ color: "var(--ink-3)", fontWeight: 400 }}>(optional)</span></label>
                      <input id="suPhone" data-testid="harness-signup-phone" type="tel" autoComplete="tel" placeholder="+1 …" value={suPhone} onChange={(e) => setSuPhone(e.target.value)} />
                    </div>
                    <div className={`field${suErrors.suPass ? " invalid" : ""}`}>
                      <label htmlFor="suPass">Password</label>
                      <div className="pw-wrap">
                        <input id="suPass" data-testid="harness-signup-password" type={suShowPw ? "text" : "password"} autoComplete="new-password" placeholder="At least 8 characters" value={suPass} onChange={(e) => setSuPass(e.target.value)} />
                        <button type="button" className="pw-toggle" onClick={() => setSuShowPw((v) => !v)}>{suShowPw ? "Hide" : "Show"}</button>
                      </div>
                      {suPass && (
                        <div className="strength on" aria-hidden="true">
                          {[1, 2, 3].map((i) => <i key={i} className={i <= Math.min(3, Math.max(1, scorePassword(suPass).score - 1)) ? `f${i}` : ""} />)}
                        </div>
                      )}
                      <p className="strength-note">{suPass ? scorePassword(suPass).label : ""}</p>
                      <p className="err-msg">Your password must be at least 8 characters long.</p>
                    </div>
                    <button className="submit" type="submit" data-testid="harness-signup-submit" disabled={busy}>{busy ? "Creating account…" : "Create account & get the download"}</button>
                    <p className="legal-note">By creating an account you agree to the <a href="#terms">Terms of Use</a> and <a href="#privacy">Privacy Policy</a>.</p>
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
                    <div className={`field${siErrors.siEmail ? " invalid" : ""}`}>
                      <label htmlFor="siEmail">Email</label>
                      <input id="siEmail" data-testid="harness-signin-email" type="email" autoComplete="email" placeholder="you@example.com" value={siEmail} onChange={(e) => setSiEmail(e.target.value)} />
                      <p className="err-msg">Please enter a valid email address, like you@example.com.</p>
                    </div>
                    <div className={`field${siErrors.siPass ? " invalid" : ""}`}>
                      <label htmlFor="siPass">Password</label>
                      <div className="pw-wrap">
                        <input id="siPass" data-testid="harness-signin-password" type={siShowPw ? "text" : "password"} autoComplete="current-password" placeholder="Your password" value={siPass} onChange={(e) => setSiPass(e.target.value)} />
                        <button type="button" className="pw-toggle" onClick={() => setSiShowPw((v) => !v)}>{siShowPw ? "Hide" : "Show"}</button>
                      </div>
                      <p className="err-msg">Please enter your password.</p>
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
                    <div className={`field${rsErrors.rsEmail ? " invalid" : ""}`}>
                      <label htmlFor="rsEmail">Email</label>
                      <input id="rsEmail" data-testid="harness-reset-email" type="email" autoComplete="email" placeholder="you@example.com" value={rsEmail} onChange={(e) => setRsEmail(e.target.value)} />
                      <p className="err-msg">Please enter a valid email address, like you@example.com.</p>
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
                  <button type="button" className="dl-btn" data-testid="harness-download-btn" onClick={handleDownload} disabled={downloadState === "downloading"}>
                    {downloadState === "downloading" ? "Starting download…" : `Download for Windows — v${release.version}`}
                  </button>
                ) : (
                  <p style={{ color: "var(--ink-3)", fontSize: "0.9rem" }}>No release is published yet — check back shortly.</p>
                )}
                {release?.available && (
                  <p className="meta">
                    File size: {fmtBytes(release.size_bytes)} bytes · SHA-256: {release.sha256}<br />
                    Verify the SHA-256 after downloading — it must match exactly before you run the installer.
                  </p>
                )}
                {downloadSuccess && (
                  <div className="form-msg show ok" data-testid="harness-download-success">
                    🎊 Congratulations! Successfully downloaded
                    {deliveredSha256 && <><br /><span style={{ fontSize: "0.82rem", wordBreak: "break-all" }}>Delivered file SHA-256: {deliveredSha256}</span></>}
                  </div>
                )}
                {downloadState === "error" && (
                  <div className="form-msg show bad" data-testid="harness-download-error">{downloadError}</div>
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
                <FeatIcon>{f.icon}</FeatIcon>
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
              <li><b>Operating system:</b> Windows (the harness is PC-only; there is no native mobile version).</li>
              <li><b>Installer includes:</b> Python, Ollama and the qwen3:8b local model — no separate setup needed.</li>
              <li><b>Internet:</b> needed once to download the installer and the model; after that, Aurem Loop runs fully offline.</li>
              <li><b>Account:</b> a free ORA account (created on this page) is required to download.</li>
              <li><b>Phone access (optional):</b> you can open the harness front screen from your phone&apos;s browser through a secure Cloudflare Tunnel while your PC stays on — sign-in is protected by your account.</li>
            </ul>
          </div>
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
                  <div className="inner"><p>{item.aNode || item.a}</p></div>
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
            <article className="legal" id="license" data-testid="harness-legal-license">
              <h3>Software Licence</h3>
              <p className="updated">Applies to Aurem Loop v0.8.0 · Last updated 16 November 2026</p>
              <h4>Aurem Loop</h4>
              <p>Aurem Loop is proprietary software owned by Aurem. With a free ORA account you are granted a personal, non-exclusive, non-transferable licence to:</p>
              <ul>
                <li>Download, install and run Aurem Loop on Windows PCs that you own or control.</li>
                <li>Use it for personal and commercial coding work, including work you sell to clients.</li>
                <li>Keep and use every model copy the refine loop creates on your machine, for as long as you use Aurem Loop.</li>
              </ul>
              <p>You may not redistribute, resell, sublicense or reverse-engineer the Aurem Loop software itself, or remove its licence and attribution notices. The software is provided &ldquo;as is&rdquo;, without warranties of any kind, to the maximum extent permitted by law.</p>
              <h4>wsp-kit (open source)</h4>
              <p>The weight-space programming kit behind the refine loop is free and open source under the <b>MIT Licence</b> — you may use, copy, modify and distribute it, including commercially, provided the copyright and licence notices are kept. Source: <a href="https://github.com/AuremHQ/weight-space-programming" target="_blank" rel="noopener noreferrer">github.com/AuremHQ/weight-space-programming</a>.</p>
              <h4>Models and third-party components</h4>
              <p>The qwen3:8b model is an open-weight model used under its own licence terms, and Ollama is used under its open-source licence. Those licences travel with the components and are included in the installer.</p>
            </article>
            <article className="legal" id="privacy" data-testid="harness-legal-privacy">
              <h3>Privacy Policy</h3>
              <p className="updated">Last updated 16 November 2026</p>
              <h4>What we collect</h4>
              <ul>
                <li><b>Account details</b> — your name, email address and, if you provide it, phone number, so we can create and secure your account.</li>
                <li><b>Download records</b> — which release you downloaded and when, so we can support you and measure adoption.</li>
              </ul>
              <h4>What never leaves your PC</h4>
              <p>Your code, prompts, project files and local models stay on your machine. Aurem Loop&apos;s local mode works fully offline and sends none of this data anywhere.</p>
              <h4>Telemetry (opt-in only)</h4>
              <p>Telemetry is <b>off by default</b>. If you switch it on, the harness sends anonymous usage summaries — such as run counts and pass/fail results — to help us improve reliability. It never sends code, prompts or file names, and you can switch it off at any time.</p>
              <h4>How we use your information</h4>
              <p>We use account information only to operate your account, provide downloads and support, and send service messages. <b>We do not sell your personal information.</b> Account data is stored in the same ORA account system used by auremcto.com and is handled in line with Canada&apos;s PIPEDA and, where applicable, the GDPR.</p>
              <h4>Your choices</h4>
              <p>You can ask us to export or delete your account data at any time through the contact options on <a href="https://auremcto.com" target="_blank" rel="noopener noreferrer">auremcto.com</a>. Deleting your account removes your sign-in and download access but does not affect software already installed on your PC.</p>
            </article>
            <article className="legal" id="terms" data-testid="harness-legal-terms">
              <h3>Terms of Use</h3>
              <p className="updated">Last updated 16 November 2026</p>
              <h4>Your account</h4>
              <ul>
                <li>You are responsible for keeping your password secure and for activity under your account.</li>
                <li>One account per person; do not share accounts or create accounts for others without their consent.</li>
                <li>You must provide accurate information when you sign up.</li>
              </ul>
              <h4>Acceptable use</h4>
              <ul>
                <li>Use Aurem Loop only for lawful work. Do not use it to build malware, infringe intellectual-property rights, or break the terms of third-party services you connect to it.</li>
                <li>Code the harness produces is yours to use; you are responsible for reviewing it before you ship it, just as with any tool.</li>
              </ul>
              <h4>Subscriptions</h4>
              <p>The local harness is free. Paid ORA plans are optional, are billed through ORA, and unlock cloud extras only — cancelling a plan never removes your free local harness or your local models.</p>
              <h4>Availability and liability</h4>
              <p>Aurem Loop is provided &ldquo;as is&rdquo;. We work hard to keep releases stable and gated, but we do not guarantee uninterrupted operation, and our liability is limited to the maximum extent permitted by law. These terms are governed by the laws of Ontario, Canada.</p>
            </article>
          </div>
        </section>
      </div>

      <footer className="site" data-testid="harness-footer">
        <div className="foot-wrap">
          <div className="foot-header"><p>Aurem Loop — Built for developers · MIT extension</p></div>
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
            <span className="foot-copy">© 2026 Polaris Built Inc — Aurem Loop, made by AUREM · Incorporated in Canada · All rights reserved.</span>
            <span className="foot-trademark">AUREM™ is a trademark of Polaris Built Inc. (Application No. 2492318, filed with the Canadian Intellectual Property Office).</span>
            <span className="foot-action">
              <button type="button" className="cookie-btn" data-testid="harness-cookie-prefs" onClick={openCookiePreferences}>Cookie preferences</button>
            </span>
          </div>
        </div>
      </footer>
    </div>
  );
}
