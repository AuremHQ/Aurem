// Order matters: specific key=value patterns before the generic
// opaque-token catch-alls, so a `password=` field gets the more
// useful [REDACTED_PASSWORD] label instead of a generic one.
const PATTERNS = [
    [/\b(password|passwd|pwd)\s*[=:]\s*\S+/gi, "[REDACTED_PASSWORD]"],
    [/\bauthorization\s*:\s*bearer\s+\S+/gi, "[REDACTED_AUTH_HEADER]"],
    [/\b(api[_-]?key|secret|token|access[_-]?key)\s*[=:]\s*\S+/gi, "[REDACTED_SECRET]"],
    [/\bsk-[A-Za-z0-9_-]{16,}\b/g, "[REDACTED_KEY]"],
    [/\bgh[pousr]_[A-Za-z0-9]{20,}\b/g, "[REDACTED_GH_TOKEN]"],
    [/\bAKIA[0-9A-Z]{12,}\b/g, "[REDACTED_AWS_KEY]"],
    // Generic JWT-shaped token (three dot-separated base64url segments).
    [/\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b/g, "[REDACTED_JWT]"],
];
export function sanitize(input) {
    if (!input)
        return { text: "", redactionCount: 0 };
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
