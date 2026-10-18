# AUREM / ORA — Product Strategy: Combos, Positioning & Usage-Based Pricing
_Written 2026-02. Analysis only — nothing in this doc has been implemented._

## 0. Scope & how this was produced
Full read-only codebase investigation (routers/, services/, landing page copy,
current Stripe/billing code) + the real cost-ledger data already surfaced
during the F1 quota work (Q1/Q2/Q3 tickets). No code changed for this doc.

---

## 1. What exists today (the raw material for combos)

| Capability | Where | What it actually does |
|---|---|---|
| **Loop engine** | `routers/loop.py`, `services/loop_engine.py` | Plan → Execute → Verify → Scan → Ship. Reads a connected GitHub repo, writes code, lints/tests, security-scans, commits only after user clicks Ship. |
| **Codebase Health Scanner** | `services/codebase_health_core.py` + `_extra.py` | 16 categories: security, performance, code_quality, dependencies, database, bug_hunt, docker, test_coverage, api_security, security_headers, accessibility, seo_basics, git_hygiene, error_handling, mobile_responsiveness, documentation. "Launch-ready X/16" score. |
| **Vanguard** | `routers/vanguard_ci.py` | 25-pattern regex security scanner, CI sidecar, can hard-block a commit on CRITICAL findings. |
| **Fix Pipeline** | `routers/fix_pipeline.py` | Auto-fixes scan findings (currently free tier only gets `visibility-kit`, everything else is paid — per the Q1/F1 work). |
| **Visibility Kit** | `routers/visibility.py` | SEO + GEO + AEO (generative/answer-engine optimization) — a discoverability/growth tool, unrelated to code correctness. |
| **Architect** | `routers/architect.py` | "What I would build" advisory agent — roadmap/planning, not code-writing. |
| **MCP server** | `routers/mcp.py` | 19 tools exposed via Model Context Protocol — lets Claude Desktop / Cursor / Cline call ORA's repo-read, scan, and ship-code functions directly. Effectively: **ORA as infrastructure other AI tools can call**, not just a standalone UI. |
| **Personal Track / Scaffold** | `routers/personal_track.py`, `routers/scaffold.py` | No-existing-repo "blank slate" flow — a different (non-GitHub-first) onboarding path for people with no codebase yet. |
| **Trust / Gallery** | `routers/trust.py` | Public deploy-count + uptime badges, opt-in public project showcase. |
| **Wrapped** | `routers/wrapped.py` | Spotify-Wrapped-style monthly shareable stat card (ships, hours saved, streak). |
| **Rollback v2** | `routers/rollback_v2.py` | One-click revert of any ORA commit. |
| **Hosted Deploy** | `routers/hosted_deploy.py` | Vercel/Netlify deploy-hook bridge. |

**Current positioning** (from the live landing page): *"AUREM is the company. ORA is the product — an autonomous AI engineer that reads your GitHub repo, writes code, runs security scans, and commits directly to your branches."* Competitive set implied: Cursor, Copilot, Devin, Replit Agent.

**Current pricing** (`services/subscription_tiers.py`): Free $0 (10 tasks/mo) → Starter $9 (50) → Pro $19 (300 + 100 Maxx overage) → Team $49 (400). Task-**count**-based, not $-based.

**Real cost data** (this pod's DB, confirmed in the F1 ticket): average real LLM call costs **$0.0025**; a full first-scan costs **~$0.016**; p95 of a free user's daily chat+task spend is **$0.044**; max ever observed **$0.832/day**.

---

## 2. Combo ideas — concrete, sellable workflows (not yet built)

These chain EXISTING services together into a single named outcome, instead of
making the user manually run 3 separate features. Ranked by how directly they
"help a user's codebase" (your framing).

### Combo A — "Health Check & Fix" (highest priority, most natural)
Scan (16-category) → triage findings by severity → Fix Pipeline auto-fixes
CRITICAL/HIGH → re-scan → show before/after score. **One click, a visibly
healthier repo, with proof.** This is the single most "debugger"-flavored
offering you have, and it's almost entirely built already (scan + fix exist;
the missing piece is the auto-chain + before/after diff).

### Combo B — "Launch Readiness Score"
Codebase Health + Vanguard + Visibility Kit run together as ONE go/no-go
report before a user shares their project publicly. Turns 3 separate tools
into a single answer to "am I ready to launch?" — code quality + security +
discoverability in one score.

### Combo C — "Security Hardening Loop"
Vanguard finds CRITICAL/HIGH findings → auto-generates a Loop task per
finding → Verify phase re-runs Vanguard to confirm it's gone → Ship. Turns a
passive report into "I found it AND fixed it, here's the diff" — a much
stronger pitch than a static scan.

### Combo D — "Weekly Codebase Checkup" (recurring/subscription-sticky)
Scheduled (reuses existing cron/digest infra) health scan + Vanguard +
diff-since-last-week summary, delivered as a digest. Turns a one-off scan
into ongoing monitoring — this is the single best lever for **retention**,
since it gives a recurring reason to stay subscribed instead of "I scanned
once, I'm done."

### Combo E — "Ship + Prove It" (growth loop, not just codebase help)
Loop ship → auto-generates a Wrapped-style share card + offers Trust Gallery
opt-in + updates the repo's public health badge. Turns a private commit into
a public artifact that can drive new signups — direct acquisition lever,
reuses Wrapped + Trust which already exist but currently don't talk to each
other.

### Combo F — "Repo Rescue" (onboarding moment for messy/inherited repos)
On first connect of a new repo: Architect (what-I'd-build) + Codebase Health
+ Vanguard run together to produce a prioritized "first 30 days" roadmap.
Strong first-impression for anyone connecting an old/neglected codebase.

### Combo G — "ORA as infrastructure" (different audience — not a code-health combo, a distribution one)
MCP already exposes scan/fix/ship as callable tools inside Claude Desktop /
Cursor / Cline. This is effectively a second product wearing the same
engine: people who don't want a new UI, just want ORA's scanner+ship engine
callable from the IDE they already use. Worth naming/selling separately
(possible future B2B/API angle), not a "codebase help" combo but adjacent.

### Combo H — "One-Click Deploy Readiness"
Codebase Health (blocking issues) + Hosted Deploy (Vercel/Netlify) +
Rollback, as one guided flow: scan → fix blockers → deploy → rollback always
available. Reduces the "will this break production" fear for non-technical
users — natural fit with the Personal Track / Scaffold audience specifically.

**My recommendation if you want to start with ONE**: **Combo A** (Health
Check & Fix) — it's the cheapest to build (both halves exist, just needs
chaining + a before/after UI), it's the most literal "debugger" pitch, and
it directly demonstrates value before asking for a credit card.

---

## 3. Positioning (default used, per your instruction to skip for now)
Kept as: **"autonomous AI engineer that ships code"** as the hero pitch
(matches what's live and already has real testimonials on the landing page),
with the combos above given **names** so the debugging/health angle becomes
concrete and separately sellable without replacing the core pitch — e.g. a
pricing page section literally called "Launch Readiness" or "Weekly
Checkup" as a named feature under the ORA umbrella, not a rebrand.

---

## 4. Pricing — pure usage-based, no task buckets (your confirmed choice)

### What changes
Replace `tasks_per_month` caps in `services/subscription_tiers.py` with a
**$-metered wallet**, billed off the SAME `real_llm_usage` ledger the F1/Q1
work already built and proved accurate. This is a good structural fit — you
already track real `cost_usd` per call, per user, in real time.

### Proposed shape
- **Free tier**: keep the just-built **$0.25 lifetime free budget** exactly
  as-is (Q1) — it already *is* a usage-based free tier, nothing to change.
- **Paid tiers become two separate knobs** instead of one task count:
  1. **A flat monthly access fee** — unlocks features (parallel agents,
     priority queue, Maxx mode, dedicated DB, custom domain) — same
     role Starter/Pro/Team play today, just without a task cap attached.
  2. **Metered usage** — actual `cost_usd` consumed, billed at a **markup
     multiplier** (industry norm for AI dev tools is roughly 3–10x raw
     inference cost, to cover orchestration/support/infra margin — needs
     your call on the exact number).
- **Spend predictability**: usage-based billing removes the "flat $19/mo,
  I know my ceiling" comfort. Mitigate with a **per-user monthly spend cap +
  alert**, which is a direct extension of the daily cap infra Q2 already
  built (`services/usage.py::assert_daily_spend_cap`) — just needs a
  monthly variant and a user-facing "spend so far this month" meter.

### Implementation reality check
- **No metered-billing Stripe code exists today** — current `routers/payments.py`
  only does flat recurring subscriptions. Usage-based billing needs Stripe's
  metered price + usage-record API (or an end-of-month invoice-item model) —
  a genuinely new integration surface, not a small tweak.
- **Migration**: existing Pro/Team subscribers are on a task-count mental
  model today — needs clear "here's what changes and why" messaging, and
  probably a grandfather/transition window rather than an instant flip.

---

## 5. Decision points for you (nothing proceeds without your pick)
1. Which combo to build first (recommend **A**, cheapest + most literal fit).
2. The markup multiplier for metered usage (need a number from you — this is
   a margin/business call, not something I should pick).
3. Flat monthly access-fee amounts under the new model (keep $9/$19/$49 as
   access fees, or re-price them now that they no longer include a task cap?).
4. Migration approach for existing paid subscribers (grandfather vs hard cutover).
5. Whether to scope the metered-billing Stripe work as its own ticket (it's
   the biggest lift in this whole doc) separately from the combo-building work.
