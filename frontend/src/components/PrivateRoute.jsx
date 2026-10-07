/**
 * PrivateRoute.jsx — 2026-02-11 · Gap Register item #42
 *
 * Router-level auth guard. Redirects to /login when no JWT is present
 * BEFORE the underlying page component mounts. Runs alongside (not
 * instead of) each page's own <Shell requireAuth> gate — defense in
 * depth: this cuts the "flash of broken page then redirect" UX gap.
 *
 * `<AdminRoute>` additionally checks the admin/founder flag before
 * rendering. Non-admins get redirected to /dashboard rather than
 * /login (they're logged in — they just don't have the right tier).
 *
 * Both guards preserve the current URL in a `?next=` param so the
 * user lands back where they intended after re-auth.
 *
 * IMPORTANT: this is a UX guard only. Backend `/api/aurem-dev/*`
 * endpoints still enforce their own JWT + role checks via
 * `current_dev()` / `require_admin()`. Editing the JS bundle to
 * bypass this component does NOT grant server-side access.
 */
import React from "react";
import { Navigate, useLocation } from "react-router-dom";
import { getToken, isAdminOrFounder, getCachedAdminAccess, fetchMyAdminAccess } from "../lib/api";

export function PrivateRoute({ children }) {
  const location = useLocation();
  const token = getToken();
  if (!token) {
    const path = location.pathname + location.search;
    const next = (path && path !== "/") ? `?next=${encodeURIComponent(path)}` : "";
    return <Navigate to={`/login${next}`} replace />;
  }
  return children;
}

/** AT3 — Owner keeps the instant synchronous `isAdminOrFounder()` path.
 * A non-founder who was invited onto the Admin Team (some `screens`
 * access, checked via the self-service `/admin/team/my-access`) must
 * also get through here, not just the Owner. That check is async, so
 * we read the localStorage cache first for an instant decision on
 * repeat visits, and only show a brief loading state on the very
 * first load this session before the fetch resolves. */
export function AdminRoute({ children }) {
  const location = useLocation();
  const token = getToken();
  const [access, setAccess] = React.useState(() => getCachedAdminAccess());
  const [loading, setLoading] = React.useState(access === null && !isAdminOrFounder());

  React.useEffect(() => {
    if (!token || isAdminOrFounder()) return;
    fetchMyAdminAccess().then((result) => {
      setAccess(result);
      setLoading(false);
    });
  }, [token]);

  if (!token) {
    const path = location.pathname + location.search;
    const next = (path && path !== "/") ? `?next=${encodeURIComponent(path)}` : "";
    return <Navigate to={`/login${next}`} replace />;
  }
  if (isAdminOrFounder()) return children;
  if (loading) return null;
  const hasTeamAccess = !!access && (access.is_owner || Object.keys(access.screens || {}).length > 0);
  if (!hasTeamAccess) {
    return <Navigate to="/dashboard" replace />;
  }
  return children;
}

export default PrivateRoute;
