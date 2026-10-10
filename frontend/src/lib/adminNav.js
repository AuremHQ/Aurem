/**
 * lib/adminNav.js — single source of truth for admin navigation.
 *
 * 2026-08-27 · Admin Compact M4 — the founder-only "Admin" flyout in
 * the global RailShell and the in-shell sidebar inside Admin.jsx used
 * to be two separately hand-maintained lists (5 items vs 23 items),
 * which is exactly how the Rail's "Overview" link ended up silently
 * pointing at Cockpit (M5) — nobody updates two lists in lockstep.
 * Both surfaces now read id/label/route from this ONE array; each
 * surface still owns its own icon choice + layout (flyout vs grouped
 * sidebar), only the navigation data itself is unified.
 *
 * M2 note: "payments" and the rail's "financials" both resolve to the
 * same merged AdminFinancials page now — kept as two entries on
 * purpose (they live in two different navigation *systems*: the
 * always-visible global rail vs. the in-context admin sidebar), not a
 * true duplicate the way the /admin/observability route was.
 */
export const ADMIN_NAV = [
  { id: "cockpit",         label: "Cockpit",              route: "/admin/cockpit",       group: null, screenKeys: ["home.home"] },
  { id: "overview",        label: "Overview",             route: "/admin/overview",       group: "MONITOR", screenKeys: ["home.home"] },
  { id: "llm_credits",     label: "LLM Credits",          route: "/admin/llm-credits",    group: "MONITOR", screenKeys: ["money.credits"] },
  { id: "parliament_live", label: "Parliament Live",      route: "/admin/parliament-live",group: "MONITOR", screenKeys: ["ora.council"] },
  { id: "qa_health",       label: "QA Health",            route: "/admin/qa",             group: "MONITOR", screenKeys: ["ora.quality"] },
  { id: "maintenance",     label: "Maintenance",          route: "/admin/maintenance",    group: "MONITOR", screenKeys: ["system.maintenance"] },
  { id: "arch",            label: "Architecture",         route: "/admin/architecture",   group: "MONITOR", screenKeys: ["system.codehealth"] },
  { id: "onboarding_funnel", label: "Onboarding Funnel",  route: "/admin/onboarding-funnel", group: "USERS", screenKeys: ["growth.onboarding"] },
  // 2026-09-28 MASTER TASK "ACTIVATION FIRST" — the granular
  // signed-up→...→came-back-another-day dashboard w/ drop reasons +
  // first-task token cost. Deliberately its OWN entry, not merged
  // into "Onboarding Funnel" above — that page reads /admin/funnel's
  // `nudge_stages` (a different, coarser cron-nudge classification)
  // and this page must not disturb that existing consumer.
  // Admin Team S3 (AT2) — Owner-only invite/manage UI for
  // admin_team_members (a separate collection/feature from the
  // existing workspace Team at /team, untouched by this entry).
  { id: "activation_funnel", label: "Activation Funnel",  route: "/admin/funnel",        group: "USERS", screenKeys: ["growth.activation"] },
  { id: "bin_tracker",     label: "BIN Tracker",          route: "/admin/bin-tracker",    group: "USERS", screenKeys: ["users.plans"] },
  { id: "github_bulk_revoke", label: "GitHub Bulk Revoke", route: "/admin/github-bulk-revoke", group: "USERS", screenKeys: ["users.github"] },
  { id: "visibility_kit",  label: "Visibility Kit",       route: "/admin/visibility-kit", group: "USERS", screenKeys: ["work.visibility"] },
  { id: "users",           label: "Users (Legacy)",       route: "/admin/users",          group: "USERS", screenKeys: ["users.all"] },
  { id: "support",         label: "Support",              route: "/admin/support",        group: "USERS", screenKeys: ["support.tickets"] },
  { id: "suggestions",     label: "Suggestions",          route: "/admin/suggestions",    group: "USERS", screenKeys: ["support.suggestions"] },
  { id: "self_bugs",       label: "Self-Repair Log",      route: "/admin/self-bugs",       group: "USERS", screenKeys: ["ora.bugs"] },
  { id: "audit",           label: "Audit Log",            route: "/admin/audit",          group: "USERS", screenKeys: ["logs.activity", "logs.admin"] },
  // Admin Team S3 (AT2) — Owner-only invite/manage UI for
  // admin_team_members (a separate collection/feature from the
  // existing workspace Team at /team, untouched by this entry).
  // AT3 — every action here is hardcoded owner-only (settings.team),
  // so this intentionally gets NO screenKeys: a member can never see
  // this tab no matter what overrides the Owner grants elsewhere.
  { id: "team",            label: "Admin Team",           route: "/admin/team",           group: "USERS", screenKeys: [] },
  { id: "feature_flags",   label: "Feature Flags",        route: "/admin/feature-flags",  group: "CONFIG", screenKeys: ["settings.switches"] },
  { id: "house_rules",     label: "House Rules V2",       route: "/admin/house-rules",    group: "CONFIG", screenKeys: ["ora.instructions"] },
  { id: "robot_guide",     label: "Robot Guide",          route: "/admin/robot-guide",    group: "CONFIG", screenKeys: ["ora.instructions"] },
  // Folded in from the rail's rail-only ADMIN_ITEMS — previously
  // unreachable from inside the Admin sidebar (M4).
  { id: "api_keys",        label: "API Keys",             route: "/admin/api-keys",       group: "CONFIG", screenKeys: ["settings.apikeys"] },
  { id: "payments",        label: "Payments & Revenue",   route: "/admin/payments",       group: "BUSINESS", screenKeys: ["money.payments", "money.summary"] },
  { id: "financials",      label: "Financials",           route: "/admin/financials",     group: "BUSINESS", screenKeys: ["money.summary"] },
  { id: "tokens",          label: "Token P&L",            route: "/admin/token-pnl",      group: "BUSINESS", screenKeys: ["money.pnl"] },
  { id: "dash",            label: "Dashboard",            route: "/admin/dashboard",      group: "SYSTEM", screenKeys: ["home.home"] },
  { id: "projects",        label: "Projects",             route: "/admin/projects",       group: "SYSTEM", screenKeys: ["work.projects"] },
  { id: "tasks",           label: "Tasks",                route: "/admin/tasks",          group: "SYSTEM", screenKeys: ["work.tasks"] },
  { id: "agent_perf",      label: "Agent Performance",    route: "/admin/agent-performance", group: "SYSTEM", screenKeys: ["ora.performance"] },
  { id: "mcp",             label: "MCP Usage",            route: "/admin/mcp-usage",      group: "SYSTEM", screenKeys: ["settings.usage"] },
  { id: "failure_patterns", label: "Failure Patterns",    route: "/admin/failure-patterns", group: "SYSTEM", screenKeys: ["ora.failures"] },
  { id: "reliability",     label: "Reliability",          route: "/admin/reliability",    group: "SYSTEM", screenKeys: ["ora.reliability"] },
  // Composite page (Stripe keys, LLM models, GitHub App, video
  // secrets, ORA hints). Its data-fetch (`GET /admin/settings`) has
  // no screen_key in the registry today, which the gate treats as a
  // hard owner-only lock regardless of any override (see
  // cto_services/auth.py) — so, like "team" above, this intentionally
  // gets NO screenKeys: a member can request it (see the generic
  // "Request access" flow) but can never actually open it themselves.
  { id: "settings",        label: "Settings",             route: "/admin/settings",       group: "SYSTEM", screenKeys: [] },
];

export function findAdminNavItem(id) {
  return ADMIN_NAV.find((n) => n.id === id) || null;
}

/** AT3 — true if `access` (the `{is_owner, screens}` shape returned by
 * `/admin/team/my-access`) lets the viewer open this nav item at all.
 * Owner always passes. A team member passes if they hold a non-"off"
 * level on at least one of the item's `screenKeys` (empty list, like
 * "team", means nobody but the Owner ever sees it). */
export function canOpenAdminNavItem(item, access) {
  if (!access) return false;
  if (access.is_owner) return true;
  const screens = access.screens || {};
  return (item.screenKeys || []).some((k) => screens[k] && screens[k] !== "off");
}

/** AT3 — the nav list filtered down to what this viewer can open. */
export function visibleAdminNav(access) {
  return ADMIN_NAV.filter((item) => canOpenAdminNavItem(item, access));
}

/** Rebuild the sidebar's grouped shape (with `{group: "X"}` separator
 * rows) from the flat ADMIN_NAV list, attaching each surface's own
 * Icon component by id. `navItems` defaults to the full list (Owner);
 * AT3 passes `visibleAdminNav(access)` instead for a team member so
 * the grouped sidebar only ever contains what they can open. */
export function buildGroupedAdminNav(iconMap, navItems = ADMIN_NAV) {
  const out = [];
  let lastGroup;
  for (const item of navItems) {
    if (item.group && item.group !== lastGroup) {
      out.push({ group: item.group });
    }
    lastGroup = item.group;
    out.push({ id: item.id, label: item.label, route: item.route, Icon: iconMap[item.id] });
  }
  return out;
}
