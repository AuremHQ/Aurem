/**
 * src/api.ts — thin fetch wrapper for this extension's two backend
 * calls: login (reuses AUREM's existing JWT auth, no new backend
 * credential storage) and the context-bridge POST.
 */
const STORAGE_KEYS = {
  apiBase: "aurem_api_base",
  token: "aurem_token",
  email: "aurem_email",
};

export interface StoredAuth {
  apiBase?: string;
  token?: string;
  email?: string;
}

export async function getStoredAuth(): Promise<StoredAuth> {
  const data = await chrome.storage.local.get([
    STORAGE_KEYS.apiBase,
    STORAGE_KEYS.token,
    STORAGE_KEYS.email,
  ]);
  return {
    apiBase: data[STORAGE_KEYS.apiBase],
    token: data[STORAGE_KEYS.token],
    email: data[STORAGE_KEYS.email],
  };
}

export async function setApiBase(apiBase: string): Promise<void> {
  await chrome.storage.local.set({
    [STORAGE_KEYS.apiBase]: apiBase.trim().replace(/\/+$/, ""),
  });
}

export async function login(
  apiBase: string,
  email: string,
  password: string
): Promise<void> {
  const res = await fetch(`${apiBase}/api/aurem-dev/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data?.detail || `Login failed (${res.status})`);
  }
  if (data.mfa_required) {
    throw new Error(
      "MFA-protected accounts aren't supported by this extension yet — use an account without 2FA."
    );
  }
  if (!data.is_admin) {
    throw new Error(
      "This account isn't an AUREM admin/founder — the Context Bridge is admin-only."
    );
  }
  await chrome.storage.local.set({
    [STORAGE_KEYS.token]: data.token,
    [STORAGE_KEYS.email]: email,
  });
}

export async function logout(): Promise<void> {
  await chrome.storage.local.remove([STORAGE_KEYS.token, STORAGE_KEYS.email]);
}

export interface ContextBridgePayload {
  session_id: string;
  url: string;
  title: string;
  visible_text: string;
  console_errors: string[];
  network_metrics: Array<Record<string, unknown>>;
  captured_at: string;
}

export interface ApiError extends Error {
  status?: number;
}

export async function sendContext(
  apiBase: string,
  token: string,
  payload: ContextBridgePayload
): Promise<{ ok: boolean; preview?: string }> {
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
    const err: ApiError = new Error(data?.detail || `Send failed (${res.status})`);
    err.status = res.status;
    throw err;
  }
  return data;
}
