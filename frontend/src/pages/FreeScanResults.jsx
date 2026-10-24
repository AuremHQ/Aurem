/**
 * pages/FreeScanResults.jsx — Master Prompt v2 PART 4.1.
 *
 * The free-scan RESULTS surface. Does not trigger anything — the
 * scan already runs automatically on repo connect (see backend
 * services/project_onboarding_scan.py); this page polls
 * GET /cto/projects/:projectId/free-scan while status is "scanning"
 * and renders the real score + ALL issues (name, severity, counts)
 * once ready, plus a share card ("My score: 41" — score ONLY, no
 * issue names/files/repo strings, so nothing repo-derived ever
 * reaches a URL or OG tag).
 *
 * Stored-XSS note: `issue.name`/`issue.file` are repo-derived
 * strings (a malicious repo could name a file `<script>...`). They
 * are rendered as plain JSX text children below — React escapes
 * text content by default, and this file never uses
 * dangerouslySetInnerHTML — so this is safe by construction, not by
 * a sanitization step that could be forgotten later.
 */
import React, { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../lib/api";

const POLL_MS = 3000;

export default function FreeScanResults() {
  const { projectId } = useParams();
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const timerRef = useRef(null);

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const r = await api.get(`/cto/projects/${projectId}/free-scan`);
        if (!active) return;
        setResult(r.data);
        setError(null);
        if (r.data.status === "scanning") {
          timerRef.current = setTimeout(load, POLL_MS);
        }
      } catch (e) {
        if (!active) return;
        setError(e?.response?.status === 404 ? "not_found" : "error");
      }
    }
    load();
    return () => {
      active = false;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [projectId]);

  if (error === "not_found") {
    return <div data-testid="free-scan-not-found">Free scan not found for this project.</div>;
  }
  if (error === "error") {
    return <div data-testid="free-scan-error">Couldn&apos;t load your scan results — try again shortly.</div>;
  }
  if (!result) {
    return <div data-testid="free-scan-loading">Loading…</div>;
  }
  if (result.status === "scanning") {
    return (
      <div data-testid="free-scan-banner-scanning">
        <h2>Free scan started</h2>
        <p>Scanning your repository — this usually takes under a minute.</p>
      </div>
    );
  }
  if (result.status === "error") {
    return <div data-testid="free-scan-banner-error">We couldn&apos;t finish your free scan. Please try reconnecting the repo.</div>;
  }

  const issues = result.issues || [];

  return (
    <div data-testid="free-scan-results">
      <h2 data-testid="free-scan-score">Your score: {result.score}</h2>

      <div data-testid="free-scan-share-card">
        My score: {result.share_card?.score}
      </div>

      {result.categories_not_run && result.categories_not_run.length > 0 && (
        <p data-testid="free-scan-categories-not-run">
          Some categories were skipped to stay within the scan budget: {result.categories_not_run.join(", ")}
        </p>
      )}

      <ul data-testid="free-scan-issue-list">
        {issues.map((issue, i) => (
          <li key={i} data-testid="free-scan-issue-row">
            <span data-testid="free-scan-issue-severity">{issue.severity}</span>{" "}
            <span data-testid="free-scan-issue-name">{issue.name}</span>{" "}
            {issue.file && <span data-testid="free-scan-issue-file">{issue.file}</span>}
          </li>
        ))}
      </ul>

      {issues.length === 0 && <p data-testid="free-scan-no-issues">No issues found.</p>}
    </div>
  );
}
