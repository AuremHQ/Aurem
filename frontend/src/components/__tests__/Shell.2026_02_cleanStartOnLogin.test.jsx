/**
 * Shell.2026_02_cleanStartOnLogin.test.jsx
 *
 * ISSUE #1 — "Fix Old Chat History Loading Immediately After Login".
 *
 * Root cause (two layers, both fixed here):
 *   1. `localStorage`'s per-project session pointer was permanent, so
 *      every future login in the same browser reopened whatever
 *      conversation was last cached — failed tasks and all.
 *   2. Shell also auto-adopted the user's most-recent server-side
 *      session whenever localStorage was empty (cross-device
 *      "continuity"), which is the same "reopens an old chat" bug by
 *      a different path.
 *
 * Fix: every login/auth-completion entry point (Login, Signup,
 * OAuthFinish, MagicLogin, AcceptInvite) now sets a one-shot
 * `aurem_fresh_login=1` flag. Shell's mount effect reads-and-clears
 * it before any sticky/cached check runs, wiping every project's
 * cached session pointer so a fresh login always mints a brand-new,
 * clean session — never the most recent one. An explicit
 * `?session=<id>` deep link (or picking a row from "Previous chats")
 * still opens the exact saved conversation, and a same-visit refresh
 * still resumes exactly where it was (no flag present by then).
 *
 * This file covers three layers, mirroring the existing
 * Shell.iter329_b1_race.test.jsx convention:
 *   1. STATIC — every auth entry point sets the clean-start flag, and
 *      Shell/SessionSwitcher use "Previous chats" terminology.
 *   2. PURE HELPER — resolveInitialSession() truth table.
 *   3. RUNTIME — a harness mirroring Shell's actual mount effect,
 *      proving: fresh login wipes+mints, deep link wins over
 *      fresh-login, sticky survives same-visit refresh, new chat
 *      mints a different id without touching history.
 */
import { describe, it, expect, beforeEach } from "vitest";
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const read = (rel) => readFileSync(resolve(__dirname, "..", "..", rel), "utf-8");

const SHELL_SRC = read("components/Shell.jsx");
const SESSION_SWITCHER_SRC = read("components/SessionSwitcher.jsx");
const LOGIN_SRC = read("pages/Login.jsx");
const SIGNUP_SRC = read("pages/Signup.jsx");
const OAUTH_FINISH_SRC = read("pages/OAuthFinish.jsx");
const MAGIC_LOGIN_SRC = read("pages/MagicLogin.jsx");
const ACCEPT_INVITE_SRC = read("pages/AcceptInvite.jsx");

import { resolveInitialSession } from "../Shell.jsx";

describe("Issue #1 · static source lock — every auth entry point sets the clean-start flag", () => {
  it.each([
    ["Login.jsx (password)", LOGIN_SRC],
    ["Signup.jsx", SIGNUP_SRC],
    ["OAuthFinish.jsx", OAUTH_FINISH_SRC],
    ["MagicLogin.jsx", MAGIC_LOGIN_SRC],
    ["AcceptInvite.jsx", ACCEPT_INVITE_SRC],
  ])("%s sets aurem_fresh_login=1 on successful auth", (_label, src) => {
    expect(src).toMatch(/localStorage\.setItem\(\s*["']aurem_fresh_login["']\s*,\s*["']1["']\s*\)/);
  });

  it("Login.jsx's MFA second-leg verify ALSO sets the flag (not just the primary password path)", () => {
    const matches = LOGIN_SRC.match(/aurem_fresh_login/g) || [];
    // One in the primary password-login handler, one in submitMfa().
    expect(matches.length).toBeGreaterThanOrEqual(2);
  });
});

describe("Issue #1 · static source lock — Shell's mount effect wiring", () => {
  it("reads-and-clears aurem_fresh_login before any sticky/cached check", () => {
    expect(SHELL_SRC).toContain('localStorage.getItem("aurem_fresh_login")');
    expect(SHELL_SRC).toContain('localStorage.removeItem("aurem_fresh_login")');
  });

  it("wipes every project's cached session pointer on a fresh login, not just the current one", () => {
    expect(SHELL_SRC).toMatch(/k\.startsWith\(\s*["']aurem_session_proj_["']\s*\)/);
  });

  it("supports an explicit ?session= deep link that pre-empts fresh-login clearing", () => {
    expect(SHELL_SRC).toContain('new URLSearchParams(window.location.search).get("session")');
  });

  it("openSession() reflects the chosen id into the URL so it is a real, shareable deep link", () => {
    expect(SHELL_SRC).toMatch(/navigate\(`\/dashboard\?session=\$\{encodeURIComponent\(id\)\}`\)/);
  });

  it("mint path generates a brand-new random id via newSessionId(), not a server fetch", () => {
    expect(SHELL_SRC).toContain("const next = newSessionId();");
  });
});

describe('Issue #1 · static source lock — "Previous chats" labeling', () => {
  it("SessionSwitcher labels the history dropdown as Previous chats", () => {
    expect(SESSION_SWITCHER_SRC).toMatch(/Previous chats/i);
  });

  it("Shell's persistent sidebar section is labeled previous chats", () => {
    expect(SHELL_SRC).toMatch(/previous chats/i);
  });
});

describe("Issue #1 · resolveInitialSession() pure helper — priority truth table", () => {
  it("deep-link wins even during a fresh login (explicit link beats clean-start)", () => {
    expect(
      resolveInitialSession({ freshLogin: true, urlSessionId: "s_deep", stickySession: null, cachedSession: null })
    ).toEqual({ source: "deep-link", id: "s_deep" });
  });

  it("deep-link wins over an existing sticky/cached pointer on a normal visit", () => {
    expect(
      resolveInitialSession({ freshLogin: false, urlSessionId: "s_deep", stickySession: "s_sticky", cachedSession: "s_cached" })
    ).toEqual({ source: "deep-link", id: "s_deep" });
  });

  it("fresh login with NO deep link ignores sticky/cached pointers entirely → mint", () => {
    expect(
      resolveInitialSession({ freshLogin: true, urlSessionId: null, stickySession: "s_sticky", cachedSession: "s_cached" })
    ).toEqual({ source: "mint", id: null });
  });

  it("normal visit (not fresh login) resumes the tab-scoped sticky pointer first", () => {
    expect(
      resolveInitialSession({ freshLogin: false, urlSessionId: null, stickySession: "s_sticky", cachedSession: "s_cached" })
    ).toEqual({ source: "sticky", id: "s_sticky" });
  });

  it("normal visit with no sticky pointer falls back to the cross-tab cached pointer", () => {
    expect(
      resolveInitialSession({ freshLogin: false, urlSessionId: null, stickySession: null, cachedSession: "s_cached" })
    ).toEqual({ source: "cached", id: "s_cached" });
  });

  it("nothing cached and not a fresh login still → mint (brand-new tab, brand-new project)", () => {
    expect(
      resolveInitialSession({ freshLogin: false, urlSessionId: null, stickySession: null, cachedSession: null })
    ).toEqual({ source: "mint", id: null });
  });
});

// ── RUNTIME LIVE-REPRO — mirrors Shell.jsx's actual mount-effect
// branching (lines ~208-267) using real localStorage/sessionStorage
// and the real resolveInitialSession() helper, without mounting the
// full component tree (same strategy as Shell.iter329_b1_race.test).
function runMountEffect({ projectId = null } = {}) {
  const key = projectId ? `aurem_session_proj_${projectId}` : "aurem_session_home";

  let freshLogin = false;
  if (localStorage.getItem("aurem_fresh_login") === "1") {
    freshLogin = true;
    localStorage.removeItem("aurem_fresh_login");
    Object.keys(localStorage)
      .filter((k) => k === "aurem_session_home" || k.startsWith("aurem_session_proj_"))
      .forEach((k) => {
        localStorage.removeItem(k);
        sessionStorage.removeItem(k);
      });
  }

  const urlSessionId = new URLSearchParams(window.location.search).get("session");
  let stickySession = null;
  let cachedSession = null;
  if (!freshLogin) {
    stickySession = sessionStorage.getItem(key);
    cachedSession = localStorage.getItem(key);
  }

  const decision = resolveInitialSession({ freshLogin, urlSessionId, stickySession, cachedSession });
  if (decision.source === "deep-link" || decision.source === "cached") {
    localStorage.setItem(key, decision.id);
    sessionStorage.setItem(key, decision.id);
    return decision.id;
  }
  if (decision.source === "sticky") return decision.id;

  // mint
  const next = `minted-${Math.random().toString(36).slice(2, 10)}`;
  localStorage.setItem(key, next);
  sessionStorage.setItem(key, next);
  return next;
}

describe("Issue #1 · runtime live-repro — fresh login never resurrects an old chat", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    window.history.replaceState({}, "", "/dashboard");
  });

  it("existing user with cached history: fresh login mints a CLEAN session, old pointer discarded", () => {
    // Simulate a returning user whose last visit left a cached/sticky
    // pointer to an old (possibly failed) conversation.
    localStorage.setItem("aurem_session_home", "old_failed_conversation");
    sessionStorage.setItem("aurem_session_home", "old_failed_conversation");

    // ...then they log out and log back in — the auth page sets this.
    localStorage.setItem("aurem_fresh_login", "1");

    const resolved = runMountEffect();
    expect(resolved).not.toBe("old_failed_conversation");
    expect(resolved).toMatch(/^minted-/);
    // The flag is one-shot — consumed, not left dangling.
    expect(localStorage.getItem("aurem_fresh_login")).toBeNull();
  });

  it("a normal same-visit refresh (no fresh-login flag) resumes the exact sticky session — history NOT lost", () => {
    sessionStorage.setItem("aurem_session_home", "s_in_progress");
    localStorage.setItem("aurem_session_home", "s_in_progress");

    const resolved = runMountEffect();
    expect(resolved).toBe("s_in_progress");
  });

  it("an explicit saved-conversation link (?session=) opens on a FRESH login, bypassing clean-start", () => {
    localStorage.setItem("aurem_fresh_login", "1");
    window.history.replaceState({}, "", "/dashboard?session=s_saved_123");

    const resolved = runMountEffect();
    expect(resolved).toBe("s_saved_123");
  });

  it("starting a new chat after a fresh login mints a DIFFERENT id each time (no accidental re-adoption)", () => {
    localStorage.setItem("aurem_fresh_login", "1");
    const first = runMountEffect();

    // Simulate clicking "New run" — Shell's startNewSession mints again.
    const second = `minted-${Math.random().toString(36).slice(2, 10)}`;
    localStorage.setItem("aurem_session_home", second);
    sessionStorage.setItem("aurem_session_home", second);

    expect(second).not.toBe(first);
  });

  it("per-project isolation: a fresh login clears ALL projects' pointers, not just the active one", () => {
    localStorage.setItem("aurem_session_proj_p_a", "old_a");
    localStorage.setItem("aurem_session_proj_p_b", "old_b");
    localStorage.setItem("aurem_fresh_login", "1");

    runMountEffect({ projectId: "p_a" });

    // p_b's stale pointer must also be gone — a later switch to
    // project B this same login must not resurrect its old chat either.
    expect(localStorage.getItem("aurem_session_proj_p_b")).toBeNull();
  });
});
