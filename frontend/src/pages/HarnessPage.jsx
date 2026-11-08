/**
 * pages/HarnessPage.jsx — Aurem Loop harness, Phase B, §4.5 structure.
 *
 * Flag-gated via isHarnessPageEnabled() (same pageFlags.js pattern as
 * ship_via_pr / project_versions) — default OFF. D1 (hosting the real
 * fix-branch package) is a SEPARATE, not-yet-approved gate on the
 * backend: even with this flag on, POST /harness/download returns a
 * clean 409 ("no release available yet") until an admin has actually
 * published one — this page never claims a download exists that
 * isn't there.
 *
 * Honesty constraints, enforced in copy here (do not loosen):
 *   - Never claims "Local mode / point at Ollama" works — that
 *     feature is flagged not_built on the live status endpoint.
 *   - Never claims Loop C has produced real weight changes, or that
 *     it runs on anything but the harness-local model.
 *   - Never claims an exact benchmark pass-rate lift — those figures
 *     are not independently verified here; shown only once Teji
 *     confirms them (left out rather than guessed).
 *   - Free vs subscriber: ONE package for everyone — extras are
 *     gated at harness runtime by the verify endpoint, never by
 *     shipping different zip contents, so the copy never implies a
 *     "paid download" vs "free download".
 *
 * Note on gating scope: pageFlags.js is only populated after a
 * successful /auth/me call, so this mechanism gates logged-in
 * sessions only — a signed-out visitor sees the sign-up teaser below
 * regardless of the flag's value. A true pre-login public gate (e.g.
 * an unauthenticated /config/flags read) does not exist in this
 * codebase yet; flagged as a follow-up for whoever does the real
 * public launch wiring, not solved here.
 */
import React, { useState } from "react";
import { Link } from "react-router-dom";
import { api, getToken } from "../lib/api";
import { isHarnessPageEnabled } from "../lib/pageFlags";

function downloadBlobAsFile(blob, filename) {
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  window.URL.revokeObjectURL(url);
}

function filenameFromContentDisposition(headerValue, fallback) {
  if (!headerValue) return fallback;
  const match = /filename="?([^"]+)"?/.exec(headerValue);
  return match ? match[1] : fallback;
}

export default function HarnessPage() {
  const [downloadState, setDownloadState] = useState("idle"); // idle | downloading | error | no_release
  const loggedIn = !!getToken();

  async function handleDownload() {
    setDownloadState("downloading");
    try {
      const resp = await api.post("/harness/download", null, {
        responseType: "blob",
      });
      const filename = filenameFromContentDisposition(
        resp.headers?.["content-disposition"], "aurem-loop.zip",
      );
      downloadBlobAsFile(resp.data, filename);
      setDownloadState("idle");
    } catch (e) {
      if (e?.response?.status === 409) {
        setDownloadState("no_release");
      } else {
        setDownloadState("error");
      }
    }
  }

  if (!loggedIn) {
    return (
      <div data-testid="harness-page-signed-out">
        <h1>Aurem Loop — local coding harness</h1>
        <p>A local coding harness that checks its own work.</p>
        <Link to="/signup" data-testid="harness-signup-cta">
          Create a free account to download
        </Link>
      </div>
    );
  }

  if (!isHarnessPageEnabled()) {
    return (
      <div data-testid="harness-page-not-available">
        Aurem Loop isn&apos;t available yet.
      </div>
    );
  }

  return (
    <div data-testid="harness-page">
      <section data-testid="harness-hero">
        <h1>Aurem Loop — local coding harness</h1>
        <p>A local coding harness that checks its own work.</p>
        <button
          type="button"
          data-testid="harness-download-button"
          onClick={handleDownload}
          disabled={downloadState === "downloading"}
        >
          {downloadState === "downloading" ? "Preparing your download…" : "Download Aurem Loop"}
        </button>
        {downloadState === "no_release" && (
          <p data-testid="harness-no-release">
            No build is published yet — check back soon.
          </p>
        )}
        {downloadState === "error" && (
          <p data-testid="harness-download-error">
            Couldn&apos;t start the download — try again shortly.
          </p>
        )}
      </section>

      <section data-testid="harness-how-it-works">
        <h2>How it works</h2>
        <ol>
          <li>You describe the task.</li>
          <li>The harness plans it, and a small local model does one narrow step at a time.</li>
          <li>Every step is verified by code before it&apos;s kept — nothing is trusted on the model&apos;s word alone.</li>
        </ol>
      </section>

      <section data-testid="harness-what-it-does-not-do">
        <h2>What it does — and doesn&apos;t</h2>
        <ul>
          <li>Runs entirely on your machine against a small local model.</li>
          <li>Never deletes files, never pushes to a remote repo on its own, never touches secrets or credentials.</li>
          <li>Loop C (its gated self-improvement step) runs only against the local model — it has not produced any real weight changes so far, and it never touches cloud models.</li>
        </ul>
      </section>

      <section data-testid="harness-tiers">
        <h2>Free vs. subscriber</h2>
        <p>There is one download for everyone — subscriber extras unlock automatically on your account, nothing to reinstall.</p>
        <table>
          <tbody>
            <tr>
              <td>Local harness, full functionality</td>
              <td data-testid="harness-tier-free-basic">Included</td>
              <td data-testid="harness-tier-sub-basic">Included</td>
            </tr>
            <tr>
              <td>Cloud model option</td>
              <td data-testid="harness-tier-free-cloud">Locked</td>
              <td data-testid="harness-tier-sub-cloud">Included</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section data-testid="harness-install-steps">
        <h2>Install (Windows)</h2>
        <ol>
          <li>Download the zip above.</li>
          <li>Run <code>install.bat</code>.</li>
          <li>Open the harness and describe your first task.</li>
        </ol>
      </section>
    </div>
  );
}
