/**
 * SaveToGitHubModal.jsx — Part B (2026-10), "Save to GitHub" modal.
 *
 * Founder-reported 2026-10-21 incident: clicking "Approve the fix"
 * pushed straight to the project's base branch with no branch
 * choice, no PR, no warning. Phase 1/1b already fixed the BACKEND
 * (every ship now lands on a dedicated branch by default — see
 * services/loop_safety.py::resolve_ship_target). This modal is the
 * missing FRONTEND half: the user explicitly picks where the fix
 * goes, and nothing is written to GitHub until they confirm here.
 *
 * Rendered by MessageBubble.jsx in place of the old one-click submit
 * — `shipViaCTO()` now just opens this modal; the actual
 * `/cto/tasks/submit` POST only fires from `handleSave` below, via
 * the `onConfirm` prop MessageBubble passes in (its existing
 * `doSubmit(shipTarget)`).
 *
 * States: idle (pick repo/branch) -> submitting -> queued (honest
 * "queued" screen, not a fake "shipped") | error (human message,
 * Retry stays on the same picks).
 */
import React, { useEffect, useRef, useState } from "react";
import {
  X, Loader2, GitBranch, GitPullRequest, AlertTriangle, ExternalLink, CheckCircle2, RotateCcw,
} from "lucide-react";
import { api } from "../lib/api";
import { useModalA11y } from "../hooks/useModalA11y";
import { sanitizeApiErrorDetail } from "../lib/cleanErr";

function _shortSlug() {
  return Math.random().toString(36).slice(2, 8);
}

export default function SaveToGitHubModal({
  open,
  activeProject,
  onCancel,
  onConfirm,
}) {
  const modalRef = useRef(null);
  useModalA11y({ ref: modalRef, isOpen: open, onClose: onCancel });

  const [repos, setRepos] = useState([]);
  const [reposLoading, setReposLoading] = useState(false);
  const [reposError, setReposError] = useState(null);
  const [reposErrorStatus, setReposErrorStatus] = useState(null);
  const [reposRetryTick, setReposRetryTick] = useState(0);

  const [selectedOwner, setSelectedOwner] = useState(activeProject?.github_owner || "");
  const [selectedRepo, setSelectedRepo] = useState(activeProject?.github_repo || "");

  const [tab, setTab] = useState("create"); // "create" | "existing"
  const [newBranchName, setNewBranchName] = useState(() => `auremcto/ship-${_shortSlug()}`);
  // Cosmetic nit noticed in the same pass — computed once so the
  // (normally-invisible-since-the-input-starts-non-empty) placeholder
  // doesn't silently change value on every re-render.
  const [placeholderBranch] = useState(() => `auremcto/ship-${_shortSlug()}`);

  const [branches, setBranches] = useState([]);
  const [branchesLoading, setBranchesLoading] = useState(false);
  const [branchesError, setBranchesError] = useState(null);
  const [branchesErrorStatus, setBranchesErrorStatus] = useState(null);
  const [branchesRetryTick, setBranchesRetryTick] = useState(0);
  const [existingBranch, setExistingBranch] = useState("");
  const [branchSearch, setBranchSearch] = useState("");

  const [mainConfirmChecked, setMainConfirmChecked] = useState(false);
  const [phase, setPhase] = useState("idle"); // idle | submitting | error | queued
  const [submitError, setSubmitError] = useState(null);
  const [submitErrorStatus, setSubmitErrorStatus] = useState(null);
  const [queued, setQueued] = useState(null);

  // Reset to a clean, safe state every time the modal opens.
  useEffect(() => {
    if (!open) return;
    setSelectedOwner(activeProject?.github_owner || "");
    setSelectedRepo(activeProject?.github_repo || "");
    setTab("create");
    setNewBranchName(`auremcto/ship-${_shortSlug()}`);
    setExistingBranch("");
    setBranchSearch("");
    setMainConfirmChecked(false);
    setPhase("idle");
    setSubmitError(null);
    setSubmitErrorStatus(null);
    setQueued(null);
  }, [open, activeProject?.project_id]);

  // Fetch the writable-repo list once per open (or on Retry click).
  useEffect(() => {
    if (!open || !activeProject?.project_id) return;
    let cancelled = false;
    setReposLoading(true);
    setReposError(null);
    setReposErrorStatus(null);
    api.get(`/cto/projects/${activeProject.project_id}/github/repos`)
      .then((r) => {
        if (cancelled) return;
        const list = r?.data?.repos || [];
        setRepos(list);
        // 2026-10-27 — code-review fix. The project's own
        // `github_owner`/`github_repo` (the initial selection) isn't
        // guaranteed to be in THIS installation's writable-repo list
        // (e.g. the installation's repo access was changed on GitHub's
        // side). A <select> whose `value` matches no `<option>` shows
        // the browser's own fallback (the first option) while React
        // state silently keeps the stale, invisible value — Save would
        // then submit a repo the user never actually saw selected.
        // Snap to a real, listed repo as soon as the list is known.
        const stillValid = list.some(
          (x) => x.owner === selectedOwner && x.repo === selectedRepo,
        );
        if (!stillValid && list.length) {
          const fallback = list.find(
            (x) => x.owner === r?.data?.default_owner && x.repo === r?.data?.default_repo,
          ) || list[0];
          setSelectedOwner(fallback.owner);
          setSelectedRepo(fallback.repo);
        }
      })
      .catch((e) => {
        if (cancelled) return;
        setReposError(sanitizeApiErrorDetail(e, "Couldn't load your connected repos."));
        setReposErrorStatus(e?.response?.status || null);
      })
      .finally(() => { if (!cancelled) setReposLoading(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, activeProject?.project_id, reposRetryTick]);

  const selectedRepoObj = repos.find(
    (r) => r.owner === selectedOwner && r.repo === selectedRepo,
  );
  const isOwnProjectRepo = (
    selectedOwner === activeProject?.github_owner
    && selectedRepo === activeProject?.github_repo
  );
  const defaultBaseBranch = isOwnProjectRepo
    ? (activeProject?.branch || selectedRepoObj?.default_branch || "main")
    : (selectedRepoObj?.default_branch || "main");

  // Fetch branches lazily — only once the "Existing branch" tab is opened
  // (or on Retry click).
  useEffect(() => {
    if (!open || tab !== "existing" || !activeProject?.project_id
        || !selectedOwner || !selectedRepo) return;
    let cancelled = false;
    setBranchesLoading(true);
    setBranchesError(null);
    setBranchesErrorStatus(null);
    api.get(`/cto/projects/${activeProject.project_id}/github/branches`, {
      params: { owner: selectedOwner, repo: selectedRepo },
    })
      .then((r) => {
        if (cancelled) return;
        const list = r?.data?.branches || [];
        setBranches(list);
        if (!existingBranch && list.length) {
          // Never default-select the repo's default/main branch.
          const safe = list.find((b) => b.name !== defaultBaseBranch) || list[0];
          setExistingBranch(safe.name);
        }
      })
      .catch((e) => {
        if (cancelled) return;
        setBranchesError(sanitizeApiErrorDetail(e, "Couldn't load branches for this repo."));
        setBranchesErrorStatus(e?.response?.status || null);
      })
      .finally(() => { if (!cancelled) setBranchesLoading(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, tab, activeProject?.project_id, selectedOwner, selectedRepo, branchesRetryTick]);

  if (!open) return null;

  const chosenBranch = (tab === "existing" ? existingBranch : newBranchName).trim();
  const isMainBranchChosen = !!chosenBranch && chosenBranch === defaultBaseBranch;
  const canSave = (
    !!selectedOwner && !!selectedRepo && !!chosenBranch
    && (!isMainBranchChosen || mainConfirmChecked)
    && phase !== "submitting"
  );

  const filteredBranches = branches.filter(
    (b) => !branchSearch || b.name.toLowerCase().includes(branchSearch.toLowerCase()),
  );

  async function handleSave() {
    if (!canSave) return;
    setPhase("submitting");
    setSubmitError(null);
    setSubmitErrorStatus(null);
    // 2026-10-27 — if the chosen "new branch" name is actually the
    // same as the repo's base/default branch, there is no new branch
    // to create — commit directly to it (the existing main-branch
    // confirm checkbox above already gates this). Sending
    // create_branch:true here would ask the worker to "create" a
    // branch that already exists, a redundant call that can 422.
    const createBranch = tab === "create" && chosenBranch !== defaultBaseBranch;
    const shipTarget = {
      repo: `${selectedOwner}/${selectedRepo}`,
      branch: chosenBranch,
      create_branch: createBranch,
      base_branch: tab === "create" ? defaultBaseBranch : null,
    };
    try {
      const result = await onConfirm(shipTarget);
      if (result?.needs_clarification) {
        // Not a save failure — the backend declined for an unrelated
        // reason (task too vague). The chat already shows that
        // message; just close quietly instead of a fake "Saved".
        onCancel();
        return;
      }
      setQueued({
        owner: selectedOwner, repo: selectedRepo, branch: chosenBranch,
        createBranch,
      });
      setPhase("queued");
    } catch (e) {
      // Never surface a raw exception string (or raw Cloudflare/HTML
      // infra text) — sanitizeApiErrorDetail() reads the backend's
      // structured {code,message,retryable} detail (or sanitizes an
      // infra body) into one human sentence, same "never e.message"
      // contract as before.
      setSubmitError(sanitizeApiErrorDetail(e, "Couldn't save to GitHub — please try again."));
      setSubmitErrorStatus(e?.response?.status || null);
      setPhase("error");
    }
  }

  const branchUrl = queued && !queued.createBranch
    ? `https://github.com/${queued.owner}/${queued.repo}/tree/${encodeURIComponent(queued.branch)}`
    : null;

  return (
    <div
      data-testid="save-to-github-overlay"
      style={{
        position: "fixed", inset: 0, zIndex: 1000,
        background: "rgba(0,0,0,0.6)",
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: 16,
      }}
      onMouseDown={(e) => { if (e.target === e.currentTarget && phase !== "submitting") onCancel(); }}
    >
      <div
        ref={modalRef}
        data-testid="save-to-github-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Save to GitHub"
        style={{
          width: "min(480px, 100%)",
          background: "var(--panel, #141414)",
          border: "1px solid var(--border, #2A2A2A)",
          borderRadius: 10,
          padding: 20,
          color: "var(--text, #F5F5F5)",
          fontSize: 13,
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
          <strong style={{ fontSize: 15 }}>Save to GitHub</strong>
          <button
            type="button"
            data-testid="save-to-github-close"
            onClick={onCancel}
            aria-label="Close"
            disabled={phase === "submitting"}
            style={{ background: "none", border: "none", color: "var(--text-dim)", cursor: "pointer" }}
          >
            <X size={16} />
          </button>
        </div>

        {phase === "queued" ? (
          <div data-testid="save-to-github-success">
            <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--ok, #22C55E)", fontWeight: 600, marginBottom: 8 }}>
              <CheckCircle2 size={16} />
              Saved to {queued.owner}/{queued.repo}@{queued.branch}
            </div>
            <p style={{ color: "var(--text-dim)", lineHeight: 1.5, marginBottom: 12 }}>
              {queued.createBranch
                ? "ORA is writing the fix now and will push it to this new branch — progress and the commit/PR link will show up in the chat below."
                : "ORA is writing the fix now and will push it to this branch — progress and the commit link will show up in the chat below."}
            </p>
            {branchUrl && (
              <a
                href={branchUrl}
                target="_blank"
                rel="noreferrer"
                data-testid="save-to-github-view-link"
                style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "var(--accent-2)", marginBottom: 14 }}
              >
                <ExternalLink size={13} /> View branch on GitHub
              </a>
            )}
            <div style={{ display: "flex", justifyContent: "flex-end" }}>
              <button
                type="button"
                data-testid="save-to-github-okay"
                onClick={onCancel}
                style={{
                  padding: "8px 16px", borderRadius: 8, border: "none",
                  background: "#FF6608", color: "#0A0A0A", fontWeight: 700, cursor: "pointer",
                }}
              >
                Okay, got it
              </button>
            </div>
          </div>
        ) : (
          <>
            {/* Repository */}
            <div style={{ marginBottom: 14 }}>
              <label style={{ display: "block", color: "var(--text-dim)", marginBottom: 6 }}>Repository</label>
              {reposLoading ? (
                <div data-testid="save-to-github-repos-loading" style={{ color: "var(--text-faint)" }}>
                  <Loader2 size={13} style={{ animation: "spin 1s linear infinite", marginRight: 6 }} />
                  Loading your connected repos…
                </div>
              ) : reposError ? (
                <div data-testid="save-to-github-repos-error" style={{ color: "var(--danger)" }}>
                  <div>{reposError}{reposErrorStatus ? ` (HTTP ${reposErrorStatus})` : ""}</div>
                  <button
                    type="button"
                    data-testid="save-to-github-repos-retry"
                    onClick={() => setReposRetryTick((n) => n + 1)}
                    style={{
                      marginTop: 6, display: "inline-flex", alignItems: "center", gap: 4,
                      background: "none", border: "1px solid var(--border)", borderRadius: 6,
                      color: "var(--text-dim)", padding: "4px 8px", cursor: "pointer",
                    }}
                  >
                    <RotateCcw size={12} /> Retry
                  </button>
                </div>
              ) : repos.length === 0 ? (
                <div data-testid="save-to-github-repos-empty" style={{ color: "var(--text-faint)" }}>
                  No writable repos found for your GitHub connection.
                </div>
              ) : (
                <select
                  data-testid="save-to-github-repo-select"
                  value={`${selectedOwner}/${selectedRepo}`}
                  onChange={(e) => {
                    const [o, r] = e.target.value.split("/");
                    setSelectedOwner(o);
                    setSelectedRepo(r);
                    setExistingBranch("");
                    setBranches([]);
                  }}
                  style={{
                    width: "100%", padding: "8px 10px", borderRadius: 6,
                    background: "var(--panel-2)", color: "var(--text)",
                    border: "1px solid var(--border)",
                  }}
                >
                  {repos.map((r) => (
                    <option key={r.full_name} value={`${r.owner}/${r.repo}`}>
                      {r.full_name}{r.private ? " (private)" : ""}
                    </option>
                  ))}
                </select>
              )}
            </div>

            {/* Branch tabs */}
            <div style={{ marginBottom: 10 }}>
              <label style={{ display: "block", color: "var(--text-dim)", marginBottom: 6 }}>Branch</label>
              <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
                <button
                  type="button"
                  data-testid="save-to-github-tab-create"
                  onClick={() => setTab("create")}
                  style={{
                    flex: 1, padding: "6px 10px", borderRadius: 6, cursor: "pointer",
                    border: `1px solid ${tab === "create" ? "#FF6608" : "var(--border)"}`,
                    background: tab === "create" ? "rgba(255,102,8,0.12)" : "transparent",
                    color: tab === "create" ? "#FF6608" : "var(--text-dim)",
                  }}
                >
                  Create new
                </button>
                <button
                  type="button"
                  data-testid="save-to-github-tab-existing"
                  onClick={() => setTab("existing")}
                  style={{
                    flex: 1, padding: "6px 10px", borderRadius: 6, cursor: "pointer",
                    border: `1px solid ${tab === "existing" ? "#FF6608" : "var(--border)"}`,
                    background: tab === "existing" ? "rgba(255,102,8,0.12)" : "transparent",
                    color: tab === "existing" ? "#FF6608" : "var(--text-dim)",
                  }}
                >
                  Existing branch
                </button>
              </div>

              {tab === "create" ? (
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <GitBranch size={14} style={{ color: "var(--text-faint)", flexShrink: 0 }} />
                  <input
                    data-testid="save-to-github-new-branch-input"
                    value={newBranchName}
                    onChange={(e) => setNewBranchName(e.target.value)}
                    placeholder={placeholderBranch}
                    style={{
                      flex: 1, padding: "8px 10px", borderRadius: 6,
                      background: "var(--panel-2)", color: "var(--text)",
                      border: "1px solid var(--border)",
                    }}
                  />
                </div>
              ) : (
                <div>
                  <input
                    data-testid="save-to-github-branch-search"
                    value={branchSearch}
                    onChange={(e) => setBranchSearch(e.target.value)}
                    placeholder="Search branches…"
                    style={{
                      width: "100%", padding: "6px 10px", borderRadius: 6, marginBottom: 6,
                      background: "var(--panel-2)", color: "var(--text)",
                      border: "1px solid var(--border)",
                    }}
                  />
                  {branchesLoading ? (
                    <div data-testid="save-to-github-branches-loading" style={{ color: "var(--text-faint)" }}>
                      <Loader2 size={13} style={{ animation: "spin 1s linear infinite", marginRight: 6 }} />
                      Loading branches…
                    </div>
                  ) : branchesError ? (
                    <div data-testid="save-to-github-branches-error" style={{ color: "var(--danger)" }}>
                      <div>{branchesError}{branchesErrorStatus ? ` (HTTP ${branchesErrorStatus})` : ""}</div>
                      <button
                        type="button"
                        data-testid="save-to-github-branches-retry"
                        onClick={() => setBranchesRetryTick((n) => n + 1)}
                        style={{
                          marginTop: 6, display: "inline-flex", alignItems: "center", gap: 4,
                          background: "none", border: "1px solid var(--border)", borderRadius: 6,
                          color: "var(--text-dim)", padding: "4px 8px", cursor: "pointer",
                        }}
                      >
                        <RotateCcw size={12} /> Retry
                      </button>
                    </div>
                  ) : filteredBranches.length === 0 ? (
                    <div data-testid="save-to-github-branches-empty" style={{ color: "var(--text-faint)" }}>
                      No branches match.
                    </div>
                  ) : (
                    <select
                      data-testid="save-to-github-existing-branch-select"
                      value={existingBranch}
                      onChange={(e) => setExistingBranch(e.target.value)}
                      size={Math.min(6, filteredBranches.length)}
                      style={{
                        width: "100%", padding: "4px", borderRadius: 6,
                        background: "var(--panel-2)", color: "var(--text)",
                        border: "1px solid var(--border)",
                      }}
                    >
                      {filteredBranches.map((b) => (
                        <option key={b.name} value={b.name}>
                          {b.name}{b.name === defaultBaseBranch ? " (default)" : ""}{b.protected ? " 🔒" : ""}
                        </option>
                      ))}
                    </select>
                  )}
                </div>
              )}
            </div>

            {isMainBranchChosen && (
              <div
                data-testid="save-to-github-main-warning"
                style={{
                  display: "flex", gap: 8, padding: "8px 10px", borderRadius: 6,
                  background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.3)",
                  color: "var(--danger)", marginBottom: 10, alignItems: "flex-start",
                }}
              >
                <AlertTriangle size={14} style={{ flexShrink: 0, marginTop: 1 }} />
                <div>
                  <div style={{ fontWeight: 600, marginBottom: 4 }}>
                    This commits straight to {chosenBranch} — your live branch.
                  </div>
                  <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
                    <input
                      type="checkbox"
                      data-testid="save-to-github-main-confirm-checkbox"
                      checked={mainConfirmChecked}
                      onChange={(e) => setMainConfirmChecked(e.target.checked)}
                    />
                    I understand this ships directly to {chosenBranch}, no review branch.
                  </label>
                </div>
              </div>
            )}

            {phase === "error" && (
              <div
                data-testid="save-to-github-error"
                style={{
                  padding: "8px 10px", borderRadius: 6, marginBottom: 10,
                  background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.3)",
                  color: "var(--danger)",
                }}
              >
                <div>{submitError}</div>
                {submitErrorStatus && (
                  <div style={{ fontSize: 11, color: "var(--text-faint)", marginTop: 2 }}>
                    HTTP {submitErrorStatus}
                  </div>
                )}
              </div>
            )}

            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 6 }}>
              <button
                type="button"
                data-testid="save-to-github-cancel"
                onClick={onCancel}
                disabled={phase === "submitting"}
                style={{
                  padding: "8px 14px", borderRadius: 8, border: "1px solid var(--border)",
                  background: "transparent", color: "var(--text-dim)",
                  cursor: phase === "submitting" ? "default" : "pointer",
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                data-testid="save-to-github-save"
                onClick={handleSave}
                disabled={!canSave}
                style={{
                  padding: "8px 16px", borderRadius: 8, border: "none",
                  display: "inline-flex", alignItems: "center", gap: 6,
                  background: canSave ? "#FF6608" : "#5A5A5A",
                  color: "#0A0A0A", fontWeight: 700,
                  cursor: canSave ? "pointer" : "default",
                }}
              >
                {phase === "submitting"
                  ? (<><Loader2 size={13} style={{ animation: "spin 1s linear infinite" }} /> Saving…</>)
                  : (<><GitPullRequest size={13} /> Save to GitHub</>)}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
