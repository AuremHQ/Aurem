/**
 * faq.mjs — single source of truth for /faq.
 * Imported by BOTH the React page (FaqPage.jsx) AND
 * scripts/seo-prerender.mjs, so the crawler-facing static HTML can
 * never drift from what a real visitor sees (same pattern as
 * companyInfo.mjs). Pure data — no JSX, no React imports.
 *
 * Content: 11 questions people actually search (AlsoAsked,
 * "AI app development", 2026-11-12 — 52 questions, 41 unique).
 * Prices are founder-approved: App Rescue $199, Launch Audit $299,
 * App Care Plan $499/month. No unverified figures are repeated as
 * fact (no "85% of AI projects fail" claim).
 */
export const FAQ = {
  title: "FAQ — Ora by Aurem",
  description:
    "Frequently asked questions about Ora by Aurem: can AI develop an app, " +
    "what it costs, whether AI replaces developers, and why AI projects fail.",
  canonical: "https://auremcto.com/faq",
  heading: "Frequently asked questions",
  intro:
    "Questions people actually ask about AI app development — answered plainly.",
  items: [
    {
      q: "Can AI develop an app?",
      a: "Yes. AI can write the code for an app. But a real app needs more than code: fixing bugs, security, App Store rules, payments, and design that works. Ora by Aurem does exactly this — AI builds, and a human engineer reviews, fixes, and ships it.",
    },
    {
      q: "How much does it cost to create an app using AI?",
      a: "Ora's pricing: a free fix first, then App Rescue $199, Launch Audit $299, and App Care Plan $499/month.",
    },
    {
      q: "Can I build an app all by myself?",
      a: "Yes, for a simple app. But non-technical founders usually get stuck on bugs, security, and launch. That is what Ora is for — you describe the idea, Ora builds and ships it.",
    },
    {
      q: "Can a beginner create an app?",
      a: "Yes. AI tools make it possible to start with no coding experience. Finishing it and launching it is the hard part — that is the part Ora handles.",
    },
    {
      q: "How do I create my own AI app?",
      a: "Describe the idea, let AI draft it, test it, fix the bugs, then launch. If you do not want to do the fixing and launching yourself, Ora does it for you.",
    },
    {
      q: "Can AI replace app developers?",
      a: "No. AI writes code fast, but a human engineer is still needed to make it correct, secure, and shippable. AI changes the developer's job; it does not remove it.",
    },
    {
      q: "Will AI replace programmers in 5 years?",
      a: "No. The job changes — directing AI, reviewing its code, fixing what it gets wrong — but it does not disappear.",
    },
    {
      q: "Can you code an app with ChatGPT?",
      a: "Yes, for small and simple apps. A real product needs more than one chat session: a codebase, tests, fixes, and a launch plan.",
    },
    {
      q: "What is the best AI app builder?",
      a: "There is no single best one; it depends on the app. Ora is not an app builder — it is the engineer service for people who do not want to build the app themselves.",
    },
    {
      q: "Do app owners earn money?",
      a: "Some do; most apps earn little. What matters is solving a real problem and getting users. Ora does not promise income.",
    },
    {
      q: "Why do AI projects fail?",
      a: "There is no verified '85%' figure behind that claim. AI projects usually fail for the same reasons: unclear requirements, no real user need, bugs nobody fixes, and no launch plan. Ora's process is built to close those gaps: a real fix first, then audit, then ongoing care.",
    },
  ],
};
