/**
 * components/VideoGenPanel.jsx — 2026-09-10, ORA Chat "Video" mode.
 *
 * Deepgram Aura-2 TTS + RunPod MuseTalk lip-sync + ffmpeg hybrid
 * compositor (backend: services/video_pipeline/pipeline.py, routes:
 * routers/admin_video_pipeline.py under /admin/video/*). Renders
 * inside OraChatDrawer when its mode switch is set to "Video" instead
 * of the normal chat message list.
 */
import React, { useEffect, useRef, useState } from "react";
import { Upload, Video, Download, Loader2, AlertTriangle } from "lucide-react";
import { api } from "../lib/api";

const STAGE_LABEL = {
  queued: "Queued…",
  synthesizing_audio: "Generating voice (Deepgram Aura-2)…",
  lip_syncing: "Syncing lips (RunPod MuseTalk) — can take 30-90s…",
  compositing: "Compositing final video (ffmpeg)…",
  done: "Done",
  failed: "Failed",
};

export default function VideoGenPanel() {
  const [keys, setKeys] = useState(null);
  const [avatar, setAvatar] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [script, setScript] = useState("");
  const [job, setJob] = useState(null);
  const [history, setHistory] = useState([]);
  const fileInputRef = useRef(null);
  const pollRef = useRef(null);

  async function refreshAll() {
    try {
      const [k, a, h] = await Promise.all([
        api.get("/admin/video/secrets"),
        api.get("/admin/video/avatar"),
        api.get("/admin/video/jobs"),
      ]);
      setKeys(k.data);
      setAvatar(a.data);
      setHistory(h.data.jobs || []);
    } catch { /* keys/avatar sections show their own empty states */ }
  }
  useEffect(() => { refreshAll(); return () => clearInterval(pollRef.current); }, []);

  async function uploadAvatar(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const r = await api.post("/admin/video/avatar", form);
      setAvatar({ configured: true, ...r.data });
    } catch (err) {
      setAvatar({ configured: false, error: err?.response?.data?.detail || "Upload failed" });
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  function pollJob(jobId) {
    clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const r = await api.get(`/admin/video/jobs/${jobId}`);
        setJob(r.data);
        if (["done", "failed"].includes(r.data.status)) {
          clearInterval(pollRef.current);
          refreshAll();
        }
      } catch { clearInterval(pollRef.current); }
    }, 3000);
  }

  async function generate() {
    if (!script.trim()) return;
    try {
      const r = await api.post("/admin/video/jobs", { script: script.trim() });
      setJob({ job_id: r.data.job_id, status: r.data.status, script: script.trim() });
      pollJob(r.data.job_id);
    } catch (err) {
      setJob({ status: "failed", error: err?.response?.data?.detail || "Could not start job" });
    }
  }

  const ready = keys && keys.deepgram_configured && keys.runpod_configured && keys.runpod_endpoint_id;
  const generating = job && !["done", "failed"].includes(job.status);

  return (
    <div data-testid="video-gen-panel"
         style={{ flex: 1, overflow: "auto", padding: 16, display: "flex",
                  flexDirection: "column", gap: 16, fontSize: 12, color: "#e8e3d3" }}>
      {keys && !ready && (
        <div data-testid="video-gen-keys-missing-banner"
             style={{ padding: "10px 12px", background: "rgba(224,122,95,0.08)",
                      border: "1px solid rgba(224,122,95,0.25)", borderRadius: 8,
                      display: "flex", gap: 8, alignItems: "flex-start", lineHeight: 1.5 }}>
          <AlertTriangle size={14} style={{ marginTop: 1, flexShrink: 0, color: "#E07A5F" }} />
          <span>
            Add your Deepgram + RunPod keys in{" "}
            <a href="/admin/settings" style={{ color: "#f4a082" }}>Admin Settings → Video generation keys</a>{" "}
            before generating.
          </span>
        </div>
      )}

      {/* Avatar */}
      <div data-testid="video-gen-avatar-section">
        <div style={{ fontWeight: 600, marginBottom: 8 }}>Founder avatar</div>
        {avatar?.configured ? (
          avatar.kind === "video" ? (
            <video data-testid="video-gen-avatar-preview" src={avatar.preview_url}
                    muted loop autoPlay style={{ width: 120, height: 120, objectFit: "cover", borderRadius: 8 }} />
          ) : (
            <img data-testid="video-gen-avatar-preview" src={avatar.preview_url} alt="avatar"
                  style={{ width: 120, height: 120, objectFit: "cover", borderRadius: 8 }} />
          )
        ) : (
          <div style={{ color: "#7a7466" }}>No avatar uploaded yet.</div>
        )}
        <input ref={fileInputRef} data-testid="video-gen-avatar-input" type="file"
               accept="image/png,image/jpeg,image/webp,video/mp4,video/quicktime"
               onChange={uploadAvatar} style={{ display: "none" }} />
        <button type="button" data-testid="video-gen-avatar-upload-btn"
                onClick={() => fileInputRef.current?.click()} disabled={uploading}
                style={{ marginTop: 8, display: "flex", alignItems: "center", gap: 6,
                          background: "#3a3428", color: "#e8e3d3", border: "none",
                          borderRadius: 8, padding: "6px 12px", cursor: "pointer", fontSize: 12 }}>
          <Upload size={13} />
          {uploading ? "Uploading…" : avatar?.configured ? "Change photo/clip" : "Upload photo or video loop"}
        </button>
      </div>

      {/* Script */}
      <div>
        <div style={{ fontWeight: 600, marginBottom: 8 }}>Script</div>
        <textarea data-testid="video-gen-script-input" value={script}
                  onChange={(e) => setScript(e.target.value)} rows={5} maxLength={2000}
                  placeholder="What should the avatar say? (max 2000 characters)"
                  style={{ width: "100%", padding: "10px 12px", background: "rgba(255,255,255,0.04)",
                            border: "1px solid rgba(255,255,255,0.08)", borderRadius: 8,
                            color: "#e8e3d3", fontSize: 13, lineHeight: 1.5, resize: "none",
                            fontFamily: "inherit" }} />
      </div>

      <button type="button" data-testid="video-gen-generate-btn" onClick={generate}
              disabled={!ready || !avatar?.configured || !script.trim() || generating}
              style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
                        background: "#E07A5F", color: "#0a0a0a", border: "none", borderRadius: 8,
                        padding: "10px 14px", cursor: "pointer", fontSize: 13, fontWeight: 600,
                        opacity: (!ready || !avatar?.configured || !script.trim() || generating) ? 0.5 : 1 }}>
        {generating ? <Loader2 size={14} className="animate-spin" /> : <Video size={14} />}
        {generating ? "Generating…" : "Generate video"}
      </button>

      {/* Progress / result */}
      {job && (
        <div data-testid="video-gen-status"
             style={{ padding: "10px 12px", borderRadius: 8,
                        background: job.status === "failed" ? "rgba(220,80,80,0.08)" : "rgba(255,255,255,0.04)",
                        border: `1px solid ${job.status === "failed" ? "rgba(220,80,80,0.3)" : "rgba(255,255,255,0.08)"}` }}>
          {job.status === "failed" ? (
            <span style={{ color: "#f88" }}>{job.error || "Generation failed."}</span>
          ) : (
            <span>{STAGE_LABEL[job.status] || job.status}</span>
          )}
          {job.status === "done" && job.video_url && (
            <div style={{ marginTop: 10 }}>
              <video data-testid="video-gen-result-video" src={job.video_url} controls
                      style={{ width: "100%", maxHeight: 320, borderRadius: 8, background: "#000" }} />
              <a data-testid="video-gen-download-btn" href={job.video_url} download={`ora-video-${job.job_id}.mp4`}
                  style={{ marginTop: 8, display: "inline-flex", alignItems: "center", gap: 6,
                            color: "#f4a082", fontSize: 12, textDecoration: "none" }}>
                <Download size={13} /> Download MP4
              </a>
            </div>
          )}
        </div>
      )}

      {/* History */}
      {history.length > 0 && (
        <div>
          <div style={{ fontWeight: 600, marginBottom: 8 }}>Recent generations</div>
          <div data-testid="video-gen-history-list" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {history.map((h, i) => (
              <div key={h.job_id} data-testid={`video-gen-history-item-${i}`}
                    style={{ display: "flex", justifyContent: "space-between", gap: 8,
                              padding: "6px 10px", background: "rgba(255,255,255,0.03)", borderRadius: 6 }}>
                <span style={{ color: "#a39d8a", overflow: "hidden", textOverflow: "ellipsis",
                                whiteSpace: "nowrap", flex: 1 }}>
                  {h.script.slice(0, 40)}{h.script.length > 40 ? "…" : ""}
                </span>
                <span style={{ color: h.status === "failed" ? "#f88" : "#7a7466" }}>{h.status}</span>
                {h.status === "done" && h.video_url && (
                  <a href={h.video_url} download={`ora-video-${h.job_id}.mp4`} style={{ color: "#f4a082" }}>
                    <Download size={12} />
                  </a>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
