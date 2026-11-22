/**
 * Login.jsx — Developer sign-in.
 */
import React, { useState, useEffect } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { LogIn, Github } from "lucide-react";
import GoogleIcon from "../components/GoogleIcon";
import AuthShell from "../components/AuthShell";
import usePageMeta from "../lib/usePageMeta";
import { api, setToken, setUser } from "../lib/api";
import { loginFreeOfferCta } from "../lib/billingCopy";
import RobotGuide, { RobotGuideKeyframes, escapeHtml } from "../components/RobotGuide";
import PasswordInput from "../components/PasswordInput";
import { ErrorState, RateLimitState, SavingButton, SessionExpiredBanner, SuccessState } from "../components/ui-states/UiStates.jsx";

export default function Login() {
  const [searchParams] = useSearchParams();
  // Honour ?next=… for safe in-app paths only (must start with /, not
  // // or a backslash — 2026-11-11, aligned with the backend's
  // _safe_next() in routers/github_oauth.py).
  const rawNext = searchParams.get("next") || "";
  const next = (rawNext.startsWith("/") && !rawNext.startsWith("//") && !rawNext.includes("\\"))
    ? rawNext : "/dashboard";
  // 2026-11-11 — Fix 8: a visitor arriving via ?next=/harness (the
  // download button's redirect, HarnessPage.jsx `handleDownloadClick`)
  // came from Aurem Loop, not ORA — the page must brand itself
  // "Aurem Loop" throughout (no "ORA by Aurem" anywhere) so it doesn't
  // look like a wrong-product redirect. Arriving with no `next=/harness`
  // (i.e. from ORA itself) keeps the existing "ORA by Aurem" branding
  // exactly as before — this is an ADDITIVE branding layer, same one
  // account/credentials either way.
  const isHarness = next === "/harness" || next.startsWith("/harness/")
    || next.startsWith("/harness?") || next.startsWith("/harness#");
  // 2026-11-11 testing-agent finding: the login<->signup cross-links
  // were hardcoded to plain /signup and /login, dropping `next` — a
  // harness visitor without an account who clicked "Get started"
  // landed back on the plain ORA-branded /signup and, after signing
  // up, on /dashboard instead of /harness. Append `?next=` whenever
  // it isn't the default, so switching forms (or signing up from
  // here) keeps both the branding AND the eventual redirect intact.
  const crossLinkQuery = next !== "/dashboard" ? `?next=${encodeURIComponent(next)}` : "";

  usePageMeta({
    title: isHarness ? "Sign in · Aurem Loop" : "Sign in · ORA by Aurem",
    description: isHarness
      ? "Sign in to download Aurem Loop — the same free account also gives you access to ORA."
      : "Sign in to your AUREM account and continue shipping features to your GitHub repo with an autonomous AI engineer.",
    canonical: (typeof window !== "undefined" ? window.location.origin : "") + "/login",
  });
  const navigate = useNavigate();
  // Iter 113 — friendly banner when user cancelled the GitHub OAuth flow
  // and was sent back here.
  const cancelled = searchParams.get("github") === "cancelled";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
const [rateLimited, setRateLimited] = useState(null);
// Iter 2026-11-20 — 429 (Too Many Requests): surface the library RateLimitState
// instead of a generic error, and offer the password-reset path as the alternative.
function handleRateLimit(e) {
  if (e?.response?.status !== 429) return false;
  const retryAfter = Number(e.response?.headers?.["retry-after"]);
  setRateLimited({ retryInSeconds: Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : 45 });
  setError(null);
  return true;
}
  // Iter 388t · GDPR self-delete flow lands here with ?deleted=1.
  // Surface a one-shot success banner (auto-dismisses on next Tab).
  const [deletedBanner, setDeletedBanner] = useState(
    () => searchParams.get("deleted") === "1",
  );
  // Iter 212m-187 — admin-editable welcome message
  const [welcomeMsg, setWelcomeMsg] = useState("");
  useEffect(() => {
    api.get("/auth/robot-guide")
      .then((r) => setWelcomeMsg(r.data?.login_message || ""))
      .catch(() => {});
  }, []);
  // Iter 212m-20 — Admin 2FA challenge state. `null` = normal email/pw
  // form. `{ mfa_token, email }` = the password was correct but the
  // account has TOTP enabled, so we now collect the 6-digit code.
  const [mfaState, setMfaState] = useState(null);
  const [mfaCode,  setMfaCode]  = useState("");
  const [useBackup, setUseBackup] = useState(false);
  // 2026-08-19 — self-service forgot-password toggle.
  const [forgotMode, setForgotMode] = useState(false);
  const [forgotBusy, setForgotBusy] = useState(false);
  const [forgotDone, setForgotDone] = useState(false);

  async function submitForgot(e) {
    e.preventDefault();
    setForgotBusy(true);
    setError(null);
    try {
      await api.post("/auth/forgot-password", { email: email.trim() });
      setForgotDone(true);
    } catch (e) {
      if (!handleRateLimit(e)) setError(e?.response?.data?.detail || "Could not send reset link. Try again.");
    } finally {
      setForgotBusy(false);
    }
  }

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api.post("/auth/login", { email: email.trim(), password });
      // Iter 212m-20 — admin 2FA gate. Switch to the 2FA step instead
      // of issuing a session.
      if (r.data?.mfa_required && r.data?.mfa_token) {
        setMfaState({ mfa_token: r.data.mfa_token, email: r.data.email || email });
        setBusy(false);
        return;
      }
      setToken(r.data.token);
      setUser({
        user_id: r.data.user_id,
        email: r.data.email,
        name: r.data.name,
        tier: r.data.tier,
        tokens_remaining: r.data.tokens_remaining,
      });
      try { localStorage.setItem("aurem_just_logged_in", "1"); } catch { /* ignore */ }
      // 2026-02 — Clean-start fix. Read-and-cleared once by Shell.jsx's
      // session-init effect so a fresh login always opens a blank chat
      // instead of silently reopening whatever conversation (possibly
      // a failed one) was last cached for this browser.
      try { localStorage.setItem("aurem_fresh_login", "1"); } catch { /* ignore */ }
      // Iter 212m-235 — Track-based routing on LOGIN (there is no
      // signup-time prompt anymore; /choose-track was removed in
      // Iter 390). Fetch the user's persisted track from /auth/me
      // and route accordingly. If ?next=… was set explicitly, that
      // always wins. Existing users on track="personal" still land
      // on /build so their workspace is preserved.
      if (next !== "/dashboard") {
        navigate(next, { replace: true });
      } else {
        try {
          const me = await api.get("/auth/me");
          const track = me.data?.user?.track;
          navigate(track === "personal" ? "/build" : "/dashboard", { replace: true });
        } catch {
          navigate("/dashboard", { replace: true });
        }
      }
    } catch (e) {
      if (!handleRateLimit(e)) setError(e?.response?.data?.detail || "Sign in failed. Try again.");
    } finally {
      setBusy(false);
    }
  }

  // Iter 212m-20 — submit the second-leg verify with the 6-digit code
  // (or a backup recovery code) in exchange for the real session JWT.
  async function submitMfa(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const payload = { mfa_token: mfaState.mfa_token };
    if (useBackup) payload.backup_code = mfaCode.trim();
    else           payload.code        = mfaCode.replace(/\D/g, "");
    try {
      const r = await api.post("/auth/login/2fa-verify", payload);
      setToken(r.data.token);
      setUser({
        user_id: r.data.user_id,
        email: r.data.email,
        name: r.data.name,
        tier: r.data.tier,
        tokens_remaining: r.data.tokens_remaining,
      });
      try { localStorage.setItem("aurem_just_logged_in", "1"); } catch { /* ignore */ }
      try { localStorage.setItem("aurem_fresh_login", "1"); } catch { /* ignore */ }
      // Iter 212m-235 — same track-based routing as the primary
      // login handler above.
      if (next !== "/dashboard") {
        navigate(next, { replace: true });
      } else {
        try {
          const me = await api.get("/auth/me");
          const track = me.data?.user?.track;
          navigate(track === "personal" ? "/build" : "/dashboard", { replace: true });
        } catch {
          navigate("/dashboard", { replace: true });
        }
      }
    } catch (e) {
      if (!handleRateLimit(e)) setError(e?.response?.data?.detail || "Invalid 2FA code. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell
      brand={isHarness ? "Aurem Loop" : "ORA by Aurem"}
      homeHref={isHarness ? "/harness" : "/"}
      secondaryCta={
        <Link to={`/signup${crossLinkQuery}`} data-testid="auth-nav-signup" className="btn-primary" style={{ fontSize: 12 }}>
          Get started
        </Link>
      }
    >
      <section style={{ maxWidth: 440, margin: "20px auto" }}>
        {deletedBanner && (
        <>
          <SuccessState title="Your account has been permanently deleted." nextLabel="Dismiss" onNext={() => setDeletedBanner(false)} />
          <p className="ora-ui-text" style={{ marginTop: 8 }}>
            All associated data has been purged from our systems. You can create a new account at any time.
          </p>
        </>
      )}
        <div style={{ textAlign: "center", marginBottom: 28 }}>
          <span className="eyebrow">sign in</span>
          <h1 className="serif" style={{ fontSize: 32, marginTop: 10 }}>
            {isHarness ? "Your code. Your PC. Your model." : "Welcome back, builder."}
          </h1>
          {isHarness ? (
            <p style={{ fontSize: 13, color: "var(--text-dim)" }}>
              With weight-space refine loop. Your local model improves itself on each run.
            </p>
          ) : (
            <p style={{ fontSize: 13, color: "var(--text-dim)" }}>
              Sign in to your <strong>AUREM</strong> account
              <span style={{ color: "var(--text-dim)", opacity: 0.7 }}> — for developers</span>.
            </p>
          )}
        </div>

        {isHarness && (
          <div
            data-testid="login-harness-guidelines"
            style={{
              background: "rgba(245,158,11,0.08)", border: "1px solid rgba(245,158,11,0.25)",
              borderRadius: 8, padding: "12px 14px", marginBottom: 20,
              fontSize: 12.5, lineHeight: 1.6, color: "var(--text-dim)",
            }}
          >
            One account, two things: sign in here, then you&rsquo;ll land back on the Aurem Loop
            download page — it&rsquo;s free and runs on your own PC. This same login also works for ORA.
          </div>
        )}

        <div className="card" data-testid="login-card" style={{
          background: "rgba(20, 20, 28, 0.55)",
          backdropFilter: "blur(10px)",
          border: "1px solid rgba(255,255,255,0.06)",
        }}>
          <RobotGuideKeyframes />
          <RobotGuide
            testid="login-robot-guide"
            brand={isHarness ? "AUREM LOOP" : "ORA"}
            kind={error ? "error" : "info"}
            message={
              error
                ? `Hmm — <strong>${escapeHtml(error)}</strong>. Try again, or use GitHub above.`
                : cancelled
                  ? `No worries — GitHub sign-in was cancelled. Try again, or sign in with email below. <span class="ora-arrow">👇</span>`
                  : email && password.length >= 6
                    ? `Looks good — hit <strong>Sign in</strong> when you&rsquo;re ready. <span class="ora-arrow">👇</span>`
                    : email
                      ? `Now enter your <strong>password</strong> and sign in. <span class="ora-arrow">👇</span>`
                      : (welcomeMsg || (isHarness
                          ? `Sign in to pick up your <strong>Aurem Loop</strong> download <span class="ora-arrow">👇</span> — or use GitHub/Google above, one tap, no password.`
                          : `<strong>Fastest way:</strong> click <strong>Continue with GitHub</strong> below <span class="ora-arrow">👇</span> — one tap, no password.`))
            }
          />
          {/* Iter 113 — friendly banner when GitHub OAuth was cancelled */}
          {cancelled && (
            <div data-testid="login-github-cancelled" style={{
              padding: "10px 12px", marginBottom: 12,
              borderRadius: 4,
              background: "rgba(255,138,42,0.08)",
              border: "1px solid rgba(255,138,42,0.35)",
              color: "#ff8a2a", fontSize: 12, lineHeight: 1.5,
            }}>
              GitHub sign-in cancelled. You can try again or use email below.
            </div>
          )}
          {/* 2026-08-28 — flipped to AUREM's own Google OAuth (routers/google_oauth.py),
              replacing the Emergent-managed broker so users see AUREM's own consent screen. */}
          <button
            type="button"
            data-testid="login-google-oauth"
            onClick={() => {
              const base = window.location.origin;
              window.location.href = `${base}/api/aurem-dev/google/oauth/start?intent=login&next=${encodeURIComponent(next)}`;
            }}
            style={{
              padding: "12px 14px", marginBottom: 12,
              borderRadius: 4, cursor: "pointer",
              background: "#fff", color: "#1f1f1f",
              border: "1px solid #dadce0",
              fontWeight: 600, fontSize: 13,
              display: "flex", alignItems: "center", gap: 8,
              justifyContent: "center", width: "100%",
              fontFamily: "'JetBrains Mono', monospace",
              letterSpacing: "0.04em",
            }}
          >
            <GoogleIcon size={16} /> Continue with Google
          </button>
          {/* Iter 50 — GitHub OAuth-first CTA (signup-killer removed) */}
          <button
            type="button"
            data-testid="login-github-oauth"
            onClick={async () => {
              // 2026-08-01 — funnel telemetry: cta_click. Fires before
              // the redirect; uses fetch keepalive so it survives.
              try {
                const { trackFunnel, withFunnelParams } = await import("../lib/githubFunnel");
                await trackFunnel("cta_click", "login", { intent: "login" });
                // Use the live origin so the OAuth callback returns to
                // whichever domain the user actually loaded the app from
                // (preview pod, auremcto.com, custom domain). (2026-11-21
                // — Aurem Deploy: the baked REACT_APP_BACKEND_URL is gone
                // from the client bundle; same-origin /api everywhere.)
                const base = window.location.origin;
                // Iter 113 — pass intent=login so backend redirects to
                // /login (not /signup) if the user clicks Cancel on
                // GitHub's consent screen.
                // 2026-11-11 — Fix 7: carry ?next= through so a
                // signup/login started from /harness returns there
                // instead of always landing on /dashboard.
                const url = withFunnelParams(
                  `${base}/api/aurem-dev/github/oauth/connect?signup=1&intent=login&next=${encodeURIComponent(next)}`,
                  "login",
                );
                window.location.href = url;
              } catch {
                // Telemetry lib failure MUST NOT block the OAuth flow.
                const base = window.location.origin;
                window.location.href = `${base}/api/aurem-dev/github/oauth/connect?signup=1&intent=login&next=${encodeURIComponent(next)}`;
              }
            }}
            style={{
              padding: "12px 14px", marginBottom: 16,
              borderRadius: 4, cursor: "pointer",
              background: "#0d1117", color: "#fff",
              border: "1px solid #30363d",
              fontWeight: 600, fontSize: 13,
              display: "flex", alignItems: "center", gap: 8,
              justifyContent: "center", width: "100%",
              fontFamily: "'JetBrains Mono', monospace",
              letterSpacing: "0.04em",
            }}
          >
            <Github size={14} /> Continue with GitHub
          </button>
          <div style={{
            display: "flex", alignItems: "center", gap: 10,
            margin: "0 0 16px 0", color: "var(--text-faint)",
            fontSize: 10, letterSpacing: "0.1em",
          }}>
            <div style={{ flex: 1, height: 1, background: "rgba(255,255,255,0.08)" }} />
            <span>OR EMAIL</span>
            <div style={{ flex: 1, height: 1, background: "rgba(255,255,255,0.08)" }} />
          </div>
          <form onSubmit={submit} style={{ display: mfaState || forgotMode ? "none" : "grid", gap: 16 }}>
{searchParams.get("session") === "expired" && (<SessionExpiredBanner email={searchParams.get("email") || ""} onSignIn={() => document.querySelector("input[type=email]")?.focus()} />)}
            <label>
              <span className="label-mini">Email</span>
              <input
                data-testid="login-email"
                className="input"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@company.com"
              />
            </label>

            <label>
              <span className="label-mini">Password</span>
              <PasswordInput
                testId="login-password"
                required
                minLength={6}
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>

            <button
              type="button"
              data-testid="login-forgot-password-link"
              onClick={() => { setForgotMode(true); setError(null); setForgotDone(false); }}
              style={{
                background: "transparent", border: "none", padding: 0,
                color: "var(--accent-2)", cursor: "pointer",
                fontSize: 12, textAlign: "right", justifySelf: "end",
              }}
            >
              Forgot password?
            </button>

            {rateLimited ? (
        <RateLimitState retryInSeconds={rateLimited.retryInSeconds} alternativeLabel="Reset your password instead" onUnlockAlternative={() => { setRateLimited(null); setForgotMode(true); }} />
      ) : error ? (
        <ErrorState title="Sign-in failed" message={error} onRetry={() => { setError(null); document.querySelector("input[type=email]")?.focus(); }} />
      ) : null}

            <button
              type="submit"
              data-testid="login-submit"
              className="btn-primary"
              disabled={busy}
              style={{ justifyContent: "center" }}
            >
              <LogIn size={15} /> {busy ? "Signing in…" : "Sign in"}
            </button>
          </form>

          {/* 2026-08-19 — forgot-password mini-flow, same card. */}
          {forgotMode && !mfaState && (
            <form
              data-testid="login-forgot-password-form"
              onSubmit={submitForgot}
              style={{ display: "grid", gap: 16 }}
            >
              {forgotDone ? (
                <div data-testid="login-forgot-password-done" style={{
                  fontSize: 13, color: "var(--text-dim)",
                  padding: "10px 12px", borderRadius: 4,
                  background: "rgba(109,212,161,0.06)",
                  border: "1px solid rgba(109,212,161,0.25)",
                }}>
                  If that email exists, a reset link has been sent. Check your inbox.
                </div>
              ) : (
                <>
                  <label>
                    <span className="label-mini">Email</span>
                    <input
                      data-testid="login-forgot-password-email"
                      className="input"
                      type="email"
                      required
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      placeholder="you@company.com"
                    />
                  </label>
                  {rateLimited ? (
        <RateLimitState retryInSeconds={rateLimited.retryInSeconds} alternativeLabel="Back to sign in" onUnlockAlternative={() => { setRateLimited(null); setForgotMode(false); }} />
      ) : error ? (
        <ErrorState title="Could not send reset link" message={error} onRetry={() => { setError(null); document.querySelector("input[type=email]")?.focus(); }} />
      ) : null}
                  <button
                    type="submit"
                    data-testid="login-forgot-password-submit"
                    className="btn-primary"
                    disabled={forgotBusy}
                    style={{ justifyContent: "center" }}
                  >
                    {forgotBusy ? "Sending…" : "Send reset link"}
                  </button>
                </>
              )}
              <button
                type="button"
                data-testid="login-forgot-password-cancel"
                onClick={() => { setForgotMode(false); setForgotDone(false); setError(null); }}
                style={{
                  background: "transparent", border: "none",
                  color: "var(--text-faint)", cursor: "pointer",
                  padding: 0, fontSize: 11, justifySelf: "start",
                }}
              >
                ← Back to sign in
              </button>
            </form>
          )}

          {/* Iter 212m-20 — second leg of admin 2FA login. Shown only
              after the password step returned `mfa_required:true`. */}
          {mfaState && (
            <form
              data-testid="login-2fa-form"
              onSubmit={submitMfa}
              style={{ display: "grid", gap: 16 }}
            >
              <div style={{
                fontSize: 12, color: "var(--text-dim)",
                padding: "10px 12px", borderRadius: 4,
                background: "rgba(255,197,96,0.06)",
                border: "1px solid rgba(255,197,96,0.25)",
              }}>
                🔐 Two-factor required for <strong>{mfaState.email}</strong>.
                {useBackup
                  ? " Enter a backup code from when you enrolled."
                  : " Enter the 6-digit code from your authenticator app."}
              </div>
              <label>
                <span className="label-mini">
                  {useBackup ? "Backup code" : "Authenticator code"}
                </span>
                <input
                  data-testid={useBackup ? "login-2fa-backup-input" : "login-2fa-code-input"}
                  className="input"
                  type="text"
                  required
                  autoFocus
                  value={mfaCode}
                  onChange={(e) => setMfaCode(e.target.value)}
                  placeholder={useBackup ? "XXXX-XXXX-XXXX" : "000000"}
                  inputMode={useBackup ? "text" : "numeric"}
                  pattern={useBackup ? undefined : "[0-9]{6}"}
                  maxLength={useBackup ? 14 : 6}
                  style={{
                    fontFamily: "'JetBrains Mono', monospace",
                    letterSpacing: useBackup ? "0.12em" : "0.4em",
                    fontSize: 18,
                    textAlign: "center",
                  }}
                />
              </label>
              {rateLimited ? (
        <RateLimitState retryInSeconds={rateLimited.retryInSeconds} alternativeLabel="Use a backup code instead" onUnlockAlternative={() => { setRateLimited(null); setUseBackup(true); }} />
      ) : error ? (
        <ErrorState title="Verification failed" message={error} onRetry={() => { setError(null); document.querySelector('[data-testid="login-2fa-code-input"], [data-testid="login-2fa-backup-input"]')?.focus(); }} />
      ) : null}
              <button
                type="submit"
                data-testid="login-2fa-submit"
                className="btn-primary"
                disabled={busy || mfaCode.length < (useBackup ? 12 : 6)}
                style={{ justifyContent: "center" }}
              >
                <LogIn size={15} /> {busy ? "Verifying…" : "Verify & sign in"}
              </button>
              <div style={{
                display: "flex", justifyContent: "space-between",
                fontSize: 11, color: "var(--text-dim)",
              }}>
                <button
                  type="button"
                  data-testid="login-2fa-toggle-backup"
                  onClick={() => { setUseBackup((v) => !v); setMfaCode(""); setError(null); }}
                  style={{
                    background: "transparent", border: "none",
                    color: "var(--accent-2)", cursor: "pointer",
                    padding: 0, fontSize: 11,
                  }}
                >
                  {useBackup
                    ? "← Use authenticator code instead"
                    : "Use a backup code →"}
                </button>
                <button
                  type="button"
                  data-testid="login-2fa-cancel"
                  onClick={() => {
                    setMfaState(null); setMfaCode(""); setUseBackup(false);
                    setError(null);
                  }}
                  style={{
                    background: "transparent", border: "none",
                    color: "var(--text-faint)", cursor: "pointer",
                    padding: 0, fontSize: 11,
                  }}
                >
                  Cancel
                </button>
              </div>
            </form>
          )}

          <div style={{
            marginTop: 22, paddingTop: 18,
            borderTop: "1px solid var(--border)",
            textAlign: "center", fontSize: 13, color: "var(--text-dim)",
          }}>
            No account yet?{" "}
            <Link to={`/signup${crossLinkQuery}`} data-testid="login-to-signup">{loginFreeOfferCta()}</Link>
          </div>

          <div
            data-testid="login-legal-line"
            style={{ marginTop: 12, textAlign: "center", fontSize: 10.5, color: "var(--text-faint)" }}
          >
            {isHarness
              ? "© 2026 Polaris Built Inc. — Aurem Loop, made by AUREM"
              : "© 2026 Polaris Built Inc. — ORA by Aurem, made by AUREM"}
          </div>
        </div>
      </section>
    </AuthShell>
  );
}
