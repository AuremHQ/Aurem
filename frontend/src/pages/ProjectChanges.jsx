/**
 * pages/ProjectChanges.jsx — Part 2c B4, Project Versions.
 *
 * The private "Changes" page: every merged AUREM change for this
 * project, numbered v1, v2, v3... (never renumbered, gaps allowed).
 * Reads GET /projects/:projectId/versions — flag-gated server-side
 * (404 when `project_versions` is off; this component never assumes
 * it's reachable and handles that 404 as an honest "not available"
 * state, not a crash).
 *
 * Stored-XSS note: `summary` is repo/PR-title-derived free text (a
 * malicious repo could name a PR `<script>...`). Rendered as plain
 * JSX text below — React escapes text content by default, and this
 * file never uses dangerouslySetInnerHTML — safe by construction.
 *
 * PR links are rendered ONLY when the URL's host is exactly
 * `github.com` over `https` — never trust a stored pr_url blindly
 * (a historical row could in principle carry anything).
 */
import React, { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../lib/api";

function isSafeGithubPrUrl(url) {
  if (!url || typeof url !== "string") return false;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" && parsed.hostname === "github.com";
  } catch {
    return false;
  }
}

function shortSha(sha) {
  return (sha || "").slice(0, 7);
}

function formatDate(epochSeconds) {
  if (!epochSeconds) return "";
  try {
    return new Date(epochSeconds * 1000).toLocaleDateString(undefined, {
      year: "numeric", month: "short", day: "numeric",
    });
  } catch {
    return "";
  }
}

export default function ProjectChanges() {
  const { projectId } = useParams();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const r = await api.get(`/projects/${projectId}/versions`);
        if (!active) return;
        setData(r.data);
        setError(null);
      } catch (e) {
        if (!active) return;
        setError(e?.response?.status === 404 ? "not_available" : "error");
      }
    }
    load();
    return () => { active = false; };
  }, [projectId]);

  if (error === "not_available") {
    return (
      <div data-testid="changes-not-available">
        Changes history isn&apos;t available for this project yet.
      </div>
    );
  }
  if (error === "error") {
    return (
      <div data-testid="changes-error">
        Couldn&apos;t load your changes history — try again shortly.
      </div>
    );
  }
  if (!data) {
    return <div data-testid="changes-loading">Loading…</div>;
  }

  const versions = data.versions || [];

  return (
    <div data-testid="project-changes-page">
      <h1>Changes</h1>
      {data.locked_count > 0 && (
        <p data-testid="changes-locked-line">
          Showing your latest {versions.length} of {data.total}.
        </p>
      )}
      {versions.length === 0 ? (
        <p data-testid="changes-empty-state">No changes landed yet.</p>
      ) : (
        <ul data-testid="changes-list">
          {versions.map((v) => (
            <li key={v.version_id} data-testid={`changes-row-${v.version_id}`}>
              <span data-testid={`changes-badge-${v.version_id}`}>v{v.n}</span>
              <span data-testid={`changes-date-${v.version_id}`}>{formatDate(v.merged_at)}</span>
              {/* Repo/PR-title-derived free text — plain React text
                  child, never dangerouslySetInnerHTML. */}
              <span data-testid={`changes-summary-${v.version_id}`}>{v.summary}</span>
              {v.merged_sha && (
                <code data-testid={`changes-sha-${v.version_id}`}>{shortSha(v.merged_sha)}</code>
              )}
              {isSafeGithubPrUrl(v.pr_url) && (
                <a
                  data-testid={`changes-pr-link-${v.version_id}`}
                  href={v.pr_url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  View PR
                </a>
              )}
              {v.status === "rolled_back" && (
                <span data-testid={`changes-rolled-back-${v.version_id}`}>Rolled back</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
