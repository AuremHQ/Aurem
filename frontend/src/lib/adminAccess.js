/**
 * adminAccess.js — what the admin shell should do when GET /admin/me fails.
 *
 * Only an explicit 401 (bad/expired token) or 403 (signed in, not an
 * admin) says anything about the user's access. Everything else — a 5xx
 * during a deploy or restart, a timeout, a dropped connection (no
 * `response` at all) — says nothing about access, so the shell must stay
 * put and offer a retry instead of telling a real admin they lost access
 * and bouncing them to /dashboard.
 */
export function adminMeFailureAction(err) {
  const status = err?.response?.status;
  if (status === 401) return "login";
  if (status === 403) return "denied";
  return "retry";
}
