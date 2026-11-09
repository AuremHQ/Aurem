/**
 * harnessPage.mjs — single source of truth for /harness.
 * Imported by BOTH HarnessPage.jsx and scripts/seo-prerender.mjs, so
 * the crawler-facing static snapshot can never drift from what a real
 * visitor sees (same pattern as companyInfo.mjs / competitors.mjs).
 * Pure data — no JSX, no React imports.
 *
 * Honesty policy (do not loosen without re-checking the live status
 * endpoint and the actual scoreboard):
 *   - No "Local mode / point at Ollama / LM Studio / llama.cpp" claim
 *     — that feature is flagged not_built on the live status endpoint.
 *   - No benchmark pass-rate number — not independently verified here.
 *   - No claim that Loop C has produced real weight changes.
 *   - No claim that the cloud model option currently works — it is
 *     shown as a locked, future subscriber extra, never "available".
 *   - Exactly one package for everyone; free vs. subscriber is never
 *     presented as two different downloads.
 */

export const HARNESS = {
  title: "Aurem Loop — local coding harness",
  description:
    "A local coding harness that checks its own work. Free to " +
    "download with a signup, runs on your machine.",
  canonical: "https://auremcto.com/harness",

  heroLine: "A local coding harness that checks its own work.",

  howItWorks: [
    "You describe the task.",
    "The harness plans it, and a small local model does one narrow step at a time.",
    "Every step is verified by code before it's kept — nothing is trusted on the model's word alone.",
  ],

  whatItDoes: [
    "Runs entirely on your machine against a small local model.",
    "Never deletes files, never pushes to a remote repo on its own, and never touches secrets or credentials.",
    "Loop C, its gated self-improvement step, runs only against the local model — it has not produced any real weight changes so far, and it never touches cloud models.",
  ],

  tiers: [
    { feature: "Local harness, full functionality", free: "Included", subscriber: "Included" },
    { feature: "Cloud model option", free: "Locked", subscriber: "Included" },
  ],

  installSteps: [
    "Download the zip above.",
    "Run install.bat.",
    "Open the harness and describe your first task.",
  ],

  faq: [
    {
      q: "Is Aurem Loop free?",
      a: "Yes. The full local harness is free with a signup — no payment required.",
    },
    {
      q: "Does it send my code anywhere?",
      a: "The local harness runs against a small model on your own machine. It does not send your code to Aurem's servers unless you explicitly choose the subscriber cloud option, and that option is not yet available.",
    },
    {
      q: "What does it actually do to my files?",
      a: "It plans a task, has a local model write one small step at a time, and verifies each step with real checks before keeping it. It never deletes files and never pushes to a remote repo on its own.",
    },
    {
      q: "Does Aurem Loop get smarter over time?",
      a: "It has a gated self-improvement step (Loop C) that only ever touches its own local model. As of today it has not produced any real weight changes.",
    },
    {
      q: "What's included for free vs. as a subscriber?",
      a: "The local harness itself is the same for everyone. A subscriber additionally unlocks a cloud model option — that option is locked for free accounts and is not yet available even for subscribers.",
    },
    {
      q: "What platforms does it support?",
      a: "Windows. There is no install for other platforms yet.",
    },
    {
      q: "How do I uninstall it?",
      a: "Run the uninstaller the installer places alongside the harness, or remove the install folder and its scheduled task manually.",
    },
  ],

  footerLinks: [
    { to: "/about", label: "About" },
    { to: "/contact", label: "Contact" },
    { to: "/privacy", label: "Privacy" },
  ],
};
