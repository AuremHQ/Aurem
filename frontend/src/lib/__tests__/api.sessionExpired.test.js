/**
 * api.sessionExpired.test.js — 2026-09-09b
 *
 * Founder-reported production incident: clicking "Continue with GitHub
 * App" (or even just opening the "Add repository" wizard, which
 * auto-checks legacy OAuth status on mount) force-logged the user out
 * of the ENTIRE AUREM session instead of proceeding with the GitHub
 * connection. Root cause: the global axios response interceptor in
 * api.js treats ANY 401 from ANY AUREM endpoint as "the AUREM JWT is
 * dead" and wipes the shared localStorage token + hard-navigates to
 * /login. A stale/revoked GITHUB OAuth token (an unrelated,
 * third-party credential) returning 401 from `/github/oauth/repos`
 * collided with that contract.
 *
 * Fix (two layers):
 *   1. Server: routers/github_oauth.py now returns 409 (not 401) for
 *      a revoked GitHub token — see
 *      test_iter_2026_09_09_oauth_repos_revoked_token.py.
 *   2. Client (this file/test): the interceptor also explicitly
 *      excludes `/github/oauth/*` and `/github/app/*` from the
 *      session-expired trigger, so no endpoint in this family can
 *      ever again force a full logout over a third-party token issue.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("aurem_token", "header.payload.signature");
});

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

async function fireInterceptorFor(url) {
  const { api } = await import("../api");
  const dispatchSpy = vi.spyOn(window, "dispatchEvent");
  api.defaults.adapter = () =>
    Promise.reject({
      response: { status: 401, data: { detail: "nope" } },
      config: { url },
      isAxiosError: true,
    });
  await api.get(url).catch(() => {});
  return dispatchSpy;
}

describe("api.js global 401 interceptor — GitHub endpoints must NOT force a full logout", () => {
  it("does NOT dispatch aurem:session-expired for a 401 on /github/oauth/repos", async () => {
    const dispatchSpy = await fireInterceptorFor("/github/oauth/repos");
    const fired = dispatchSpy.mock.calls.some(
      (c) => c[0]?.type === "aurem:session-expired",
    );
    expect(fired).toBe(false);
  });

  it("does NOT dispatch aurem:session-expired for a 401 on /github/app/installations", async () => {
    const dispatchSpy = await fireInterceptorFor("/github/app/installations");
    const fired = dispatchSpy.mock.calls.some(
      (c) => c[0]?.type === "aurem:session-expired",
    );
    expect(fired).toBe(false);
  });

  it("STILL dispatches aurem:session-expired for a 401 on an unrelated AUREM endpoint", async () => {
    const dispatchSpy = await fireInterceptorFor("/cto/projects");
    const fired = dispatchSpy.mock.calls.some(
      (c) => c[0]?.type === "aurem:session-expired",
    );
    expect(fired).toBe(true);
  });
});
