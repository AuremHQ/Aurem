/**
 * components/FounderOfferPill.jsx — Slim "X of Y left" pill for the
 * free founder SEO fix offer.
 *
 * Iter 388t · Bug 11 fix — copy previously said "founder spots
 * remaining" which visually collided with the Landing hero's
 * "First-50 · X/50 founder spots left" chip (different offer:
 * signup promo Pro trial vs. free SEO fix for repo-connect).
 * Renamed to "free SEO fixes for founders" so the two counters
 * describe different things at a glance.
 *
 * 2026-09-22 · Founder-reported bug — this pill only ever read the
 * GLOBAL `/founder-offer/status` counter and had ZERO per-user
 * awareness, so it kept advertising "claim yours" to logged-in users
 * (Projects.jsx header) who had already claimed and used the offer.
 * Now: on an authenticated page (a JWT is present) it also fetches
 * `/founder-offer/user-status` and hides permanently for that user
 * once they've claimed at least once — same server-side truth
 * FounderOfferCard.jsx already uses, just consulted here too. On the
 * unauthenticated Landing page (no token) this check is skipped
 * entirely, so the public marketing pill is unaffected.
 *
 * Drop-in for any page header `right` slot. Polls /founder-offer/status
 * every 60 s; auto-hides when the offer sells out so it can't dangle.
 * Both `remaining` and `total` come from the API (single source of
 * truth — matches ConnectRepoBanner.jsx pattern) so an env-driven
 * change to PROMO_FOUNDER_OFFER_TOTAL doesn't silently drift the UI.
 */
import React, { useEffect, useState } from "react";
import { api, getToken } from "../lib/api";

export default function FounderOfferPill() {
  const [s, setS] = useState(null);
  const [alreadyClaimed, setAlreadyClaimed] = useState(false);
  useEffect(() => {
    let alive = true;
    const refresh = () => api.get("/founder-offer/status")
      .then((r) => { if (alive) setS(r.data); })
      .catch(() => {});
    refresh();
    const t = setInterval(refresh, 60_000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  useEffect(() => {
    if (!getToken()) return; // anonymous Landing visitor — nothing to check
    let alive = true;
    const checkClaimed = () => api.get("/founder-offer/user-status")
      .then((r) => { if (alive) setAlreadyClaimed((r.data?.repos_claimed ?? 0) > 0); })
      .catch(() => {});
    checkClaimed();
    const t = setInterval(checkClaimed, 60_000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  if (!s || !s.is_active || (s.remaining ?? 0) <= 0) return null;
  if (alreadyClaimed) return null;
  const color = s.remaining <= 10 ? "#ef4444"
              : s.remaining <= 50 ? "#f97316"
              : "#22c55e";
  return (
    <a
      data-testid="founder-offer-pill"
      href="/dashboard?action=connect-repo&utm_source=projects_pill&utm_campaign=onboarding"
      title="Connect a repo to claim your free founder SEO fix"
      style={{
        display: "inline-flex", alignItems: "center", gap: 8,
        padding: "6px 12px", borderRadius: 999,
        background: "rgba(234,179,8,0.10)",
        border: "1px solid rgba(234,179,8,0.40)",
        color, fontSize: 11, fontWeight: 600,
        fontFamily: "'JetBrains Mono', monospace",
        letterSpacing: "0.04em", textDecoration: "none",
        whiteSpace: "nowrap",
      }}
    >
      🎁 {s.remaining} of {s.total} free SEO fixes for founders left
    </a>
  );
}
