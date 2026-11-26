/**
 * src/api.ts — thin fetch wrapper for this extension's two backend
 * calls: login (reuses AUREM's existing JWT auth, no new backend
 * credential storage) and the context-bridge POST.
 *
 * Part VII fix 4(b), server half — the bridge no longer sends the
 * 7-day session JWT. `sendContextViaBridge()` mints a 15-minute
 * `scope: "context_bridge"` token at POST /auth/context-bridge-token
 * (from the session JWT) and sends THAT to the bridge endpoint, which
 * rejects session JWTs. The bridge token lives in
 * chrome.storage.session under its own key, is dropped once its `exp`
 * passes, and is only handed out while a session token is still
 * stored — so signing out (which clears the session token) also
 * strands any cached bridge token. The session token's own storage
 * (local vs session) is the part7 client half, untouched here.
 *
 * Part VII fixes:
 *  - 4(a) MFA: /auth/login returns { mfa_required, mfa_token } for
 *    MFA-protected admins; login() now returns that challenge instead
 *    of throwing "use an account without 2FA", and verifyMfa()
 *    trades the 5-minute mfa_pending token + TOTP/backup code for the
 *    real session JWT at /auth/login/2fa-verify. No fake endpoint —
 *    both legs are the backend's existing Iter 212m-20 flow.
 *  - 4(b) Token storage: the JWT now lives in chrome.storage.session
 *    (cleared when the browser closes) instead of chrome.storage.local,
 *    and getStoredAuth() drops it once its `exp` passes. This bounds
 *    persistence — it does NOT shorten the token itself, which is still
 *    a server-issued 7-day JWT (see FIX-REPORT.md: backend-first item).
 *  - 4(c) The `is_admin` checks below are UX gating only. The server
 *    enforces admin on POST /admin/ora/context-bridge itself
 *    (routers/admin_context_bridge.py: router-level
 *    Depends(require_admin_dep) + current_dev in the handler), so a
 *    non-admin token is rejected server-side even if this client
 *    check were bypassed.
 */
const STORAGE_KEYS = {
    apiBase: "aurem_api_base",
    token: "aurem_token",
    email: "aurem_email",
    bridgeToken: "aurem_bridge_token",
};
/** True when the JWT's `exp` claim has passed (expiry only — the
 *  signature is still the server's job). Unparseable tokens are
 *  treated as expired: fail closed, force a fresh login. */
function _tokenExpired(token) {
    try {
        const payloadSegment = token.split(".")[1];
        if (!payloadSegment)
            return true;
        const json = JSON.parse(atob(payloadSegment.replace(/-/g, "+").replace(/_/g, "/")));
        const exp = json?.exp;
        if (typeof exp !== "number")
            return true;
        return Date.now() / 1000 >= exp;
    }
    catch {
        return true;
    }
}
export async function getStoredAuth() {
    const local = await chrome.storage.local.get([
        STORAGE_KEYS.apiBase,
        STORAGE_KEYS.email,
    ]);
    // Token lives in session storage (4b) — cleared on browser restart.
    const session = await chrome.storage.session.get([STORAGE_KEYS.token]);
    let token = session[STORAGE_KEYS.token];
    if (token && _tokenExpired(token)) {
        await chrome.storage.session.remove([STORAGE_KEYS.token]);
        token = undefined;
    }
    return {
        apiBase: local[STORAGE_KEYS.apiBase],
        token,
        email: local[STORAGE_KEYS.email],
    };
}
export async function setApiBase(apiBase) {
    await chrome.storage.local.set({
        [STORAGE_KEYS.apiBase]: apiBase.trim().replace(/\/+$/, ""),
    });
}
async function _storeSession(email, token) {
    await chrome.storage.session.set({ [STORAGE_KEYS.token]: token });
    await chrome.storage.local.set({ [STORAGE_KEYS.email]: email });
}
export async function login(apiBase, email, password) {
    const res = await fetch(`${apiBase}/api/aurem-dev/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        throw new Error(data?.detail || `Login failed (${res.status})`);
    }
    // 4(a) — MFA challenge, not a dead end: the caller collects the
    // TOTP/backup code and finishes via verifyMfa() below.
    if (data.mfa_required) {
        if (!data.mfa_token) {
            throw new Error("Server asked for 2FA but returned no MFA token.");
        }
        return { mfaRequired: true, mfaToken: data.mfa_token };
    }
    if (!data.is_admin) {
        throw new Error("This account isn't an AUREM admin/founder — the Context Bridge is admin-only.");
    }
    await _storeSession(email, data.token);
    return { mfaRequired: false };
}
/** Second MFA leg: trade the mfa_pending token + 6-digit TOTP code
 *  (or a backup code) for the real session JWT. Same response shape
 *  as /auth/login, including `is_admin` — enforced here as UX gating
 *  (the server re-enforces admin on every context-bridge call). */
export async function verifyMfa(apiBase, email, mfaToken, code, useBackupCode = false) {
    const body = { mfa_token: mfaToken };
    if (useBackupCode) {
        body.backup_code = code.trim();
    }
    else {
        body.code = code.replace(/\D/g, "");
    }
    const res = await fetch(`${apiBase}/api/aurem-dev/auth/login/2fa-verify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        throw new Error(data?.detail || `2FA verification failed (${res.status})`);
    }
    if (!data.is_admin) {
        throw new Error("This account isn't an AUREM admin/founder — the Context Bridge is admin-only.");
    }
    await _storeSession(email, data.token);
}
export async function logout() {
    await chrome.storage.session.remove([STORAGE_KEYS.token]);
    // P1j — clear the cached bridge token too: it is bound to the
    // session being logged out of and must not survive it.
    await chrome.storage.session.remove([STORAGE_KEYS.bridgeToken]);
    await chrome.storage.local.remove([STORAGE_KEYS.email]);
}
export async function sendContext(apiBase, token, payload) {
    const res = await fetch(`${apiBase}/api/aurem-dev/admin/ora/context-bridge`, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        const err = new Error(data?.detail || `Send failed (${res.status})`);
        err.status = res.status;
        throw err;
    }
    return data;
}
// ── Part VII fix 4(b), server half: short-lived scoped bridge token ──
/** True when a bridge token's `exp` claim has passed (expiry only —
 *  the signature is still the server's job). Unparseable tokens are
 *  treated as expired: fail closed, mint a fresh one. (Named
 *  `_bridgeTokenExpired`, not `_tokenExpired`, so this helper does not
 *  collide with the session-token expiry helper in the part7 client
 *  half — the combiner folds the two together.) */
function _bridgeTokenExpired(token) {
    try {
        const payloadSegment = token.split(".")[1];
        if (!payloadSegment)
            return true;
        const json = JSON.parse(atob(payloadSegment.replace(/-/g, "+").replace(/_/g, "/")));
        const exp = json?.exp;
        if (typeof exp !== "number")
            return true;
        return Date.now() / 1000 >= exp;
    }
    catch {
        return true;
    }
}
/** Mint a 15-minute `scope: "context_bridge"` token from the session
 *  JWT. POST /api/aurem-dev/auth/context-bridge-token (admin-only,
 *  server side). */
export async function mintBridgeToken(apiBase, sessionToken) {
    const res = await fetch(`${apiBase}/api/aurem-dev/auth/context-bridge-token`, {
        method: "POST",
        headers: { Authorization: `Bearer ${sessionToken}` },
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        const err = new Error(data?.detail || `Bridge token mint failed (${res.status})`);
        err.status = res.status;
        throw err;
    }
    if (!data.token || data.scope !== "context_bridge") {
        throw new Error("Server returned a malformed bridge token.");
    }
    return { token: data.token, expiresIn: data.expires_in, scope: data.scope };
}
/** Return a usable bridge token: the cached one from session storage
 *  when it is still unexpired, otherwise a freshly minted one (which
 *  is then cached). Refuses outright when no session token is stored
 *  — so a bridge token cached before logout can never be used after
 *  it. Callers pass the session token from `getStoredAuth()`. */
export async function getBridgeToken(apiBase, sessionToken) {
    const stored = await getStoredAuth();
    if (!stored.token) {
        await chrome.storage.session.remove([STORAGE_KEYS.bridgeToken]);
        throw new Error("Not signed in — please sign in again.");
    }
    const cached = await chrome.storage.session.get([STORAGE_KEYS.bridgeToken]);
    const cachedToken = cached[STORAGE_KEYS.bridgeToken];
    if (cachedToken && !_bridgeTokenExpired(cachedToken)) {
        return cachedToken;
    }
    const minted = await mintBridgeToken(apiBase, sessionToken);
    await chrome.storage.session.set({ [STORAGE_KEYS.bridgeToken]: minted.token });
    return minted.token;
}
/** Drop the cached bridge token (e.g. after a 401, so the next send
 *  mints fresh instead of retrying a dead token). */
export async function clearBridgeToken() {
    await chrome.storage.session.remove([STORAGE_KEYS.bridgeToken]);
}
/** Send context using a bridge token instead of the session JWT.
 *  On a 401 the cached bridge token is dropped and the send is
 *  retried ONCE with a freshly minted token; if that also 401s the
 *  error propagates (the popup treats 401 as "session expired —
 *  sign in again", as before). */
export async function sendContextViaBridge(apiBase, sessionToken, payload) {
    const bridgeToken = await getBridgeToken(apiBase, sessionToken);
    try {
        return await sendContext(apiBase, bridgeToken, payload);
    }
    catch (e) {
        if (e?.status !== 401)
            throw e;
        await clearBridgeToken();
        const fresh = await getBridgeToken(apiBase, sessionToken);
        return await sendContext(apiBase, fresh, payload);
    }
}
