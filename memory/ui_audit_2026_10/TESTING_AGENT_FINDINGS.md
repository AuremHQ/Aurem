# UI + Workflow Documentation Audit: Testing-Agent Findings (iteration_402)
All of this is PREVIEW (bin-context-pat.preview.emergentagent.com, local/ephemeral Mongo). Nothing here describes production.
Screenshots live in /app/memory/ui_audit_2026_10/. "NV" means NOT VERIFIED.
Read-only audit. No app code, flags, or data were changed. Throwaway account created: `audit-popup-377207@example.com` (pw redacted, ends `26!x`).

## 0. Environment facts that block or shape several flows (evidence: curl, logs)
- **Stripe key in preview backend/.env is LIVE (`sk_live_…`), not test mode.** Clicking "Upgrade to Starter" opened `checkout.stripe.com/f/pay/cs_live_…`. The page shows "Subscribe to Starter US$9.00 per month" with no TEST MODE badge. I did NOT enter card 4242 (it would not work in live mode, and a real card would really be charged). Paid-tier UI states are NV. Screenshot: stripe_checkout_starter.png
- **OpenRouter LLM key returns 401 in preview.** Backend log: `call_openrouter_model(...) 401 Unauthorized`. Every LLM-dependent flow fails or comes back empty: Fix (`llm_empty_response`), chat replies (empty "Root cause:/Fix:"), and /build Ship (`qa_reviewer_unavailable … raw_error http_401 User not found`).
- **Admin GitHub access is broken for the default project p_demo_a.** The dashboard shows "GitHub access revoked (token rejected …)". Toast: "frontend looks disconnected from GitHub — use the switcher above to pick another project, or reconnect it in Settings." Two admin projects still scan OK: p_42a22287f7 (polarisbuiltinc-wq/ora-grounding) and p_firstscan_demo_e6ed2d.
- **Feature flag `ship_via_pr`** in preview `feature_flags`: `{enabled:true, rollout_pct:100, description:"…Preview only, prod stays OFF (no row in prod Mongo)"}`. Confirmed via a direct DB read.
- **The preview frontend is served as unbundled ES modules**, one request per file. Repeated full page reloads triggered ingress 429s on .jsx module requests. That made RouteErrorBoundary show on /admin and /analytics during my first pass. This is a preview-infra artifact. Later passes used client-side navigation.

## 1. Page audit (Admin = test@aurem.dev unless stated)
| Route | Result (live) | Key on-screen text / controls | Screenshots |
|---|---|---|---|
| / | loads | H1 "The AI engineer That actually commits. No IDE, no token meters." CTAs: "Start free — 10 tasks" (/signup), "Watch it ship" (#watch). Stats: "$9 flat monthly · 0 token billing · 1 tap to commit · ∞ repos". Sections: Watch ORA ship, 4 steps, Three windows, Pick your speed (Swift/Pro/Max), pricing (Monthly/Annual SAVE 20%, Current plan, Upgrade to Starter/Pro/Team), FAQ. Cookie banner bottom-right. | landing_desktop.png, landing_mobile.png |
| /pricing | unchanged | H1 "Flat pricing. No token meters." Buttons: Get started, Upgrade to Starter/Pro/Team. Upgrade fires POST /payments/checkout and redirects to LIVE Stripe checkout. | pricing_desktop.png, pricing_mobile.png |
| /signup | loads | H1 "Start building, production-ready from day one", "10 free tasks. No card required." Inline ORA GUIDE card: "Fastest way: click Continue with GitHub below 👇". Fields: Google, GitHub, name, email, phone+country, password, confirm, terms checkbox, "Create account & start". | signup_desktop.png, signup_mobile.png |
| /login | loads | H1 "Welcome back, builder." Google, GitHub, email, password, "Forgot password?", "Sign in", "Claim 10 free tasks →". | login_desktop.png, login_mobile.png |
| /demo | loads | H1 "From signup to shipped PR — see the whole loop". Player controls (❚❚, 🔇, ↺ RESTART, Jump to step 1-7), "⚡ 25s TEASER", "START FREE — 10 TASKS", "SEE PRICING". | demo_desktop.png, demo_mobile.png |
| /why-ora | loads | H1 "20 features that literally live in the code — grep them if you don't believe us." Cards include "5-Category Codebase Health Scanner". Note: /codebase-health shows 16 categories. | why_ora_desktop.png, why_ora_mobile.png |
| /dashboard (admin) | loads | Repo switcher "aurem-demo/frontend › TEST_branch_2026_08_20". Previous chats (19), bell 9+, New run, Preview/Graph/Kit tabs, "1 console error", SEND TO ORA, COPY, Session id. Banner "⚠ 14 projects point to deleted or unreachable repos — click to clean up". Card "Your project is connected. ORA read 8 files." with Scan/Fix/Explain. Card "Free first-scan already used … Scan this repo". Strip "9 critical · 2 high idle 30+ days". Scope chip. Composer with "CASUAL 0 / 20,000" and "Swift". Advisor panel. ORA Guide bubble. | dashboard_admin_desktop.png, dashboard_admin_mobile.png, dashboard_admin_desktop_1920.png |
| /dashboard (fresh signup, no repo) | loads | "No repo connected" switcher. Welcome message. NewUserWizard modal. ORA Guide bubble. Advisor tab. (Section 3 lists them in order.) | popup_01_after_signup_t*.png |
| /codebase-health | loads | "CODEBASE HEALTH / How healthy is your code today?" Badge "💎 760810 tokens" (admin). Fresh free user sees "💎 250000 tokens". Empty state: "🔍 Your codebase is unscanned". 16 cards with diamond costs (Section 4). Result, loading and error states captured (Section 2B). | codebase_health_admin_desktop.png, codebase_health_full_scan_loading.png, codebase_health_result_ora_grounding.png, codebase_health_admin_mobile.png |
| /bug-hunt | loads (marketing page, even when logged in) | H1 "Detect security vulnerabilities in yo…". Sections: Secret Types (15), Vulnerable Code Patterns (20), Exposed Endpoints (10), CVE-Vulnerable Dependencies (11). CTAs "Scan my repo — Start free" go to /signup even for a logged-in admin. "Health Dashboard" goes to /codebase-health. | bug_hunt_admin_desktop.png |
| /tools | loads | H1 "Developer tools". All 4 cards are "Coming soon": Bug Hunt, Vanguard Scan, Security Scan, Health Scan. Each card has a repo select (24 entries, including 11× "probe-cross" and duplicates), a disabled "Coming soon" button, and a "Get notified when ready" input + "Notify me". "Back to ORA". | tools_admin_desktop.png |
| /projects | loads | H1 "Client Projects". "Add Project", project rows "APP", "Edit project". | projects_admin_desktop.png |
| /deploy | loads | H1 "Deploy". Deploy configuration (host, user, path, SSH private key textarea), "Show advanced", "Save config", "Deploy now". Recent deploys. "Hosted deploy (Vercel / Netlify)" with repo select + "Connect deploy". | deploy_admin_desktop.png |
| /domain | loads | H1 "Domain". Input "app.example.com", registrar select (cloudflare/namecheap/route53/other), "Save", "Verify DNS". | domain_admin_desktop.png |
| /integrations | loads | H1 "Connect ORA to your IDE". Exact text: **"Used by every IDE integration below. Treat it like a password — you can revoke it any time from Admin → API Keys."** Key shown masked `sk-aurem-oauth…ZuxQ`, Reveal/Copy, "Test Connection". Endpoint shown is **https://auremcto.com/api/aurem-dev/mcp (the PROD URL, even in preview)**. Tabs: Cursor/VS Code/Claude Desktop/Claude Code CLI. | integrations_admin_desktop.png |
| /settings?tab=profile | loads | Profile, Phone number, Change password, Public Ship Wall toggle, Loop Trust Level L1/L2 (ACTIVE)/L3, invite share (Copy/X/LinkedIn), "Delete my account". | settings_profile_admin_desktop.png |
| /settings?tab=plans | loads | "TOKEN WALLET 250,000" (free user) shown above "Plans — Flat fee. No token surprises." Plan cards: FREE "10 tasks per month … Direct commits to your repo". STARTER $9 "50 tasks per month … Direct commits + rollback". PRO $19 "300 tasks … Direct GitHub commit — Beta". TEAM $49 "400 tasks … Direct GitHub commit — Beta". Then ORA Wrapped stats and "Usage history — Your token activity for the last 30 days." | settings_plans_free_before_upgrade.png, settings_plans_admin_desktop.png |
| /settings?tab=integrations | loads | "Vercel · ORA platform tools", "My Repos", "Disconnect", "Manage on GitHub" (link). | settings_integrations_admin_desktop.png |
| /settings?tab=vault | loads | Only "Vault audit log — Every read / write against your encrypted key vault. No key activity yet." **No revoke-API-key button exists here.** | settings_vault_admin_desktop.png |
| /settings?tab=team | loads | "Invite a team member" (email, role Admin/Editor/Viewer, Send invite), "Team (7)" with "Edit access". | settings_team_admin_desktop.png |
| /wrapped | loads | H1 "ORA Wrapped", This month / Last month / All time. | wrapped_admin_desktop.png |
| /automations | redirects to **/dashboard** (live) | | automations_admin_desktop.png |
| /wall | redirects to **/** (landing). A logged-in admin then sees the marketing nav "Sign in / Start free". | wall_admin_desktop.png |
| /tokens | redirects to **/settings?tab=plans** (live) | | tokens_admin_desktop.png |
| /analytics | redirects to **/dashboard** (live) | | analytics_admin_desktop.png |
| /build | loads, LIGHT theme (Section 4.9) | "Personal Track" pill, H1 "What do you want to build?", textarea, "Build", 5 example chips, "⌘↵ to build". Header: "AUREM" + settings + logout icons. | popup_04_nav_build_newuser.png, build_admin_mobile.png |
| /support | loads (public layout) | H1 "Need help?". Name, email, message, "Send message", "← Back to home". | support_admin_desktop.png |
| /admin sidebar | loads | Cockpit, Overview, LLM Credits, Parliament Live, QA Health, Maintenance, Architecture, Onboarding Funnel, Activation Funnel, BIN Tracker, GitHub Bulk Revoke, Visibility Kit, Users (Legacy), Support, Suggestions, Self-Repair Log, Audit Log, Admin Team, Feature Flags, House Rules V2, Robot Guide, API Keys, Payments & Revenue, Financials, Token P&L, Dashboard, Projects, Tasks, Agent Performance, MCP Usage, Failure Patterns, Reliability. Links: back to app, Personal Track ops, ORA Chat. | admin_admin_desktop.png |
Mobile 375: none of the 16 authenticated routes I captured has horizontal overflow (scrollWidth = 375). On mobile the ORA Guide bubble covers the /build textarea and Build button (build_admin_mobile.png).

## 2. Flows
**A. New visitor → signup → dashboard → connect repo → first scan**
1. /signup with email: WORKS. Lands on /dashboard about 1.5s later.
2. First dashboard: WORKS. NewUserWizard "Connect your GitHub repo" opens, together with the mascot.
3. "Continue with GitHub App": PARTIAL. It opens a popup at `github.com/login?integration=aurem-devops&return_to=/apps/aurem-devops/installations/new…`. I could not finish it (no GitHub account), so NV.
4. First scan without a repo: BROKEN for a no-repo user. Clicking a category shows "Connect a project first from the dashboard." No API call fires. (error_codebase_health_no_repo_newuser.png)

**B. Scan → Fix → diff/verify/PR** (admin, project p_42a22287f7 ora-grounding)
- Scan on p_demo_a: BROKEN. The backend returns 502 with detail "GitHub returned HTTP 404 while minting the App installation token … likely transient". Cloudflare replaces that body, so the user sees **"The origin web server returned an invalid or incomplete response to Cloudflare. This typically indicates the origin is overloaded or misconfigured."** (codebase_health_result_admin.png)
- Scan on ora-grounding: WORKS. Score **88/100 HEALTHY**, "LAUNCH-READY 9 / 9 categories", "Your codebase has 4 issues … Scanned 9 files.", "Rescan all · 78 💎", "4 issues found across 9 scanner(s) — 0 critical." The page says "LAUNCH-READY 9/9" while also listing 4 issues and only scanning 9 of 16 categories.
- Locked findings: the category shows "🟡 MEDIUM (1)" and "Unlock MEDIUM — 2 💎". Clicking Unlock showed the finding and "⚡ Fix all 1 →" with **no network request** (admin). (codebase_health_unlock_medium.png, codebase_health_after_unlock.png)
- Fix: BROKEN in preview. "Fix this — 1 task" opens the drawer "Fix in progress … RETRY 3/3 … ⚠ llm_empty_response". It ends with "Completed with failures · 0 fixed · 1 failed · Fixed 0/1 (0 tasks used …) · No live preview configured for this project — before/after proof wasn't attempted. · 0 of 1 fix committed". Root cause: OpenRouter 401. (fix_progress_t0.png, fix_progress_t1.png)
- Diff shown? NV (no diff was ever generated). Verify result before commit? NV. PR created? **No PR was created, so there is no PR URL or number.** The ship_via_pr behaviour (PR in preview vs direct commit in prod) is therefore NV live. The copy discrepancy is still real: the plans page says "Direct commits to your repo" / "Direct GitHub commit — Beta" while the preview flag is ON (PR mode).
- Score after Fix: stayed 88. No automatic re-scan was seen. Code note: CodebaseHealth.jsx `aurem:finding-fixed` handler adds a client-side **+2** to the score (`score + 2`) and does not re-scan.

**C. Chat "debug mode"**: PARTIAL. As the fresh user, I sent "Bug: my login page returns 500 when the email field is empty…". The header switched to **"Mode D · Debug"**, which is a distinct mode label. The reply was an empty template: "🟡 Root cause: (empty) / Fix: (empty) / This fix doesn't require a code change — you can apply it manually. / Response delivered". No read-file, diagnose or confirm steps were visible, because the LLM returned 401. tokens_remaining went 250000 → 249994. (chat_bug_description_newuser_t25.png)

**D. Stripe upgrade**: PARTIAL, then stopped on purpose. Before: "TOKEN WALLET 250,000" next to "FREE … 10 tasks per month". Clicking Upgrade fires POST /payments/checkout (200) and opens a LIVE Stripe checkout (see Section 0). I did not pay. The after-upgrade state is NV.

**E. /build (Personal Track) from the no-repo account**: PARTIAL.
- Example chip + Build fires POST /scaffold/new-project (200). It goes to /build/{draft_id} "YOUR DRAFT", with a file tree (Frontend 5, Database 1, Logic & Config 6, Overview 1), a README preview, "Regenerate" and "Ship it". The title is truncated: "A habit tracker with streak notificati".
- "Ship it" goes to /build/{id}/ship and shows a "Warming up the servers…" toast and steps. POST /materialize returns 503 `qa_reviewer_unavailable`. The user sees "We couldn't finish this one. Your draft is safe — try shipping again in a minute… Try again / Back to draft". (build_after_submit_t15.png, build_ship_it_t8.png)

## 3. Popup inventory: fresh signup audit-popup-377207, localStorage cleared
1. **CookieConsentBanner** (banner, z 10000, bottom-right). Text: "We use cookies to improve your experience…". Buttons: Manage preferences / Reject non-essential / Accept all. Shows on /signup. It **disappeared after signup without any click** (no visible element on /dashboard). (popup_00_signup_cookie_banner.png)
2. **ORA Guide mascot bubble** (floating bubble + avatar, z 10001), about 1.5s after landing on /dashboard. Text: "ORA GUIDE Connect your repo to get started — click Connect GitHub above. [Got it] [Something's wrong]".
3. **Advisor side tab** (floating tab, z 10002, right edge, "ADVISOR"). Chips: Diagnose failed run / Summarize open PRs / Token breakdown.
4. **NewUserWizard** (full-screen modal, z 9000), about 3s after landing. Text: "ORA GUIDE One click: Continue with GitHub App below — you pick which repos AUREM can see… Connect your GitHub repo… Install Aurem for your repos RECOMMENDED… Continue with GitHub App… Skip for now".
   - **It STACKS with #2.** The mascot (z 10001) sits above the wizard scrim. The two give conflicting directions: "click Connect GitHub **above**" vs "Continue with GitHub App **below**". (popup_STACKED_newuserwizard_plus_oraguide_mascot.png)
5. **StarterChoiceModal** (modal, z 9100). Triggered by the wizard's "Skip for now". Text: "Let's get your first win — No repo connected yet — no problem. Pick one and ORA will do something real right now. Scan my website / Fix a bug in my code / Try a demo project / Connect GitHub instead". Closes with ✕ or Escape.
   - **It STACKS with the mascot bubble (#2)**, which still says "click Connect GitHub above". (popup_STACKED_starterchoicemodal_plus_oraguide_mascot.png)
   - All three (wizard + starter modal + mascot) together: not observed. Skip closes the wizard before the starter modal opens.
6. **ORA Guide bubble persists on every page I visited**: /codebase-health, /tools, /settings, /build (light theme), and on top of the Fix progress drawer. For the admin the text is "New here? Start with a quick scan of your site — I'll tell you what to fix first. [Scan my site] [Dismiss]", and it **still shows after a scan already exists**.
7. Admin-only stacked set on /dashboard: toast "frontend looks disconnected from GitHub…", repo-cleanup-banner, first-win card, "Free first-scan already used" card, mascot bubble, mascot avatar badge "14". All visible at once. (dashboard_admin_desktop.png)
8. /build Ship: toast "Warming up the servers…".
9. Fix progress drawer (right drawer + scrim), triggered by Fix.
- **NOT triggered / not seen (NV):** FloatingORAButton as a separate element (only the mascot avatar was seen), TokenBell/TokenBanner quota warnings, EngagementNudge/PaletteNudgeBubble, ConnectRepoTour, UsageLimitModal. The session was about 6 minutes, not 10.

## 4. Specific confirmations
1. Per-category costs on /codebase-health:
   - Core: Security 5 💎, Performance 5, Code Quality 5, Dependencies 5, Database 5.
   - NEW: Bug Hunt 8, Docker CIS 5, Automated Tests 6, API & Auth Security 6, Security Headers 5, Accessibility 4, SEO Basics 4, Git Hygiene 3, Error Handling 4, Mobile Friendly 4, Documentation 4.
   - "🚀 Full Scan — all 16 categories · 78 💎". After a scan: "Rescan all · 78 💎", "Unlock MEDIUM — 2 💎".
   - Header badge "💎 760810 tokens" (admin) and "💎 250000 tokens" (free). "Buy more" was not seen.
2. Ship behaviour: NV live, because Fix failed before it reached commit or PR (LLM 401).
3. Redirects re-confirmed live: /wall → /, /automations → /dashboard, /tokens → /settings?tab=plans, /analytics → /dashboard.
4. /tools: all 4 cards are "Coming soon". Sidebar "Health Scan"/"Bug Hunt" (SidebarBound DEV_TOOLS): **not found in the live dashboard rail** at 1366 or 1920. The rail only has Chat/Ship/Insights/Settings/Admin, and hovering Chat shows a repo list. I tried 3 times and stopped, so this is NV. /codebase-health and /bug-hunt both work by direct URL. /bug-hunt is a marketing page whose CTAs go to /signup.
5. Before/after score: there is no before/after view. The score did not change after a failed fix and no re-scan ran.
6. Diff + verify before commit: NV.
9. /build uses a light cream/white theme. The rest of the app is dark navy/black. Compare popup_04_nav_build_newuser.png with popup_04_nav_codebase_health_newuser.png.
10. Error states:
    - RouteErrorBoundary "Something went wrong loading this page. This is usually a transient network hiccup. Your data is safe. [Retry] [Reload app]" DOES appear: offline + client navigation to /admin/feature-flags and /settings?tab=team (error_offline_admin_feature_flags.png, error_offline_settings_team.png), and during the preview 429 burst.
    - /projects offline shows a false empty state instead: "No projects yet. Connect a GitHub repo to get started." (the admin has 24 projects) (text captured; screenshot not saved due to a script error).
    - /codebase-health shows its own local red error banner (the raw Cloudflare text).
