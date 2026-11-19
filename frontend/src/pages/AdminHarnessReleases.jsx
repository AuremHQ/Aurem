/**
 * AdminHarnessReleases.jsx — 2026-11-16.
 * /admin/harness-releases — Aurem Loop release publishing for admins.
 * Standalone page (same pattern as AdminMaintenance) so it doesn't
 * fight the Admin.jsx shell's internal switch statement.
 *
 * Why this page exists: there was NO admin UI for harness releases —
 * publishing v0.8.0 needed a DevTools console script, which the Muse
 * browser cannot run (no JS evaluation in its tools) and the founder
 * cannot run from his phone. This page lets an admin publish with
 * clicks only: pick the zip, create the draft, check the SHA-256
 * match, publish, verify the live status.
 *
 * Backend: routers/admin_harness.py — POST /admin/harness/releases
 * (HarnessReleaseCreate), POST /admin/harness/releases/{id}/publish,
 * GET /admin/harness/releases (list). Public live status is
 * GET /api/harness/release-status (no auth).
 */
import React, { useEffect, useState, useCallback } from "react";
import { Link } from "react-router-dom";
import {
  ArrowLeft,
  Package,
  Upload,
  Rocket,
  Loader2,
  RefreshCw,
  CheckCircle2,
  XCircle,
} from "lucide-react";
import { api, API_BASE, getToken } from "../lib/api";
import { cleanErr } from "../lib/cleanErr";
import { toast } from "../components/Toast";
// Same origin as the API, minus the /aurem-dev suffix, for the public
// release-status endpoint (it lives at /api/harness/release-status).
const PUBLIC_BASE = API_BASE.endsWith("/aurem-dev")
  ? API_BASE.slice(0, -"/aurem-dev".length)
  : API_BASE;
const DEFAULT_CHANGELOG = [
  "Aurem Loop v0.8.0 — first public download.",
  "Local harness that runs on your own PC, free and offline; one small task at a time, with a gate that checks each change.",
  "Requires Python 3.11+ and Ollama with qwen2.5-coder:1.5b; Windows 10/11 or Ubuntu.",
];
function Card({ children, style }) {
  return (
    <div
      style={{
        background: "#0f0f0f",
        border: "1px solid #262626",
        borderRadius: 12,
        padding: 20,
        ...style,
      }}
    >
      {children}
    </div>
  );
}
const _MONTH_NAMES = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

// 2026-11-16 — the CREATED column showed "20752d ago": created_at/
// published_at are SECONDS since epoch (Python time.time()), but
// `new Date(iso)` treats a raw number as MILLISECONDS, landing the
// computed date ~57 years off. Fixed the unit (* 1000) and switched
// to an absolute date — a publish date doesn't need re-computing on
// every render, and "Xd ago" hid exactly this kind of unit bug.
function ago(seconds) {
  if (!seconds) return "—";
  const d = new Date(seconds * 1000);
  if (isNaN(d.getTime())) return "—";
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${d.getDate()} ${_MONTH_NAMES[d.getMonth()]} ${d.getFullYear()}, ${hh}:${mm}`;
}
async function sha256Hex(arrayBuffer) {
  if (!window.crypto?.subtle) return null;
  const digest = await window.crypto.subtle.digest("SHA-256", arrayBuffer);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}
function readFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const dataUrl = String(reader.result || "");
      const comma = dataUrl.indexOf(",");
      resolve(comma >= 0 ? dataUrl.slice(comma + 1) : dataUrl);
    };
    reader.onerror = () => reject(new Error("Could not read the zip file"));
    reader.readAsDataURL(file);
  });
}
export default function AdminHarnessReleases() {
  const [releases, setReleases] = useState([]);
  const [live, setLive] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [version, setVersion] = useState("0.8.0");
  const [platform, setPlatform] = useState("windows");
  const [changelog, setChangelog] = useState(DEFAULT_CHANGELOG.join("\n"));
  const [mandatory, setMandatory] = useState(false);
  const [fileInfo, setFileInfo] = useState(null);
  const [draft, setDraft] = useState(null);
  const [published, setPublished] = useState(null);
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.get("/admin/harness/releases", {
        headers: { Authorization: `Bearer ${getToken()}` },
      });
      const arr = Array.isArray(r.data) ? r.data : r.data?.releases || [];
      setReleases(arr);
    } catch (e) {
      toast({ message: cleanErr(e, "Failed to load releases"), kind: "error" });
    }
    try {
      const res = await fetch(`${PUBLIC_BASE}/harness/release-status`);
      setLive(await res.json());
    } catch {
      setLive(null);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  async function onFilePicked(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    setDraft(null);
    setPublished(null);
    try {
      const buf = await file.arrayBuffer();
      const [b64, localSha] = await Promise.all([
        readFileAsBase64(file),
        sha256Hex(buf),
      ]);
      setFileInfo({ name: file.name, size: file.size, b64, localSha });
    } catch (err) {
      toast({
        message: err?.message || "Could not read the zip file",
        kind: "error",
      });
      setFileInfo(null);
    }
  }
  async function createDraft() {
    if (!fileInfo) {
      toast({ message: "Pick the release zip first", kind: "error" });
      return;
    }
    const items = changelog
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
    if (!version.trim()) {
      toast({ message: "Version is required", kind: "error" });
      return;
    }
    setBusy(true);
    try {
      const r = await api.post(
        "/admin/harness/releases",
        {
          version: version.trim(),
          platform: platform.trim() || "windows",
          changelog: items,
          mandatory,
          artifact_base64: fileInfo.b64,
          source: "admin_ui",
        },
        { headers: { Authorization: `Bearer ${getToken()}` } },
      );
      setDraft(r.data);
      toast({ message: `Draft ${r.data.version} created`, kind: "success" });
    } catch (e) {
      toast({ message: cleanErr(e, "Draft creation failed"), kind: "error" });
    } finally {
      setBusy(false);
    }
  }
  async function publishRelease(releaseId) {
    setBusy(true);
    try {
      const r = await api.post(
        `/admin/harness/releases/${releaseId}/publish`,
        {},
        { headers: { Authorization: `Bearer ${getToken()}` } },
      );
      setPublished(r.data);
      toast({
        message: "Release published — live on the site",
        kind: "success",
      });
      await load();
    } catch (e) {
      toast({ message: cleanErr(e, "Publish failed"), kind: "error" });
    } finally {
      setBusy(false);
    }
  }
  const shaMatch =
    draft && fileInfo?.localSha
      ? fileInfo.localSha === draft.artifact_sha256
      : null;
  const canPublish = draft && shaMatch !== false;
  if (loading && releases.length === 0 && !live) {
    return (
      <div
        style={{
          minHeight: "100vh",
          display: "grid",
          placeItems: "center",
          background: "#0a0a0a",
          color: "#666",
        }}
      >
        <Loader2 size={18} className="spin" />
      </div>
    );
  }
  return (
    <div
      style={{
        minHeight: "100vh",
        background: "#0a0a0a",
        color: "#e5e5e5",
        fontFamily: "system-ui, -apple-system, sans-serif",
        padding: "28px 24px 60px",
      }}
    >
      <div style={{ maxWidth: 900, margin: "0 auto" }}>
        <Link
          to="/admin/cockpit"
          data-testid="harness-releases-back-link"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            color: "#888",
            fontSize: 12,
            textDecoration: "none",
            marginBottom: 18,
          }}
        >
          <ArrowLeft size={12} /> Cockpit
        </Link>
        <h1
          style={{
            fontSize: 20,
            fontWeight: 700,
            display: "flex",
            alignItems: "center",
            gap: 10,
            margin: "0 0 4px",
          }}
        >
          <Package size={18} color="#ff8a2a" /> Aurem Loop Releases
        </h1>
        <p style={{ fontSize: 12.5, color: "#888", margin: "0 0 22px" }}>
          Publish a harness release (zip) so users can download it from the
          homepage and the /harness page. The draft&apos;s SHA-256 must match
          the zip before Publish unlocks.
        </p>
        {/* Live status */}
        <Card style={{ marginBottom: 18 }}>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <div style={{ fontSize: 13, fontWeight: 600 }}>
              Live on the site right now
            </div>
            <button
              data-testid="harness-releases-refresh-btn"
              onClick={load}
              disabled={loading}
              style={{
                padding: "6px 12px",
                fontSize: 12,
                borderRadius: 6,
                border: "1px solid #333",
                background: "#181818",
                color: "#ccc",
                cursor: loading ? "wait" : "pointer",
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
              }}
            >
              <RefreshCw size={12} /> Refresh
            </button>
          </div>
          <div
            data-testid="harness-releases-live-status"
            style={{
              fontSize: 12.5,
              color: "#999",
              marginTop: 10,
              fontFamily: "monospace",
              wordBreak: "break-all",
            }}
          >
            {live
              ? live.available
                ? `Available: v${live.version} · sha256 ${live.sha256 || "—"}`
                : "No release published yet (available: false)"
              : "Could not load live status."}
          </div>
        </Card>
        {/* Publish a release */}
        <Card style={{ marginBottom: 18 }}>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>
            Publish a release
          </div>
          <div style={{ fontSize: 11.5, color: "#777", marginBottom: 14 }}>
            Pick the release zip, create the draft, check the SHA-256 match,
            then publish. Publishing makes this version the one latest download
            for its platform.
          </div>
          <div
            style={{
              display: "flex",
              gap: 12,
              flexWrap: "wrap",
              marginBottom: 12,
            }}
          >
            <label
              style={{
                flex: "1 1 160px",
                fontSize: 11,
                color: "#888",
                textTransform: "uppercase",
                letterSpacing: "0.06em",
              }}
            >
              Version
              <input
                data-testid="harness-release-version-input"
                value={version}
                onChange={(e) => setVersion(e.target.value)}
                placeholder="0.8.0"
                style={{
                  display: "block",
                  width: "100%",
                  background: "#181818",
                  border: "1px solid #2a2a2a",
                  borderRadius: 8,
                  color: "#e5e5e5",
                  fontSize: 12.5,
                  padding: "8px 10px",
                  marginTop: 4,
                }}
              />
            </label>
            <label
              style={{
                flex: "1 1 160px",
                fontSize: 11,
                color: "#888",
                textTransform: "uppercase",
                letterSpacing: "0.06em",
              }}
            >
              Platform
              <input
                data-testid="harness-release-platform-input"
                value={platform}
                onChange={(e) => setPlatform(e.target.value)}
                placeholder="windows"
                style={{
                  display: "block",
                  width: "100%",
                  background: "#181818",
                  border: "1px solid #2a2a2a",
                  borderRadius: 8,
                  color: "#e5e5e5",
                  fontSize: 12.5,
                  padding: "8px 10px",
                  marginTop: 4,
                }}
              />
            </label>
            <label
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                fontSize: 12.5,
                color: "#aaa",
                paddingTop: 18,
                cursor: "pointer",
              }}
            >
              <input
                data-testid="harness-release-mandatory-input"
                type="checkbox"
                checked={mandatory}
                onChange={(e) => setMandatory(e.target.checked)}
              />
              Mandatory update
            </label>
          </div>
          <label
            style={{
              fontSize: 11,
              color: "#888",
              display: "block",
              marginBottom: 4,
              textTransform: "uppercase",
              letterSpacing: "0.06em",
            }}
          >
            Changelog (one entry per line)
          </label>
          <textarea
            data-testid="harness-release-changelog-input"
            value={changelog}
            onChange={(e) => setChangelog(e.target.value)}
            rows={3}
            style={{
              width: "100%",
              background: "#181818",
              border: "1px solid #2a2a2a",
              borderRadius: 8,
              color: "#e5e5e5",
              fontSize: 12.5,
              padding: "8px 10px",
              marginBottom: 12,
              resize: "vertical",
            }}
          />
          <label
            style={{
              fontSize: 11,
              color: "#888",
              display: "block",
              marginBottom: 4,
              textTransform: "uppercase",
              letterSpacing: "0.06em",
            }}
          >
            Release zip
          </label>
          <input
            data-testid="harness-release-file-input"
            type="file"
            accept=".zip"
            onChange={onFilePicked}
            style={{
              display: "block",
              fontSize: 12.5,
              color: "#ccc",
              marginBottom: 6,
            }}
          />
          {fileInfo && (
            <div
              data-testid="harness-release-file-info"
              style={{
                fontSize: 12,
                color: "#999",
                marginBottom: 12,
                fontFamily: "monospace",
                wordBreak: "break-all",
              }}
            >
              {fileInfo.name} · {fileInfo.size.toLocaleString()} bytes
              {fileInfo.localSha ? ` · sha256 ${fileInfo.localSha}` : ""}
            </div>
          )}
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            <button
              data-testid="harness-release-create-draft-btn"
              onClick={createDraft}
              disabled={busy || !fileInfo}
              style={{
                padding: "8px 18px",
                fontSize: 12,
                fontWeight: 700,
                borderRadius: 6,
                border: "1px solid #ff8a2a",
                background: "rgba(255,138,42,0.12)",
                color: "#ff8a2a",
                cursor: busy || !fileInfo ? "not-allowed" : "pointer",
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
              }}
            >
              <Upload size={13} /> {busy ? "Working…" : "1. Create draft"}
            </button>
            <button
              data-testid="harness-release-publish-btn"
              onClick={() => draft && publishRelease(draft.release_id)}
              disabled={busy || !canPublish}
              title={
                !draft
                  ? "Create the draft first"
                  : shaMatch === false
                    ? "SHA mismatch — do not publish"
                    : "Publish this draft"
              }
              style={{
                padding: "8px 18px",
                fontSize: 12,
                fontWeight: 700,
                borderRadius: 6,
                border: "1px solid #3ECF8E",
                background: "rgba(62,207,142,0.10)",
                color: "#3ECF8E",
                cursor: busy || !canPublish ? "not-allowed" : "pointer",
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
              }}
            >
              <Rocket size={13} /> {busy ? "Working…" : "2. Publish draft"}
            </button>
          </div>
          {draft && (
            <div
              data-testid="harness-release-draft-result"
              style={{
                marginTop: 14,
                fontSize: 12.5,
                fontFamily: "monospace",
                wordBreak: "break-all",
                background: "#181818",
                border: "1px solid #2a2a2a",
                borderRadius: 8,
                padding: "10px 12px",
              }}
            >
              <div>release_id: {draft.release_id}</div>
              <div>status: {draft.status}</div>
              <div>server sha256: {draft.artifact_sha256}</div>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  marginTop: 6,
                }}
              >
                {shaMatch === true && (
                  <>
                    <CheckCircle2 size={14} color="#3ECF8E" />
                    <span style={{ color: "#3ECF8E" }}>
                      SHA MATCHES — safe to publish
                    </span>
                  </>
                )}
                {shaMatch === false && (
                  <>
                    <XCircle size={14} color="#f87171" />
                    <span style={{ color: "#f87171" }}>
                      SHA MISMATCH — do not publish this draft
                    </span>
                  </>
                )}
                {shaMatch === null && (
                  <span style={{ color: "#888" }}>
                    Local SHA unavailable in this browser — compare the server
                    SHA above with the known zip SHA before publishing.
                  </span>
                )}
              </div>
            </div>
          )}
          {published && (
            <div
              data-testid="harness-release-publish-result"
              style={{ marginTop: 12, fontSize: 12.5, color: "#3ECF8E" }}
            >
              Published: {JSON.stringify(published)}
            </div>
          )}
        </Card>
        {/* Existing releases */}
        <Card>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>
            All releases
          </div>
          <div style={{ fontSize: 11.5, color: "#777", marginBottom: 12 }}>
            Drafts can be published from here too — the SHA check above only
            applies to a draft created in this session, so compare the SHA in
            the list with the known zip SHA first.
          </div>
          {releases.length === 0 ? (
            <div style={{ fontSize: 12, color: "#666", padding: "10px 0" }}>
              No releases yet.
            </div>
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table
                style={{
                  width: "100%",
                  borderCollapse: "collapse",
                  fontSize: 12,
                }}
              >
                <thead>
                  <tr>
                    {[
                      "Version",
                      "Platform",
                      "Status",
                      "Latest",
                      "SHA-256",
                      "Created",
                      "",
                    ].map((h) => (
                      <th
                        key={h}
                        style={{
                          textAlign: "left",
                          padding: "6px 10px",
                          fontSize: 10,
                          color: "#666",
                          textTransform: "uppercase",
                          borderBottom: "1px solid #262626",
                        }}
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {releases.map((r) => (
                    <tr
                      key={r.release_id || `${r.version}-${r.platform}`}
                      data-testid={`harness-release-row-${r.release_id || r.version}`}
                    >
                      <td
                        style={{
                          padding: "8px 10px",
                          borderBottom: "1px solid #1a1a1a",
                          fontFamily: "monospace",
                        }}
                      >
                        {r.version}
                      </td>
                      <td
                        style={{
                          padding: "8px 10px",
                          borderBottom: "1px solid #1a1a1a",
                        }}
                      >
                        {r.platform}
                      </td>
                      <td
                        style={{
                          padding: "8px 10px",
                          borderBottom: "1px solid #1a1a1a",
                        }}
                      >
                        {r.status}
                      </td>
                      <td
                        style={{
                          padding: "8px 10px",
                          borderBottom: "1px solid #1a1a1a",
                        }}
                      >
                        {r.is_latest ? "yes" : "—"}
                      </td>
                      <td
                        style={{
                          padding: "8px 10px",
                          borderBottom: "1px solid #1a1a1a",
                          fontFamily: "monospace",
                          maxWidth: 180,
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                        title={r.artifact_sha256}
                      >
                        {r.artifact_sha256 || "—"}
                      </td>
                      <td
                        style={{
                          padding: "8px 10px",
                          borderBottom: "1px solid #1a1a1a",
                          color: "#999",
                        }}
                      >
                        {ago(r.created_at)}
                      </td>
                      <td
                        style={{
                          padding: "8px 10px",
                          borderBottom: "1px solid #1a1a1a",
                          textAlign: "right",
                        }}
                      >
                        {r.status === "draft" && (
                          <button
                            data-testid={`harness-release-publish-row-${r.release_id}`}
                            onClick={() => publishRelease(r.release_id)}
                            disabled={busy}
                            style={{
                              padding: "5px 12px",
                              fontSize: 11.5,
                              fontWeight: 600,
                              borderRadius: 6,
                              border: "1px solid #3ECF8E",
                              background: "rgba(62,207,142,0.10)",
                              color: "#3ECF8E",
                              cursor: busy ? "wait" : "pointer",
                            }}
                          >
                            Publish
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
