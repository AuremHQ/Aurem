/**
 * streamChat.doneMetaCollision.test.js — 2026-09-05
 *
 * Founder-reported repro (2 screen recordings, on TWO different
 * production domains — auremcto.com and reverbainc.com — confirming
 * this is a pure frontend bug baked into the built bundle, not an
 * environment/config issue): the chat response text prints fully,
 * but the send/stop button never reverts from "Stop" back to
 * "Send" — it stays stuck on the rotating "Thinking…" phrase until
 * the 90s idle watchdog eventually force-cancels it.
 *
 * Root cause: `streamChat()`'s SSE dispatcher used an exclusive
 * if/else-if chain keyed on field PRESENCE:
 *
 *     if (payload.meta) onMeta?.(payload);
 *     else if (...) ...
 *     else if (payload.done) onDone?.(payload);
 *
 * Mid-stream "meta" events always set `meta: true` (a literal
 * boolean discriminator — see backend routers/chat/stream.py). But
 * the terminal "done" event ALSO independently carries an unrelated
 * nested `meta` object (quota/upgrade-CTA hints), e.g.:
 *
 *     {"done": true, "provider": "...", "meta": {}, ...}
 *
 * `{}` is truthy in JS, so `if (payload.meta)` matched the DONE
 * frame too — and since it's checked first, `onDone` never fired.
 * Fix: match the boolean discriminator exactly (`payload.meta ===
 * true`) so the two same-named-but-unrelated fields can't collide.
 */
import { describe, it, expect, vi } from "vitest";
import { streamChat } from "../api.js";

function sseBody(lines) {
  const text = lines.map((l) => `data: ${JSON.stringify(l)}\n\n`).join("");
  const encoder = new TextEncoder();
  const bytes = encoder.encode(text);
  let sent = false;
  return {
    getReader() {
      return {
        read: async () => {
          if (sent) return { done: true, value: undefined };
          sent = true;
          return { done: false, value: bytes };
        },
        cancel: async () => {},
      };
    },
  };
}

describe("2026-09-05 — done-event `meta:{}` no longer swallowed by onMeta", () => {
  it("t_real_production_shaped_stream_fires_onDone_not_just_onMeta", async () => {
    // Exact shape captured live from the preview backend for a plain
    // "hi" — the smallest possible repro.
    const frames = [
      { meta: true, session_id: "s1", provider: "aurem-cto" },
      { type: "mode", mode: "A" },
      { type: "intent", intent: { tier: "casual" } },
      { meta: true, session_id: "s1", provider: "intent-gateway-casual" },
      { token: "Hey! " },
      { token: "there!" },
      {
        done: true, provider: "intent-gateway-casual", session_id: "s1",
        meta: {}, council_recalled: 2, findings_saved: [], palette_nudges: [],
      },
    ];
    global.fetch = vi.fn().mockResolvedValue({
      ok: true, body: sseBody(frames),
    });

    const onMeta = vi.fn();
    const onToken = vi.fn();
    const onDone = vi.fn();
    const onError = vi.fn();

    await streamChat({
      prompt: "hi", sessionId: "s1",
      onMeta, onToken, onDone, onError,
    });

    expect(onError).not.toHaveBeenCalled();
    expect(onToken).toHaveBeenCalledTimes(2);
    // Exactly the 2 real boolean meta:true frames — NOT the done frame.
    expect(onMeta).toHaveBeenCalledTimes(2);
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(onDone).toHaveBeenCalledWith(
      expect.objectContaining({ done: true, meta: {} }),
    );
  });

  it("t_done_with_nonempty_meta_object_still_fires_onDone_not_onMeta", async () => {
    // Guard the other shape too — a done frame whose collision `meta`
    // key happens to be a non-empty object (checkout_url hint etc.)
    // must ALSO still route to onDone, not onMeta.
    const frames = [
      { token: "ok" },
      { done: true, session_id: "s2", meta: { checkout_url: "https://x" } },
    ];
    global.fetch = vi.fn().mockResolvedValue({ ok: true, body: sseBody(frames) });

    const onMeta = vi.fn();
    const onDone = vi.fn();
    await streamChat({ prompt: "hi", sessionId: "s2", onMeta, onDone });

    expect(onMeta).not.toHaveBeenCalled();
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("t_midstream_boolean_meta_events_still_route_to_onMeta_unaffected", async () => {
    const frames = [
      { meta: true, provider: "aurem-cto" },
      { meta: true, provider: "intent-gateway-casual" },
      { token: "hi" },
      { done: true, meta: {} },
    ];
    global.fetch = vi.fn().mockResolvedValue({ ok: true, body: sseBody(frames) });

    const onMeta = vi.fn();
    const onDone = vi.fn();
    await streamChat({ prompt: "hi", sessionId: "s3", onMeta, onDone });

    expect(onMeta).toHaveBeenCalledTimes(2);
    expect(onDone).toHaveBeenCalledTimes(1);
  });
});
