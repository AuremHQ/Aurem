/**
 * AddProjectWizard.jsx — Iter 212m-5 — 3-step "Add Project" flow.
 *
 *   Step 1  Repo identify  (free-form owner/repo or full URL)
 *   Step 2  Install/select via the GitHub App
 *   Step 3  Confirm summary + project name → save → land in chat
 *
 * 2026-08-20 — founder's call: GitHub App is now the ONLY visible way
 * to connect a repo. The old PAT (Personal Access Token) fallback UI
 * was removed entirely from this flow. The backend still accepts a
 * github_token on /cto/projects/add (untouched, existing PAT-connected
 * projects keep working) — there's just no way to reach it from here
 * anymore.
 *
 * After successful save:
 *   - localStorage active project is set to the new project_id
 *   - parent's onAdded(projectId) is called (refreshes list / closes modal)
 *   - user is navigated to /dashboard so they can start chatting immediately
 */
import React, { useEffect, useState } from "react";
import {
  Github,
  Loader2,
  ArrowRight,
  ArrowLeft,
  X as XIcon,
  ShieldCheck,
  RefreshCw,
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { toast } from "./Toast";
import { setActiveProjectId } from "./TabBar";
import { metaLead, openaiGithubRepoConnected } from "../lib/analytics";
import useGitHubConnectStatus from "../hooks/useGitHubConnectStatus";

// ── Helpers ───────────────────────────────────────────────────────────

/** Parse a free-form `owner/repo` or `https://github.com/owner/repo` into
 *  canonical `{owner, repo, full_name, url}`. Returns null on invalid. */
export function parseRepoInput(raw) {
  const s = (raw || "")
    .trim()
    .replace(/^https?:\/\/github\.com\//i, "")
    .replace(/\.git$/i, "")
    .replace(/\/+$/, "");
  const parts = s.split("/").filter(Boolean);
  if (parts.length < 2) return null;
  return {
    owner:     parts[0],
    repo:      parts[1],
    full_name: `${parts[0]}/${parts[1]}`,
    url:       `https://github.com/${parts[0]}/${parts[1]}`,
  };
}

// ── Component ─────────────────────────────────────────────────────────

export default function AddProjectWizard({ onClose, onAdded }) {
  const navigate = useNavigate();
  // 2026-09-04 — founder-reported: this wizard made users TYPE the exact
  // owner/repo BEFORE they could even connect — backwards from GitHub's
  // own install flow (and Emergent's own Save-to-GitHub), where you
  // connect first and then pick from a real list, never typing anything.
  // Reordered: Step 1 is now "connect + pick from what GitHub grants",
  // Step 2 is the same confirm-name-and-save as before. Manual
  // owner/repo entry still exists as a fallback (e.g. a public repo the
  // App was never granted), just no longer the mandatory first step.
  const [step, setStep] = useState(1);
  const [manualEntry, setManualEntry] = useState(false);

  // Step-1 state
  const [repoInput, setRepoInput] = useState("");
  const typedRepo = parseRepoInput(repoInput);
  const [pickedRepo, setPickedRepo] = useState(null); // {owner,repo,full_name,url} from the GitHub picker
  const repo = pickedRepo || typedRepo;

  // Step-2 state
  const [projectName, setProjectName] = useState("");
  const [saving, setSaving] = useState(false);

  // ─── 2026-08 hardening (GitHub Connect: PERMANENT fix) ───────────
  // ONE shared hook (also used by NewUserWizard.jsx) — the wizard is a
  // pure function of the authoritative /github/app/status endpoint,
  // not a local postMessage/count-poll guess.
  const { status, connecting, timedOut, denied, startConnect, installUrl, freshInstallIds } = useGitHubConnectStatus();
  // 2026-09-09 — "full isolation" fix (founder-reported): this used
  // to flatMap `status.installations` — EVERY installation this
  // GitHub account has ever granted, across every past project. A
  // brand new project must only ever see repos from an installation
  // freshly confirmed in THIS wizard session (see
  // useGitHubConnectStatus's freshInstallIds), never stale history
  // from an unrelated earlier project.
  const freshInstallations = React.useMemo(
    () => (status.installations || []).filter(
      (inst) => freshInstallIds.includes(String(inst.installation_id)),
    ),
    [status.installations, freshInstallIds],
  );
  // 2026-08-27 — founder-confirmed workaround: some accounts get stuck
  // with an orphaned/already-installed App that never links (retrying
  // the same install popup replays the same stuck state). After the
  // first failed attempt, surface the manual reset path the founder
  // proved works: revoke the App on GitHub, then reinstall fresh.
  const [connectAttempts, setConnectAttempts] = useState(0);

  const allGrantedRepos = React.useMemo(() => {
    const out = [];
    for (const inst of freshInstallations) {
      for (const r of (inst.repositories || [])) out.push({ ...r, installation_id: inst.installation_id, github_login: inst.github_login });
    }
    return out;
  }, [freshInstallations]);

  // The specific installation (if any) that covers the current `repo`.
  // Non-null → App-install branch is available for this repo; the
  // Save button uses installation_id. Scoped to freshInstallations
  // for the same full-isolation reason as allGrantedRepos above.
  const installationForRepo = React.useMemo(() => {
    if (!repo) return null;
    for (const inst of freshInstallations) {
      const has = (inst.repositories || []).some(
        (r) => (r.full_name || "").toLowerCase() === repo.full_name.toLowerCase(),
      );
      if (has) return inst;
    }
    return null;
  }, [repo, freshInstallations]);

  function openAppInstallPopup() {
    setConnectAttempts((n) => n + 1);
    const r = startConnect();
    if (!r.ok) {
      toast({
        message: r.reason === "popup_blocked"
          ? "Popup blocked — please allow popups for this site and try again."
          : "Session expired — please log in again.",
        kind: "error",
      });
    }
  }

  // 2026-09-04 — one-click connect: the moment exactly one repo is
  // granted (fresh install, or the only repo on an existing
  // installation) auto-select it and jump straight to Step 2 — the
  // same "connect once, pick repo once, done" pattern as NewUserWizard
  // and Emergent's own Save-to-GitHub. Multiple granted repos still
  // show a real picker (there's an actual choice to make there).
  useEffect(() => {
    if (step === 1 && !manualEntry && !pickedRepo && allGrantedRepos.length === 1) {
      const r = allGrantedRepos[0];
      setPickedRepo(parseRepoInput(r.full_name));
      setStep(2);
    }
  }, [step, manualEntry, pickedRepo, allGrantedRepos]);

  function pickRepo(r) {
    setPickedRepo(parseRepoInput(r.full_name));
    setStep(2);
  }

  useEffect(() => {
    function onMessage(e) {
      const d = e.data;
      if (!d || d.type !== "aurem-app-installed") return;
      if (d.status === "err") {
        toast({
          message: d.err === "invalid_state"
            ? "Session expired during install — please try again."
            : d.err === "github_probe_failed"
              ? "GitHub couldn't verify the install. Try again."
              : "Install did not complete — please try again.",
          kind: "warn",
        });
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  // ── Auto-fill project name from repo name when entering step 2 ───
  useEffect(() => {
    if (step === 2 && !projectName.trim() && repo) {
      setProjectName(repo.repo);
    }
  }, [step, repo, projectName]);

  // ── Step-2 save ──────────────────────────────────────────────────
  async function handleSave() {
    if (!repo || !projectName.trim()) {
      toast({ message: "Fill in the project name.", kind: "warn" });
      return;
    }
    if (!installationForRepo) {
      toast({ message: "Install the GitHub App and grant access to this repo above first.", kind: "warn" });
      return;
    }
    setSaving(true);
    try {
      const payload = {
        name:            projectName.trim(),
        github_url:      repo.url,
        branch:          "main",
        installation_id: installationForRepo.installation_id,
      };
      const r = await api.post("/cto/projects/add", payload);
      const newProjectId = r.data?.project?.project_id || r.data?.project_id;
      // Iter 389 — Meta Pixel Lead. Fires only after backend confirms
      // the project was created (real intent signal, not a click).
      metaLead("project_added");
      // 2026-09 — OpenAI Ads github_repo_connected conversion.
      openaiGithubRepoConnected(newProjectId);
      toast({
        message: `Connected ${repo.full_name} — opening chat…`,
        kind:    "success",
      });
      if (newProjectId) {
        setActiveProjectId(newProjectId);
      }
      try { onAdded?.(newProjectId); } catch { /* noop */ }
      // Land directly in chat with this project active.
      navigate("/dashboard");
    } catch (e) {
      const d = e?.response?.data?.detail;
      const msg = typeof d === "object" && d?.message
        ? d.message
        : (typeof d === "string" ? d : "Connect failed");
      toast({ message: msg, kind: "error" });
    } finally {
      setSaving(false);
    }
  }

  // ── Render ───────────────────────────────────────────────────────

  return (
    <div
      onClick={onClose}
      data-testid="add-project-wizard-backdrop"
      style={{
        position: "fixed", inset: 0, zIndex: 9000,
        background: "rgba(0,0,0,0.68)",
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: 24,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        data-testid="add-project-wizard"
        style={{
          width: "100%", maxWidth: 580,
          background: "var(--panel)",
          border: "1px solid var(--border-strong)",
          borderRadius: 12,
          padding: 24,
        }}
      >
        {/* ── Header + step indicator ── */}
        <div style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          marginBottom: 18,
        }}>
          <div>
            <div style={{
              fontSize: 10, color: "var(--accent-2, #FF8A2A)",
              letterSpacing: "0.14em", textTransform: "uppercase",
              fontFamily: "'JetBrains Mono', monospace", marginBottom: 4,
            }}>
              Add Project · Step {step} of 2
            </div>
            <h3 style={{ margin: 0, fontSize: 19, color: "var(--text)" }}>
              {step === 1 && "Connect a GitHub repo"}
              {step === 2 && "Confirm and start"}
            </h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            data-testid="add-project-close"
            aria-label="Close"
            style={{
              background: "none", border: "none",
              color: "var(--text-faint)", cursor: "pointer", padding: 4,
            }}
          >
            <XIcon size={16} />
          </button>
        </div>

        {/* ── Stepper dots ── */}
        <div style={{
          display: "flex", alignItems: "center", gap: 6, marginBottom: 22,
        }}>
          {[1, 2].map((n) => (
            <div
              key={n}
              data-testid={`step-dot-${n}`}
              style={{
                height: 4, flex: 1, borderRadius: 2,
                background: n <= step
                  ? "var(--accent-2, #FF8A2A)"
                  : "rgba(255,255,255,0.08)",
                transition: "background 200ms",
              }}
            />
          ))}
        </div>

        {/* ──────────────── Step 1 — Connect via GitHub App, pick a repo ──────────────── */}
        {step === 1 && (
          <div data-testid="wizard-step-1">
            {connecting ? (
              <div
                data-testid="add-wizard-app-connecting"
                style={{
                  display: "flex", alignItems: "center", gap: 10,
                  padding: 14, marginBottom: 14,
                  background: "rgba(255,138,42,0.06)",
                  border: "1px solid rgba(255,138,42,0.28)",
                  borderRadius: 8,
                }}>
                <Loader2 size={16} className="spin" color="#FF8A2A" />
                <div style={{ fontSize: 13, lineHeight: 1.4 }}>
                  Waiting for you to finish in the GitHub popup…
                  <div style={{ fontSize: 11, color: "var(--text-faint)", marginTop: 3 }}>
                    This picks up automatically — no need to click anything else.
                  </div>
                  {/* 2026-09-05 — founder-reported: some browser/extension
                      combos let window.open() return a handle that never
                      shows a real popup (silently neutralized, not
                      rejected) — a real <a target="_blank"> click can
                      never be blocked like that, so it's always offered
                      as a guaranteed-working escape hatch. */}
                  {installUrl && (
                    <a
                      data-testid="add-wizard-app-fallback-link"
                      href={installUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      style={{
                        display: "inline-block", marginTop: 6,
                        fontSize: 11, color: "#FF8A2A", textDecoration: "underline",
                      }}>
                      Not seeing the popup? Click here to open it
                    </a>
                  )}
                </div>
              </div>
            ) : timedOut || denied ? (
              <div
                data-testid="add-wizard-app-timeout"
                style={{
                  display: "flex", flexDirection: "column", gap: 8,
                  padding: 14, marginBottom: 14,
                  background: "rgba(239,68,68,0.08)",
                  border: "1px solid rgba(239,68,68,0.32)",
                  borderRadius: 8,
                }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <div style={{ fontSize: 13, lineHeight: 1.4, flex: 1 }}>
                    Connection didn't finish — please try again.
                  </div>
                  <button
                    type="button"
                    data-testid="add-wizard-app-retry-btn"
                    onClick={openAppInstallPopup}
                    style={{
                      display: "inline-flex", alignItems: "center", gap: 6,
                      padding: "7px 12px",
                      background: "var(--accent-2, #FF8A2A)",
                      color: "#fff", border: "none",
                      borderRadius: 6, fontSize: 12, fontWeight: 600,
                      cursor: "pointer", whiteSpace: "nowrap",
                    }}>
                    <RefreshCw size={12} /> Try again
                  </button>
                </div>
                {/* Same guaranteed-working fallback — `denied` can fire
                    within one poll tick (~2.5s) if the popup handle was
                    a fake/neutralized one, before the user even notices
                    "connecting", so it must be reachable from here too. */}
                {installUrl && (
                  <div style={{ fontSize: 11.5, color: "var(--text-faint)" }}>
                    Or{" "}
                    <a
                      data-testid="add-wizard-app-fallback-link-2"
                      href={installUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      style={{ color: "#FF8A2A", textDecoration: "underline" }}>
                      open the GitHub connect page directly
                    </a>{" "}
                    — this always works even if popups are blocked.
                  </div>
                )}
                {connectAttempts >= 2 && (
                  <div style={{ fontSize: 11.5, color: "var(--text-faint)", lineHeight: 1.4 }}>
                    Still stuck?{" "}
                    <a
                      data-testid="add-wizard-app-reset-link"
                      href="https://github.com/settings/installations"
                      target="_blank"
                      rel="noreferrer"
                      style={{ color: "#FF8A2A", textDecoration: "underline" }}
                    >
                      Remove AUREM CTO on GitHub
                    </a>{" "}
                    then click Try again — this clears a stuck install
                    that sometimes can't self-heal.
                  </div>
                )}
              </div>
            ) : allGrantedRepos.length > 0 ? (
              <div data-testid="add-wizard-repo-picker" style={{ marginBottom: 14 }}>
                <p style={{ fontSize: 12, color: "var(--text-faint)", margin: "0 0 10px" }}>
                  Pick a repo to connect:
                </p>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 10 }}>
                  {allGrantedRepos.map((r) => (
                    <button
                      key={r.id || r.full_name}
                      type="button"
                      data-testid={`add-wizard-repo-${r.full_name}`}
                      onClick={() => pickRepo(r)}
                      style={{
                        padding: "6px 12px", fontSize: 12,
                        background: "rgba(255,255,255,0.05)",
                        border: "1px solid rgba(255,255,255,0.14)",
                        borderRadius: 6, color: "var(--text)",
                        cursor: "pointer", fontFamily: "'JetBrains Mono', monospace",
                      }}>
                      {r.full_name}
                      {r.private && (
                        <span style={{ marginLeft: 6, fontSize: 9, color: "var(--text-faint)" }}>private</span>
                      )}
                    </button>
                  ))}
                </div>
                <button
                  type="button"
                  data-testid="add-wizard-app-install-more-btn"
                  onClick={openAppInstallPopup}
                  style={{
                    padding: "4px 10px", fontSize: 10.5,
                    background: "transparent",
                    border: "1px dashed rgba(255,255,255,0.2)",
                    color: "var(--text-faint)", borderRadius: 4, cursor: "pointer",
                  }}>
                  + Grant access to another repo
                </button>
              </div>
            ) : (
              <div
                data-testid="add-wizard-app-cta"
                style={{
                  padding: 14, marginBottom: 14,
                  background: "linear-gradient(135deg, rgba(255,138,42,0.08), rgba(255,138,42,0.02))",
                  border: "1px solid rgba(255,138,42,0.32)",
                  borderRadius: 8,
                }}>
                <div style={{
                  display: "flex", alignItems: "center", gap: 8,
                  marginBottom: 8,
                }}>
                  <Github size={14} />
                  <strong style={{ fontSize: 13 }}>
                    Install Aurem for your repos
                  </strong>
                  <span style={{
                    fontSize: 10, padding: "2px 8px",
                    background: "rgba(255,138,42,0.24)",
                    color: "#ffb27a", borderRadius: 10,
                    letterSpacing: "0.04em",
                  }}>
                    RECOMMENDED
                  </span>
                </div>
                <p style={{
                  fontSize: 12, color: "var(--text-faint)",
                  margin: "0 0 10px", lineHeight: 1.5,
                }}>
                  One click — no token to manage or rotate. Pick the repo
                  in GitHub's own screen; this wizard picks it up automatically.
                </p>
                <button
                  type="button"
                  data-testid="add-wizard-app-install-btn"
                  data-guide-target="connect-github-btn"
                  onClick={openAppInstallPopup}
                  style={{
                    display: "inline-flex", alignItems: "center", gap: 8,
                    padding: "8px 14px",
                    background: "var(--accent-2, #FF8A2A)",
                    color: "#fff", border: "none",
                    borderRadius: 6, fontSize: 13, fontWeight: 600,
                    cursor: "pointer",
                  }}>
                  <Github size={13} />
                  Continue with GitHub App
                  <ArrowRight size={13} />
                </button>
                <div style={{
                  fontSize: 10.5, color: "var(--text-faint)",
                  marginTop: 8,
                }}>
                  Opens in a popup. This wizard picks up automatically.
                </div>
                <div data-testid="add-wizard-github-glitch-hint" style={{
                  fontSize: 10.5, color: "var(--text-faint)",
                  marginTop: 6, padding: "6px 8px",
                  background: "rgba(255,255,255,0.03)",
                  border: "1px solid var(--border)",
                  borderRadius: 4, lineHeight: 1.5,
                }}>
                  💡 If GitHub shows "No repositories found" while picking
                  repos, just type your repo's name in that search box (or
                  wait a second and reopen it) — it's a GitHub-side hiccup.
                </div>
              </div>
            )}

            {/* Fallback — public repo the App was never granted access to. */}
            {!manualEntry ? (
              <button
                type="button"
                data-testid="add-wizard-manual-toggle"
                onClick={() => setManualEntry(true)}
                style={{
                  background: "none", border: "none", padding: 0,
                  color: "var(--text-faint)", fontSize: 11.5,
                  textDecoration: "underline", cursor: "pointer",
                }}>
                Connecting a public repo instead? Paste the URL
              </button>
            ) : (
              <div style={{ marginTop: 4 }}>
                <input
                  data-testid="repo-input"
                  autoFocus
                  type="text"
                  spellCheck={false}
                  autoComplete="off"
                  value={repoInput}
                  onChange={(e) => setRepoInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && typedRepo) { setPickedRepo(null); setStep(2); }
                  }}
                  placeholder="e.g. facebook/react   •   or https://github.com/owner/repo"
                  style={{
                    width: "100%", padding: "12px 14px",
                    background: "rgba(0,0,0,0.32)",
                    color: "var(--text)",
                    fontFamily: "'JetBrains Mono', monospace", fontSize: 13,
                    border: `1.5px solid ${
                      repoInput && !typedRepo ? "rgba(239,68,68,0.5)" :
                      typedRepo              ? "rgba(34,197,94,0.5)"  :
                      "rgba(245,158,11,0.4)"
                    }`,
                    borderRadius: 8, outline: "none",
                  }}
                />
                {repoInput && !typedRepo && (
                  <div style={{ fontSize: 11, color: "#ef4444", marginTop: 8 }}>
                    Use the format <code style={codeChip}>owner/repo</code>.
                  </div>
                )}
              </div>
            )}

            <div style={navRowStyle}>
              <button
                type="button"
                onClick={onClose}
                data-testid="wizard-cancel-1"
                className="btn-ghost"
                style={{ padding: "10px 18px", fontSize: 12 }}
              >
                Cancel
              </button>
              {manualEntry && (
                <button
                  type="button"
                  onClick={() => { setPickedRepo(null); setStep(2); }}
                  disabled={!typedRepo}
                  data-testid="wizard-next-1"
                  style={cta(!!typedRepo)}
                >
                  Next <ArrowRight size={14} />
                </button>
              )}
            </div>
          </div>
        )}

        {/* ──────────────── Step 2 — Confirm + save ──────────────── */}
        {step === 2 && repo && (
          <div data-testid="wizard-step-2">
            <p style={{
              fontSize: 13, color: "var(--text-dim)",
              margin: "0 0 14px", lineHeight: 1.6,
            }}>
              {installationForRepo
                ? "Everything checks out. Confirm the project name and we'll take you to the chat."
                : "This repo has no GitHub App access yet — it'll be added as a public, read-only clone."}
            </p>

            {/* Summary card */}
            <div
              data-testid="confirm-summary"
              style={{
                padding: 14, marginBottom: 14,
                background: installationForRepo ? "rgba(34,197,94,0.06)" : "rgba(245,158,11,0.06)",
                border: `1px solid ${installationForRepo ? "rgba(34,197,94,0.32)" : "rgba(245,158,11,0.32)"}`,
                borderRadius: 10,
                display: "flex", flexDirection: "column", gap: 8,
                fontSize: 12, color: "var(--text)",
                fontFamily: "'JetBrains Mono', monospace",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <ShieldCheck size={14} style={{ color: installationForRepo ? "#22c55e" : "#f59e0b" }} />
                <span>
                  {installationForRepo ? "Access to" : "Connecting"}{" "}
                  <strong>{repo.full_name}</strong>
                  {installationForRepo ? " verified" : ""}
                </span>
              </div>
              <div style={{
                fontSize: 11, color: "var(--text-dim)", paddingLeft: 22,
              }}>
                {installationForRepo
                  ? `Connected via GitHub App · @${installationForRepo.github_login}`
                  : "Public repo — no GitHub App access granted."}
              </div>
            </div>

            <label style={{ display: "grid", gap: 6, marginBottom: 4 }}>
              <span style={{
                fontSize: 11, color: "var(--text-dim)",
                fontFamily: "'JetBrains Mono', monospace",
                letterSpacing: "0.06em", textTransform: "uppercase",
              }}>
                Project name
              </span>
              <input
                data-testid="project-name-input"
                type="text"
                value={projectName}
                onChange={(e) => setProjectName(e.target.value)}
                placeholder={repo.repo}
                className="input"
                style={{
                  fontFamily: "'JetBrains Mono', monospace", fontSize: 13,
                }}
              />
            </label>

            <div style={navRowStyle}>
              <button
                type="button"
                onClick={() => setStep(1)}
                data-testid="wizard-back-3"
                className="btn-ghost"
                disabled={saving}
                style={{ padding: "10px 18px", fontSize: 12 }}
              >
                <ArrowLeft size={12} /> Back
              </button>
              <button
                type="button"
                onClick={handleSave}
                disabled={saving || !projectName.trim() || !installationForRepo}
                data-testid="wizard-save"
                data-guide-target="continue-btn"
                style={cta(!saving && !!projectName.trim() && !!installationForRepo)}
              >
                {saving ? <Loader2 size={14} className="animate-spin" /> : null}
                {saving ? "Saving…" : "Save & Open Chat"}
                {!saving && <ArrowRight size={14} />}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Style atoms ───────────────────────────────────────────────────────

const codeChip = {
  background: "rgba(255,138,42,0.10)",
  color: "var(--accent-2, #FF8A2A)",
  padding: "1px 6px",
  borderRadius: 4,
  fontFamily: "'JetBrains Mono', monospace",
  fontSize: 11,
};

const navRowStyle = {
  display: "flex", justifyContent: "space-between", alignItems: "center",
  gap: 10, paddingTop: 18, marginTop: 18,
  borderTop: "0.5px solid var(--border, rgba(255,255,255,0.08))",
};

function cta(enabled) {
  return {
    display: "inline-flex", alignItems: "center", gap: 8,
    padding: "10px 22px",
    background: enabled ? "var(--accent-2, #FF8A2A)" : "rgba(255,255,255,0.06)",
    color:      enabled ? "#0a0e1a"               : "var(--text-faint)",
    border: "none", borderRadius: 8,
    fontSize: 13, fontWeight: 600,
    fontFamily: "'JetBrains Mono', monospace",
    cursor: enabled ? "pointer" : "not-allowed",
  };
}

