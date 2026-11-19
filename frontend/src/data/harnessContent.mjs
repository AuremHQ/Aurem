/**
 * harnessContent.mjs — single source of truth for /harness (the
 * Midnight Gold homepage, 2026-11-17 rebuild).
 *
 * Imported by BOTH the React page (pages/HarnessPage.jsx) AND
 * scripts/seo-prerender.mjs, so the crawler-facing static snapshot
 * can never drift from what a real visitor sees — same pattern as
 * competitors.mjs / companyInfo.mjs / developerInfo.mjs. Pure data —
 * no JSX, no React imports. Fields ending in `Html` are pre-built
 * HTML strings (bold/link markup only, no user input, no React
 * event handlers) so the exact same value can be dropped into a
 * React `dangerouslySetInnerHTML` and into the static generator's
 * plain string interpolation with zero duplication.
 *
 * The bench numbers (46/48 vs 31/48 and 33/48, 0 false accepts) and
 * the qwen2.5-coder:1.5b model name are founder-confirmed real facts (source:
 * PC bench run 2026-11-15, 216/216 rows) — NOT sourced from the
 * private AuremHQ/aurem-loop content repo, which this workspace
 * cannot edit and which still shows an older run. See
 * HarnessPage.jsx's own module docstring for the full history.
 */

export const HERO = {
  brandLine: "Aurem Loop",
  headlineLines: ["YOUR CODE.", "YOUR PC."],
  headlineGold: "YOUR MODEL.",
  sub: "Aurem Loop is a coding harness that runs entirely on your own computer. Its built-in weight-space refine loop makes your local model better at your work with every run — no cloud, no per-run charges, no code leaving your machine.",
};

export const POINTS = [
  { b: "Runs 100% on your PC", t: "Installs on Windows and works fully offline. Your code, prompts and files never leave your machine." },
  { b: "A free local model, built in", t: "Ships qwen2.5-coder:1.5b via Ollama — a small model that runs entirely on your PC." },
  { b: "It improves itself — safely", t: "When a task fails, the refine loop trains a small weight edit. The edit is applied only if it passes a strict quality gate, it is always saved as a new copy of the model, and rollback restores the previous model exactly." },
  { b: "One account for everything", t: "Create your free account right here. The same account signs you in to ORA and unlocks the Aurem Loop download on this page — no detours, no redirects." },
];

export const BENCH = {
  passed: "46/48",
  passedLabel: "tasks passed with Aurem Loop",
  bareLabel: "same model without the harness:",
  bare: "31/48",
  falseAcceptsLabel: "false accepts:",
  falseAccepts: "0/48",
  note: "On our 48-task coding benchmark, the harness lifts the same local model from 31 to 46 correct tasks — and it never waves through broken work: zero false accepts.",
};

// Icon bodies are raw inner-<svg> markup (paths only) — wrapped in an
// <svg> by the caller. Static, developer-authored, zero user input.
export const FEAT_ITEMS = [
  {
    iconHtml: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M2 20h20"/>',
    b: "Fully local, fully offline",
    p: "The harness, the model and your project all run on your Windows PC. Once installed, it works with no internet connection at all.",
  },
  {
    iconHtml: '<path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/>',
    b: "Weight-space refine loop",
    p: "Failed tasks trigger a targeted weight edit on the failing behaviour only. A strict gate must pass before anything is applied — and the same failure is never retried twice.",
  },
  {
    iconHtml: '<path d="M12 22s8-3.6 8-10V5l-8-3-8 3v7c0 6.4 8 10 8 10z"/><path d="m9 12 2 2 4-4"/>',
    b: "Nothing ships without proof",
    p: "Every run is checked before it is accepted. On the 48-task benchmark the harness records zero false accepts — broken work is rejected, not waved through.",
  },
  {
    iconHtml: '<path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/>',
    b: "Rollback, always",
    p: "An improved model is always saved as a new copy. If anything ever looks wrong, one command restores the previous model exactly as it was.",
  },
  {
    iconHtml: '<rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
    b: "Your data stays yours",
    p: "Telemetry is off by default and strictly opt-in. Even when switched on, it sends usage summaries only — never your code, your prompts, or your file names.",
  },
  {
    iconHtml: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/>',
    b: "Saves time and tokens",
    p: "A small local model with a smart harness means fewer turns, less rework and no per-token meter running while you code.",
  },
];

export const FLOW_STEPS = [
  { num: "Step 1", b: "Create your free account", p: "Sign up with the form on this page, or sign in if you already have an ORA account. Google and GitHub sign-in are supported too." },
  { num: "Step 2", b: "Download & install", p: "Download Aurem Loop v0.8.0 for Windows. The installer sets up everything for you, including Ollama and the qwen2.5-coder:1.5b model." },
  { num: "Step 3", b: "Run tasks on your PC", p: "Give the harness a coding task. It plans, writes and checks the work locally — nothing is uploaded, and it keeps working offline." },
  { num: "Step 4", b: "Watch it get better", p: "When a task fails, the refine loop learns from that exact failure behind a strict quality gate, so the same mistake does not happen again." },
];

export const REQUIREMENTS = [
  '<b>Operating system:</b> Windows (the harness is PC-only; there is no native mobile version).',
  '<b>Installer includes:</b> Python, Ollama and the qwen2.5-coder:1.5b local model — no separate setup needed.',
  '<b>Internet:</b> needed once to download the installer and the model; after that, Aurem Loop runs fully offline.',
  '<b>Account:</b> a free ORA account (created on this page) is required to download.',
  '<b>Phone access (optional):</b> you can open the harness front screen from your phone’s browser through a secure Cloudflare Tunnel while your PC stays on — sign-in is protected by your account.',
];

export const INSTALL = {
  kicker: "Install",
  title: "Install in one command",
  lede: "Download the zip or run the one-line installer — then start the harness.",
  oneCommandLabel: "Install in one command (PowerShell):",
  oneCommand:
    `powershell -NoProfile -ExecutionPolicy Bypass -c "irm https://auremcto.com/install/aurem-loop.ps1 | iex"`,
  manualTitle: "Or the manual way:",
  manualSteps: [
    "Download the zip and extract it to <code>aurem-loop-0.8.0</code>.",
    "Double-click <code>install.bat</code> — it checks Python 3.11+ and Ollama, asks before downloading qwen2.5-coder:1.5b, creates your token and the AuremLoop scheduled task.",
    `Run <code>loop.py serve</code> and open <a href="http://localhost:8899">http://localhost:8899</a>`, 
  ],
};

export const FAQ_ITEMS = [
  {
    q: "Is Aurem Loop really free?",
    aHtml: "Yes. The local harness is free to download and use on your own PC, and local runs cost nothing — the model runs on your hardware, so there are no per-run or per-token charges. An ORA subscription is only needed for optional extras, such as cloud-model access through ORA.",
  },
  {
    q: "Does my code leave my computer?",
    aHtml: "No. In local mode everything — your code, your prompts, your files and the model itself — stays on your PC, and the harness works fully offline. Telemetry is off by default; if you choose to switch it on, it sends anonymous usage summaries only, never code, prompts or file names. See the Privacy Policy below for the full details.",
  },
  {
    q: "Which model does Aurem Loop use?",
    aHtml: "It runs qwen2.5-coder:1.5b, a small open-weight coding model, served locally by Ollama. The installer downloads and configures both for you. A small model is the point: it is fast and free on your own hardware, and the harness around it is what closes the quality gap.",
  },
  {
    q: "How does the refine loop make the model better?",
    aHtml: 'When a task fails in a way the harness cannot fix in code, a weight-edit job targets that one failing behaviour. The edit is kept only if it passes a strict gate — it must improve the failing score without damaging others. The improved model is always saved as a new copy, the same failure is never retried, and rollback restores the earlier model exactly. The underlying technique is published as the free, open-source <a href="https://github.com/AuremHQ/weight-space-programming" target="_blank" rel="noopener noreferrer">wsp-kit</a>.',
  },
  {
    q: "How well does it actually perform?",
    aHtml: "On our 48-task coding benchmark, the same local model passes <b>46 of 48</b> tasks with Aurem Loop, versus 31 of 48 on its own — with <b>0 false accepts</b>, meaning work that fails its checks is never reported as done. We publish the numbers we measure, including the failures.",
  },
  {
    q: "Is my Aurem Loop account the same as my ORA account?",
    aHtml: 'Yes — there is one account system for all Aurem products. The account you create on this page signs you in to ORA as well, and if you already have an ORA account, just use the “Existing user — Sign in” tab. Your account appears in the same ORA admin system with its source recorded as the Aurem Loop homepage.',
  },
  {
    q: "Can I use Aurem Loop on my phone or on a Mac?",
    aHtml: "The harness itself is Windows PC-only — there is no native mobile or Mac version. You can, however, open its front screen from your phone's browser through a secure Cloudflare Tunnel while your PC is on and signed in, so you can check runs and approve work from anywhere.",
  },
  {
    q: "What if an update or a weight edit makes things worse?",
    aHtml: "It cannot silently overwrite your model: every improved model is a separate copy, and a rollback restores the previous model exactly. Weight edits that fail the quality gate are discarded automatically and are never applied.",
  },
];

export const LEGAL_ARTICLES = [
  {
    id: "license",
    title: "Software Licence",
    updated: "Applies to Aurem Loop v0.8.0 · Last updated 18 November 2026",
    bodyHtml: `
<h4>Aurem Loop</h4>
<p>Aurem Loop is proprietary software owned by Aurem. With a free ORA account you are granted a personal, non-exclusive, non-transferable licence to:</p>
<ul>
<li>Download, install and run Aurem Loop on Windows PCs that you own or control.</li>
<li>Use it for personal and commercial coding work, including work you sell to clients.</li>
<li>Keep and use every model copy the refine loop creates on your machine, for as long as you use Aurem Loop.</li>
</ul>
<p>You may not redistribute, resell, sublicense or reverse-engineer the Aurem Loop software itself, or remove its licence and attribution notices. The software is provided “as is”, without warranties of any kind, to the maximum extent permitted by law.</p>
<h4>wsp-kit (open source)</h4>
<p>The weight-space programming kit behind the refine loop is free and open source under the <b>MIT Licence</b> — you may use, copy, modify and distribute it, including commercially, provided the copyright and licence notices are kept. Source: <a href="https://github.com/AuremHQ/weight-space-programming" target="_blank" rel="noopener noreferrer">github.com/AuremHQ/weight-space-programming</a>.</p>
<h4>Models and third-party components</h4>
<p>The qwen2.5-coder:1.5b model is an open-weight model used under its own licence terms, and Ollama is used under its open-source licence. Those licences travel with the components and are included in the installer.</p>
`.trim(),
  },
  {
    id: "privacy",
    title: "Privacy Policy",
    updated: "Last updated 18 November 2026",
    bodyHtml: `
<h4>What we collect</h4>
<ul>
<li><b>Account details</b> — your name, email address and, if you provide it, phone number, so we can create and secure your account.</li>
<li><b>Download records</b> — which release you downloaded and when, so we can support you and measure adoption.</li>
</ul>
<h4>What never leaves your PC</h4>
<p>Your code, prompts, project files and local models stay on your machine. Aurem Loop’s local mode works fully offline and sends none of this data anywhere.</p>
<h4>Telemetry (opt-in only)</h4>
<p>Telemetry is <b>off by default</b>. If you switch it on, the harness sends anonymous usage summaries — such as run counts and pass/fail results — to help us improve reliability. It never sends code, prompts or file names, and you can switch it off at any time.</p>
<h4>How we use your information</h4>
<p>We use account information only to operate your account, provide downloads and support, and send service messages. <b>We do not sell your personal information.</b> Account data is stored in the same ORA account system used by auremcto.com and is handled in line with Canada’s PIPEDA and, where applicable, the GDPR.</p>
<h4>Your choices</h4>
<p>You can ask us to export or delete your account data at any time through the contact options on <a href="https://auremcto.com" target="_blank" rel="noopener noreferrer">auremcto.com</a>. Deleting your account removes your sign-in and download access but does not affect software already installed on your PC.</p>
`.trim(),
  },
  {
    id: "terms",
    title: "Terms of Use",
    updated: "Last updated 18 November 2026",
    bodyHtml: `
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
<p>Aurem Loop is provided “as is”. We work hard to keep releases stable and gated, but we do not guarantee uninterrupted operation, and our liability is limited to the maximum extent permitted by law. These terms are governed by the laws of Ontario, Canada.</p>
`.trim(),
  },
];

export const FOOTER_COLS = [
  { label: "Product", links: [
    { t: "Pricing", href: "https://auremcto.com/pricing" },
    { t: "Compare", href: "https://auremcto.com/compare" },
    { t: "Sign in", href: "https://auremcto.com/login" },
  ] },
  { label: "Developers", links: [
    { t: "Developer Portal", href: "https://developers.auremcto.com" },
    { t: "API Docs", href: "https://developers.auremcto.com/docs" },
    { t: "OpenAPI Spec", href: "https://developers.auremcto.com/openapi.json" },
    { t: "llms.txt", href: "https://auremcto.com/llms.txt" },
  ] },
  { label: "Legal", links: [
    { t: "Privacy Policy", href: "https://auremcto.com/privacy" },
    { t: "Terms of Service", href: "https://auremcto.com/terms" },
    { t: "Cookie Policy", href: "https://auremcto.com/cookies" },
    { t: "Refund & Billing", href: "https://auremcto.com/refund-billing" },
    { t: "Acceptable Use", href: "https://auremcto.com/acceptable-use" },
    { t: "Data Processing (DPA)", href: "https://auremcto.com/dpa" },
  ] },
  { label: "Trust", links: [
    { t: "About", href: "https://auremcto.com/about" },
    { t: "AI & Code Processing", href: "https://auremcto.com/ai-code-processing" },
    { t: "Subprocessors", href: "https://auremcto.com/subprocessors" },
    { t: "Security", href: "https://auremcto.com/security" },
    { t: "System Status", href: "https://auremcto.com/status" },
  ] },
  { label: "Contact", links: [
    { t: "Support", href: "https://auremcto.com/support" },
    { t: "Privacy", href: "https://auremcto.com/support" },
    { t: "security@auremcto.com", href: "mailto:security@auremcto.com" },
    { t: "billing@auremcto.com", href: "mailto:billing@auremcto.com" },
  ] },
];

export const FOOTER_TAGLINE = "Aurem Loop — Built for developers · MIT extension";
export const FOOTER_COPY = "© 2026 Polaris Built Inc — Aurem Loop, made by AUREM · Incorporated in Canada · All rights reserved.";
export const FOOTER_TRADEMARK = "AUREM™ is a trademark of Polaris Built Inc. (Application No. 2492318, filed with the Canadian Intellectual Property Office).";
