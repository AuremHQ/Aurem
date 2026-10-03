/**
 * checkupApi.js — client for the flag-gated /checkup endpoints.
 * Every call fails soft: a broken checkup must never break the app or chat.
 */
import { api, API_BASE, getToken } from "./api";

export async function fetchCheckupFlags() {
  // Anonymous visitors (landing/signup) have no token; a 401 here would fire
  // the global session-expired handler, so don't even ask.
  if (!getToken()) return {};
  try {
    const { data } = await api.get("/checkup/flags");
    return data || {};
  } catch {
    return {};
  }
}

export async function fetchLatest(projectId) {
  try {
    const { data } = await api.get("/checkup/latest", { params: { project_id: projectId } });
    return data;
  } catch {
    return null;
  }
}

export const ignoreItem = (projectId, itemId) =>
  api.post("/checkup/items/ignore", { project_id: projectId, item_id: itemId })
    .then((r) => !!r.data?.ok).catch(() => false);

export const answerQuestion = (projectId, table, answer) =>
  api.post("/checkup/answer", { project_id: projectId, table, answer })
    .then((r) => !!r.data?.ok).catch(() => false);

export const skipCheckup = (projectId) =>
  api.post("/checkup/skip", { project_id: projectId }).catch(() => null);

/** Parse an SSE body (`data: {...}\n\n` frames). Exported for tests. */
export function parseSseFrames(buffer) {
  const events = [];
  const parts = buffer.split("\n\n");
  const rest = parts.pop();
  for (const part of parts) {
    const line = part.split("\n").find((l) => l.startsWith("data: "));
    if (!line) continue;
    try { events.push(JSON.parse(line.slice(6))); } catch { /* skip a bad frame */ }
  }
  return { events, rest };
}

/**
 * Run a checkup, calling onEvent for each progress event. Resolves when the
 * stream ends. Always emits a final `error` event on failure so the UI can
 * show a friendly message instead of hanging.
 */
export async function streamCheckup(projectId, { onEvent, signal } = {}) {
  const emit = (e) => { try { onEvent && onEvent(e); } catch { /* UI errors must not kill the stream */ } };
  try {
    const res = await fetch(`${API_BASE}/checkup/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${getToken()}` },
      body: JSON.stringify({ project_id: projectId }),
      signal,
    });
    if (!res.ok || !res.body) {
      emit({ type: "error", message: res.status === 429
        ? "A checkup just ran. Give it a few seconds, then try again."
        : "We couldn't start the checkup right now. Your chat still works." });
      return;
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const { events, rest } = parseSseFrames(buf);
      buf = rest;
      events.forEach(emit);
    }
  } catch (e) {
    if (e && e.name === "AbortError") return;
    emit({ type: "error", message: "We lost the connection during the checkup. Your chat still works." });
  }
}
