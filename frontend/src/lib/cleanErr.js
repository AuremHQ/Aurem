// Iter 353 — shared error sanitizer for founder-facing admin pages.
// Prod origin hiccups (deploy rollouts, proxy 520s) can hand axios a raw
// Cloudflare/HTML body; rendering that verbatim leaks infra internals
// into the UI (founder audit: House Rules, Financials, API Keys pages).
export function cleanErr(e, fallback = "Request failed") {
  let msg = e?.response?.data?.detail
    ?? (typeof e?.response?.data === "string" ? e.response.data : null)
    ?? e?.message
    ?? fallback;
  if (typeof msg !== "string") {
    try { msg = JSON.stringify(msg); } catch { msg = String(msg); }
  }
  const looksLikeInfra =
    /<\s*(html|!doctype)|cloudflare|could not parse|bad gateway|origin web server/i.test(msg);
  if (looksLikeInfra || msg.length > 220) {
    const code = e?.response?.status;
    return `Server error${code ? ` (HTTP ${code})` : ""} — the backend returned an unreadable response (usually a transient deploy/restart). Retry in a few seconds; if it persists, check /admin/system-health.`;
  }
  return msg;
}

// 2026-10-27 — shared by MessageBubble.jsx's doSubmitShipTarget AND
// SaveToGitHubModal.jsx's repos/branches/submit error states. Reads
// the backend's structured GitHub-failure detail (services/
// github_errors.py::classify_github_failure returns
// `{code, message, retryable}`) or a plain-string legacy `detail`,
// and sanitizes raw Cloudflare/HTML infra text the same way cleanErr()
// does above. Deliberately NEVER falls back to `e.message`/
// `e.toString()` — a raw JS/axios error string (e.g. "Request failed
// with status code 502") is exactly the infra leak this exists to
// prevent; only the backend's own detail, or one fixed generic
// sentence, is ever returned. A plain object (not a string) must
// never reach a React child — this guarantees that.
export function sanitizeApiErrorDetail(e, fallback) {
  const detail = e?.response?.data?.detail;
  let msg = (detail && typeof detail === "object" && typeof detail.message === "string")
    ? detail.message
    : (typeof detail === "string" ? detail : null);
  if (msg == null) return fallback;
  const looksLikeInfra =
    /<\s*(html|!doctype)|cloudflare|could not parse|bad gateway|origin web server/i.test(msg);
  if (looksLikeInfra || msg.length > 220) {
    return "The server returned an unreadable response (usually a transient issue). Please retry in a moment.";
  }
  return msg;
}
