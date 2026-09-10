/**
 * src/sanitize.ts — client-side redaction pass. This is layer 1 of 3
 * (extension → backend server-side pass in
 * `routers/admin_context_bridge.py::_sanitize` → same regex family,
 * kept in sync deliberately). Never rely on this alone: the backend
 * NEVER trusts a client-reported "already sanitized" payload as
 * truly clean.
 */
export interface SanitizeResult {
  text: string;
  redactionCount: number;
}

// Order matters: specific key=value patterns before the generic
// opaque-token catch-alls, so a `password=` field gets the more
// useful [REDACTED_PASSWORD] label instead of a generic one.
const PATTERNS: Array<[RegExp, string]> = [
  [/\b(password|passwd|pwd)\s*[=:]\s*\S+/gi, "[REDACTED_PASSWORD]"],
  [/\bauthorization\s*:\s*bearer\s+\S+/gi, "[REDACTED_AUTH_HEADER]"],
  [/\b(api[_-]?key|secret|token|access[_-]?key)\s*[=:]\s*\S+/gi, "[REDACTED_SECRET]"],
  [/\bsk-[A-Za-z0-9_-]{16,}\b/g, "[REDACTED_KEY]"],
  [/\bgh[pousr]_[A-Za-z0-9]{20,}\b/g, "[REDACTED_GH_TOKEN]"],
  [/\bAKIA[0-9A-Z]{12,}\b/g, "[REDACTED_AWS_KEY]"],
  // Generic JWT-shaped token (three dot-separated base64url segments).
  [/\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b/g, "[REDACTED_JWT]"],
];

export function sanitize(input: string): SanitizeResult {
  if (!input) return { text: "", redactionCount: 0 };
  let out = input;
  let redactionCount = 0;
  for (const [pattern, replacement] of PATTERNS) {
    out = out.replace(pattern, () => {
      redactionCount += 1;
      return replacement;
    });
  }
  return { text: out, redactionCount };
}
