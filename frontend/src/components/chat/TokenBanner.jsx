/**
 * components/chat/TokenBanner.jsx — Iter 212m-153 / Iter 364
 *
 * Yellow/red banner shown above the chat composer when the user is
 * close to (or past) their usage limit.  Drives upgrade conversion.
 *
 * Props:
 *   usage  { used, effective_limit, remaining, pct_used, is_exhausted,
 *            is_blocked, monthly_task_cap, tasks_this_month, tasks_remaining }
 *
 *   is_blocked == true: red,    "🚫 …exhausted — upgrade …"
 *                                 (server-side enforcement is live)
 *   pct_used >=  80%:   yellow, "⚠️ 80% tokens used — N remaining …"
 *   below 80%:          renders nothing (returns null)
 *
 * Iter 364 change — `is_blocked` (canonical server flag) wins over the
 * client-computed pct calc so the banner can never lie about "you're
 * blocked" while the server is happily accepting requests, and can
 * never say "you're fine" while the server is 402-ing every call.
 *
 * 2026-09-26 — bug fix: the blocked-state copy used to ALWAYS quote
 * the legacy token counters ("50 / 1,000 used"), even when the real,
 * customer-facing reason for the block is the flat-fee monthly TASK
 * cap (Free = 10 tasks/mo — see services/subscription_tiers.py). A
 * user could be blocked at 10/10 tasks having burned only 5% of their
 * token ceiling and see a confusing "50/1,000 used" message that
 * looks like they have plenty left. Now: when the task cap is the
 * actual reason (`tasks_this_month >= monthly_task_cap`), say so in
 * tasks — the unit this app's own pricing page advertises ("flat
 * pricing, no token meters").
 */
import React from "react";

export default function TokenBanner({ usage }) {
  if (!usage) return null;
  const pct = usage.pct_used || 0;
  // 2026-09-27 — the token counter was decoupled from gating (only
  // the flat-fee monthly task-count cap blocks real requests now).
  // `is_blocked` is the server's SOLE authoritative signal for that;
  // the old is_exhausted/pct>=100 fallback is REMOVED because those
  // now legitimately go true for normal, unblocked usage (the token
  // meter is cost-visibility only) — keeping the fallback would show
  // a false "blocked" banner and disable sending for users the
  // server is happily serving.
  const blocked = usage.is_blocked === true;
  if (!blocked && pct < 80) return null;
  const remaining = Math.max(0, usage.remaining || 0);
  const used  = usage.used || 0;
  const limit = usage.effective_limit || 0;
  const taskCap = usage.monthly_task_cap;
  const tasksThisMonth = usage.tasks_this_month || 0;
  // Which ceiling actually tripped? Task cap is the real, advertised
  // limit for flat-fee tiers — check it first.
  const blockedByTaskCap = blocked
    && taskCap != null && tasksThisMonth >= taskCap;

  const tone = blocked
    ? { bg: "rgba(255,77,77,0.10)",  border: "rgba(255,77,77,0.45)",  color: "#ff8585", icon: "🚫" }
    : { bg: "rgba(255,196,0,0.10)", border: "rgba(255,196,0,0.45)", color: "#ffcf5c", icon: "⚠️" };

  return (
    <div
      data-testid="token-banner"
      data-state={blocked ? "exhausted" : "warning"}
      style={{
        display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
        padding: "8px 12px",
        background: tone.bg,
        border: `1px solid ${tone.border}`,
        borderRadius: 8,
        fontSize: 12,
        color: tone.color,
        fontFamily: "'Jost', system-ui, sans-serif",
      }}
    >
      <span style={{ fontSize: 14 }}>{tone.icon}</span>
      <span style={{ flex: 1, minWidth: 0 }}>
        {blocked ? (
          blockedByTaskCap ? (
            <>
              <b>Free tasks used up.</b> {tasksThisMonth} / {taskCap} tasks this month.
              Upgrade your plan to keep shipping.
            </>
          ) : (
            <>
              <b>Tokens exhausted.</b> {used.toLocaleString()} / {limit.toLocaleString()} used.
              Upgrade your plan to continue.
            </>
          )
        ) : (
          <>
            <b>{Math.round(pct)}% tokens used</b> · {remaining.toLocaleString()} remaining.
            Upgrade to keep going.
          </>
        )}
      </span>
      <a
        href="/pricing"
        data-testid="token-banner-upgrade"
        style={{
          padding: "5px 12px",
          background: tone.color,
          color: "#0a0a0e",
          fontWeight: 600,
          fontSize: 11,
          borderRadius: 6,
          textDecoration: "none",
          whiteSpace: "nowrap",
        }}
      >
        Upgrade →
      </a>
    </div>
  );
}
