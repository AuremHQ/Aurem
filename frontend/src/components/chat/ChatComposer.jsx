// Phase 1 mobile view (2026-11-24) -- the chat composer, extracted verbatim
// from ChatPanel.jsx (which was 7,058 lines) so the input surface lives in
// one place. ChatPanel passes everything the composer needs as props; the
// composer owns only its own keyboard behaviour (useVisualViewport) and its
// mobile sizing.
//
// Mobile notes:
// - Send/Stop are 48x48px with flexShrink: 0 and minWidth: 48 -- the Send
//   button collapsed to ~0px on a real 390px viewport on 2026-08-20.
// - The textarea font is 16px (via .composer-input-bare in index.css) so
//   iOS Safari does not zoom on focus.
// - The form has safe-area bottom padding, and the whole composer translates
//   up by the keyboard height so it rises above the keyboard.
import { Github, History, Loader2, Paperclip, Send, Square, X } from "lucide-react";
import { API_BASE, getToken } from "../../lib/api";
import { detectMode } from "../ChatPanelF12";
import { EXEC_MODES } from "../LoopModeToggle";
import { dismissForSession } from "../../utils/sessionDismiss";
import { preventSmartPunctuation } from "../../utils/chatTextUtils";
import { useVisualViewport } from "../../hooks/useVisualViewport";
// Components the composer renders. The extraction from ChatPanel.jsx
// missed these imports, so the composer threw `ReferenceError` at
// runtime (first hit: <ThinkingHint>) and the dashboard error boundary
// caught it. Paths are relative to src/components/chat/.
import CharCounter from "../CharCounter";
import IntentTierIndicator from "../IntentTierIndicator";
import ModeLoopPill from "../ModeLoopPill";
import ModeSelector from "../ModeSelector";
import SlashCommandMenu from "../SlashCommandMenu";
import ThinkingHint from "../ThinkingHint";
import ToolButton from "./ToolButton";
import VisualBugAnalysisCard from "../VisualBugAnalysisCard";
import VisualDiffWorkbench from "../VisualDiffWorkbench";
import { EmptyState, FileTransferList, LoadingSkeleton, NoResultsState, RateLimitState } from "../ui-states/UiStates.jsx";

export default function ChatComposer(props) {
  const {
    GH_DOT_COLOR,
    SCOPE_CHIP_DISMISS_KEY,
    activeProject,
    agent,
    agents,
    attachments,
    busy,
    chatMode,
    dragOver,
    execMode,
    exhausted,
    fileInputRef,
    ghDisplay,
    handleExecModeChange,
    handleFiles,
    handleVisualBugAnalyze,
    imageFilesRef,
    input,
    lastIntentTier,
    matchSlashCommandsForUser,
    messages,
    onKeyDown,
    openFilePicker,
    opsHistoryOpen,
    rateLimitInfo,
    runSlashCommand,
    scopeChipDismissed,
    send,
    sessionId,
    setAgent,
    setAttachments,
    setChatMode,
    setDetectedMode,
    setDragOver,
    setInput,
    setOpsHistoryOpen,
    setRateLimitInfo,
    setScopeChipDismissed,
    setShowRepoHelp,
    setSlashIdx,
    setSlashOpen,
    setVisualBugAnalysis,
    setWorkbenchReportId,
    slashIdx,
    slashOpen,
    stop,
    stopArmed,
    taRef,
    visualBugAnalysis,
    workbenchReportId
  } = props;
  const { keyboardHeight } = useVisualViewport();

  return (
    <div
      className="chat-composer"
      style={{
        // Rise above the on-screen keyboard (iOS visualViewport); no-op
        // when the keyboard is closed (keyboardHeight === 0).
        transform:
          keyboardHeight > 0 ? `translateY(${-keyboardHeight}px)` : undefined,
      }}
    >
      <form
        data-testid="chat-form"
        onSubmit={send}
        className="glass-composer"
        // Iter 284 — outline turns amber when the agent is running so
        // the composer visually pairs with the running-agent caption
        // above.  Matches the reference UI pattern where the whole
        // composer shell tints during work.
        data-agent-running={busy ? "true" : undefined}
        // Iter 59 — drag-and-drop attachment support directly on the
        // composer. dragOver state drives the visual cue.
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          if (e.dataTransfer?.files?.length) handleFiles(e.dataTransfer.files);
        }}
        style={{
          // Iter 212m-135 / 212m-140 — composer padding lives in
          // index.css (same container queries as messages). Only
          // top/bottom 14 px is structural and stays inline alongside
          // the form's display/border styling.
          display: "flex", flexDirection: "column", gap: 8, paddingBottom: "max(10px, env(safe-area-inset-bottom))",
          outline: dragOver ? "2px dashed var(--accent-2)" : "none",
          outlineOffset: -8,
          transition: "outline 120ms ease",
          // Iter 212m-197 — Founder request (option B): remove the
          // entire composer box outline so it blends with the chat
          // pane. The previous amber side/bottom borders (from
          // 212m-37) were intended to visually fuse with the
          // FounderOfferCard above; without a top edge on that card,
          // the ring looked disconnected on wide viewports. Inner
          // `.composer-card` still has its own 1px outline so the
          // textarea stays visually contained.
          border: "none",
          // Iter 212m-54 — composer background inherits from parent
          // chat panel via `transparent`.
          background: "transparent",
        }}
      >
        {/* Iter 212m-36 — TokenBanner + composer-status-bar moved
            outside the form (above the FounderOfferCard). The form
            now starts directly with the textarea wrapper. */}
          {/* Iter 158 — tier-aware thinking-state upsell pill.
              Appears 600ms into a chat-busy cycle, slides in with a
              soft amber glow, and converts dead wait time into a
              feature/upsell CTA. Founder tier renders nothing. */}
          <ThinkingHint busy={busy} />
          {/* Iter 148 — explicit warning when the active project has
              no GitHub repo wired up. Click opens a guided helper dialog
              with the exact 3 steps to connect one. The dialog drives
              both this pill and the toolbar's red GH-status indicator. */}
          {activeProject &&
            !(activeProject.github_owner && activeProject.github_repo) && (
            <button
              type="button"
              data-testid="no-repo-warning-pill"
              onClick={() => setShowRepoHelp(true)}
              title={`No GitHub repo connected to ${activeProject.name}`}
              style={{
                display: "inline-flex", alignItems: "center", gap: 6,
                fontSize: 10, fontFamily: "'JetBrains Mono', monospace",
                letterSpacing: "0.12em",
                color: "#ff8a8a",
                padding: "4px 10px",
                border: "1px solid rgba(239,68,68,0.4)",
                borderRadius: 999,
                background: "rgba(239,68,68,0.10)",
                cursor: "pointer",
                animation: "aurem-no-repo-blink 2.2s ease-in-out infinite",
              }}
            >
              ⚠ No repo linked — click to connect
            </button>
          )}

        {/* Iter 59 — Attachment pills. */}
        {attachments.length > 0 && (
          <div
            data-testid="chat-attachments-row"
            style={{
              display: "flex", flexWrap: "wrap", gap: 6,
              maxHeight: 120, overflowY: "auto",
            }}
          >
            {attachments.some((a) => a.status === "uploading" || a.status === "error") && (
              <FileTransferList files={attachments.filter((a) => a.status === "uploading" || a.status === "error").map((a) => ({ id: a.id, name: a.name, percent: a.status === "error" ? 100 : (a.progress ?? 0), size: a.size || 0, status: a.status === "error" ? "failed" : "uploading" }))} onCancel={(id) => setAttachments((prev) => prev.filter((a) => a.id !== id))} />
            )}
            {attachments.map((a) => {
              const colour =
                a.status === "uploading" ? "var(--text-faint)"
                : a.status === "error"   ? "var(--danger)"
                : "var(--accent-2)";
              const icon = a.kind === "image" ? "🖼️" : "📎";
              return (
                <span
                  key={a.id}
                  data-testid={`chat-attach-pill-${a.status}`}
                  title={a.error || `${a.name} · ${(a.size/1024).toFixed(1)} KB`}
                  style={{
                    display: "inline-flex", alignItems: "center", gap: 6,
                    padding: "4px 8px 4px 10px", fontSize: 11,
                    fontFamily: "'JetBrains Mono', monospace",
                    color: colour,
                    background: "rgba(255,255,255,0.04)",
                    border: `1px solid ${colour}55`,
                    borderRadius: 999,
                    maxWidth: 240,
                  }}
                >
                  <span>{icon}</span>
                  <span style={{
                    overflow: "hidden", textOverflow: "ellipsis",
                    whiteSpace: "nowrap", maxWidth: 160,
                  }}>{a.name}</span>
                  {a.status === "uploading" && (
                    <span style={{ opacity: 0.8 }}>{a.progress ?? 0}%</span>
                  )}
                  {a.status === "uploading"
                    ? <Loader2 size={11} className="spin" style={{ opacity: 0.6 }} />
                    : (
                      <button
                        type="button"
                        data-testid={`chat-attach-remove-${a.id}`}
                        onClick={() => {
                          delete imageFilesRef.current[a.id];
                          setAttachments((arr) => arr.filter((x) => x.id !== a.id));
                        }}
                        style={{
                          background: "none", border: "none",
                          color: colour, cursor: "pointer",
                          padding: 0, lineHeight: 1, fontSize: 14,
                        }}
                        aria-label={`Remove ${a.name}`}
                      >×</button>
                    )}
                  {/* 2026-09-12 — Visual Bug-Fix pipeline entry point.
                      Only for a ready image pill; fires the SEPARATE
                      purpose-built upload+analyze flow (never touches
                      the existing markdown-description attachment
                      above it). */}
                  {a.kind === "image" && a.status === "ready" && (
                    <button
                      type="button"
                      data-testid={`visual-bug-analyze-btn-${a.id}`}
                      title="Find & explain this visual bug"
                      onClick={() => handleVisualBugAnalyze(a.id, a.name)}
                      disabled={visualBugAnalysis?.status === "uploading" || visualBugAnalysis?.status === "analyzing"}
                      style={{
                        background: "none", border: "none",
                        color: "var(--accent)", cursor: "pointer",
                        padding: 0, lineHeight: 1, fontSize: 13,
                        opacity: (visualBugAnalysis?.status === "uploading" || visualBugAnalysis?.status === "analyzing") ? 0.4 : 1,
                      }}
                      aria-label={`Find and fix bug in ${a.name}`}
                    >🔍</button>
                  )}
                </span>
              );
            })}
          </div>
        )}

        {visualBugAnalysis && !workbenchReportId && (
          <VisualBugAnalysisCard
            result={visualBugAnalysis}
            onDismiss={() => setVisualBugAnalysis(null)}
            onGenerateFix={() => setWorkbenchReportId(visualBugAnalysis.imageId)}
          />
        )}

        {workbenchReportId && (
          <VisualDiffWorkbench
            reportId={workbenchReportId}
            token={getToken()}
            onClose={() => {
              const t = getToken();
              fetch(`${API_BASE}/visual-bugfix/${workbenchReportId}/discard`, {
                method: "POST",
                signal: AbortSignal.timeout(30000),
                headers: t ? { Authorization: `Bearer ${t}` } : {},
              }).catch(() => { /* best-effort — closing the card matters more than the API call succeeding */ });
              setWorkbenchReportId(null);
              setVisualBugAnalysis(null);
            }}
            onTokenLimit={(detail) => {
              window.dispatchEvent(new CustomEvent("aurem:token-limit-reached", {
                detail: {
                  kind: detail?.error || "monthly_task_limit_reached",
                  message: detail?.message || "You've used your free fixes this month — upgrade to continue.",
                  upgrade_url: detail?.upgrade_url || "/pricing",
                },
              }));
            }}
          />
        )}


        {/* 2026-02-18 — Active project chip.
            Replaces the legacy `[Working on project: …]` prompt-preamble
            approach (that leaked into every persisted user bubble). A
            single persistent chip above the composer keeps the scope
            visible without cluttering the message list. Hidden when
            no active project (home mode). */}
        {activeProject && activeProject.project_id !== "home" && !scopeChipDismissed && (
          <div
            data-testid="active-project-chip"
            style={{
              display:       "inline-flex",
              alignItems:    "center",
              gap:           6,
              padding:       "5px 8px 5px 8px",
              marginBottom:  8,
              background:    "var(--panel-2, rgba(255,255,255,0.03))",
              border:        "1px solid var(--border, rgba(255,255,255,0.09))",
              borderRadius:  999,
              fontSize:      12,
              lineHeight:    1.3,
              color:         "var(--text-dim, #9aa0aa)",
              fontFamily:    "ui-monospace, 'JetBrains Mono', monospace",
              maxWidth:      "100%",
              overflow:      "hidden",
            }}
            title={`ORA is scoped to ${activeProject.github_owner}/${activeProject.github_repo}@${activeProject.branch}. Every message inherits this repo context — no need to paste paths.`}
          >
            <span aria-hidden="true" style={{ fontSize: 11 }}>📌</span>
            <span style={{ color: "var(--text-faint, #6a6f78)" }}>Scope:</span>
            <span
              data-testid="active-project-chip-name"
              style={{
                color:        "var(--text, #d8dae0)",
                fontWeight:   500,
                overflow:     "hidden",
                textOverflow: "ellipsis",
                whiteSpace:   "nowrap",
              }}
            >
              {activeProject.name}
            </span>
            <span style={{ color: "var(--text-faint, #6a6f78)" }}>·</span>
            <span
              data-testid="active-project-chip-branch"
              style={{
                color:      "var(--text-dim, #9aa0aa)",
                overflow:   "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {activeProject.github_owner}/{activeProject.github_repo}
              @{activeProject.branch}
            </span>
            <button
              type="button"
              data-testid="active-project-chip-dismiss"
              onClick={(e) => {
                e.stopPropagation();
                setScopeChipDismissed(true);
                dismissForSession(SCOPE_CHIP_DISMISS_KEY);
              }}
              aria-label="Dismiss"
              title="Dismiss — won't show again until you log in again"
              style={{
                flexShrink: 0,
                display: "inline-flex", alignItems: "center", justifyContent: "center",
                width: 16, height: 16, marginLeft: 2,
                borderRadius: "50%", border: "none", background: "transparent",
                color: "var(--text-faint, #6a6f78)", cursor: "pointer", padding: 0,
              }}
            >
              <X size={11} strokeWidth={2.5} />
            </button>
          </div>
        )}

        {/* 2026-08-22 — guided prompt-starter panel: 5 plain-English
            categories with real example phrasing, shown only before
            this project's first real message. Clicking one pre-fills
            the composer — never auto-sends. `messages` is hydrated
            from real persisted history on load (see the
            `turns.map(...)` loader), so `length <= 1` (just WELCOME)
            is already a reliable "never sent a real message" signal —
            no extra localStorage flag needed. Replaces the older
            3-pill FirstMessageChips with 5 clearer categories. */}
        {/* 2026-06 · founder-directed placement — the ORA GUIDE
            prompt-starter card moved to the right-side Advisor panel
            (see Dashboard.jsx topSlot). It no longer renders inline
            here in the center chat stream; picks arrive via the
            "aurem:starter-pick" event listener below. */}

        {/* Iter 147 — unified composer card: textarea + toolbar share
            one rounded surface so it reads as a single chat input. */}
        {rateLimitInfo && (
          <RateLimitState retryInSeconds={rateLimitInfo.retrySecs} onUnlockAlternative={() => setRateLimitInfo(null)} alternativeLabel="Dismiss for now" />
        )}
        {messages.length === 1 && (
          <EmptyState title="No messages yet" message="Describe the fix you want, or pick a starter prompt below." />
        )}
        {messages.length === 0 && (
          <LoadingSkeleton lines={3} label="Loading messages…" />
        )}
        <div className="composer-card" data-testid="composer-card"
             style={{ position: "relative" }}>
        {/* Iter 212m-190 · Session 3 — Slash-command popover. Absolute
            positioned above the textarea; visible while `slashOpen`
            AND the input still starts with a slash pattern. */}
        {slashOpen ? (matchSlashCommandsForUser(input).length === 0 ? (<NoResultsState query={input} onClearFilters={() => setSlashOpen(false)} />) : (<SlashCommandMenu matches={matchSlashCommandsForUser(input)} selectedIndex={slashIdx} onPick={(cmd) => runSlashCommand(cmd, "menu_click")} />)) : null}
        <textarea
          ref={taRef}
          data-testid="chat-input"
          className="composer-input-bare"
          maxLength={20000}
          value={input}
          spellCheck={false}
          autoCorrect="off"
          onBeforeInput={preventSmartPunctuation}
          onChange={(e) => {
            const v = e.target.value;
            setInput(v);
            setDetectedMode(detectMode(v));
            // Iter 212m-190 · Session 3 — Slash-menu detection. Opens
            // the popover the moment the user types a leading "/",
            // filters as they keep typing, closes once the prefix is
            // gone or the input contains a newline.
            const isSlashy = v.startsWith("/") && !v.includes("\n") && matchSlashCommandsForUser(v).length > 0;
            setSlashOpen(isSlashy);
            if (!isSlashy) setSlashIdx(0);
            // Iter 212m-123 — Per founder spec: TopBar / focus chrome
            // hides ONLY once the user has actually TYPED something.
            // Pure focus (click into the input) no longer triggers
            // hide — the previous `onFocus` dispatch was removed.
            if (v.length > 0) {
              try { window.dispatchEvent(new CustomEvent("aurem:chat-focus")); }
              catch { /* ignore */ }
            }
          }}
          onKeyDown={onKeyDown}
          onPaste={(e) => {
            // Iter 59 — paste-to-attach. If the clipboard contains any
            // image (Cmd-V from a screenshot tool), capture them as
            // attachments instead of letting them stringify into text.
            const items = e.clipboardData?.items || [];
            const files = [];
            for (const it of items) {
              if (it.kind === "file") {
                const f = it.getAsFile();
                if (f) files.push(f);
              }
            }
            if (files.length) {
              e.preventDefault();
              handleFiles(files);
            }
          }}
          placeholder="Ask ORA to build, fix, or scan… (type / for scan commands)"
          // Iter 212m-201 — Composer height depends on execMode:
          //   • Prompt mode  → min 3 rows (extra breathing room for
          //     longer natural-language asks now that LoopStepBar is
          //     hidden).
          //   • Loop mode    → min 2 rows (LoopStepBar sits above and
          //     consumes vertical space, so keep composer compact).
          // Still auto-grows up to 6 rows based on \n count.
          rows={Math.min(
            6,
            Math.max(
              execMode === EXEC_MODES.LOOP ? 2 : 3,
              input.split("\n").length
            )
          )}
          autoFocus
          // Iter 280 P0 fix — allow typing during an active loop so
          // the queue-next feature (Iter 279) is actually reachable.
          // The submit handler still gates network I/O via `busy`;
          // this only unlocks typing + the 409 → queue confirm path.
          // `exhausted` (token depletion) still hard-locks the input.
          disabled={exhausted}
        />

        {/* Toolbar — inside the same card as the textarea */}
        <div className="composer-toolbar">
          {/* Visually hidden, NOT display:none: a display:none file input cannot be opened reliably with a programmatic click. */}
      <input
            ref={fileInputRef}
            type="file"
            multiple
            accept=".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.png,.jpg,.jpeg,.webp,.gif,.txt,.md,.csv,.json,.js,.jsx,.ts,.tsx,.py,.html,.css,.xml,.yml,.yaml"
        aria-label="Attach file"
        data-testid="chat-file-input"
            style={{ position: "absolute", width: 1, height: 1, padding: 0, margin: -1, overflow: "hidden", clip: "rect(0, 0, 0, 0)", whiteSpace: "nowrap", border: 0, opacity: 0, pointerEvents: "none" }}
            onChange={(e) => {
              handleFiles(e.target.files);
              e.target.value = "";
            }}
          />
          {activeProject && (
            <span style={{ marginRight: 4 }} />
          )}
          <ToolButton
            testid="chat-attach-btn"
            title="Attach file — PDF, DOCX, XLSX, PPTX, images, code (max 25 MB)"
            onClick={openFilePicker}
            Icon={Paperclip}
            wide
          />
          {/* Iter 339e — Ops History toggle (founder request: keep the
              chat window clean — the ship/rollback timeline shows ONLY
              when this is toggled on). Replaced the old Codebase Graph
              toggle; Graph stays reachable from the top tab bar. */}
          {activeProject?.project_id && activeProject.project_id !== "home" && (
            <ToolButton
              testid="ops-history-toggle-btn"
              title="Ship history — what shipped and what was rolled back (click to show/hide)"
              onClick={() => setOpsHistoryOpen((v) => !v)}
              Icon={History}
              active={opsHistoryOpen}
              wide
            />
          )}
          {/* Iter 212m-55 — 1-click security scanner. Shield icon opens
              right-side drawer with vulnerability findings. Only shown
              when a real project with a connected GitHub repo is
              active (the scanner needs an owner/repo/PAT to read).
              Iter 212m-56 — red dot badge with critical+high count
              when the latest cached scan found findings. Same pattern
              as the GitHub status dot above. */}
          {/* Iter 212m-162 — Security Scan composer button removed.
              The scanner is now surfaced as a "Coming soon" card inside
              /tools (Developer tools) — see ToolsPage.jsx. Removing
              the composer affordance keeps the chat header lean and
              consolidates all upcoming security/health tooling on
              one page. */}
          {/* Iter 212m-93 — REMOVED "Vanguard active" green pill from
              composer per founder spec (matches v0 lean look). The
              security guarantee is still surfaced via the bottom
              caption "ORA · Vanguard reviews every change before it
              ships." and via the Shield icon in the toolbar. */}
          {/* Iter 146 — passive GitHub status indicator.
              Green dot = active project has a connected repo (push works
              from the Projects page). Red dot = no repo configured.
              Click routes to Projects so the user can wire one up — we
              no longer expose AUREM-owned PAT pushes to end users. */}
          {/* 2026-09-12 — GitHub connection toggle is now the ONLY
              UI signal for connection state, per the founder's
              clarified spec (no banners of any kind for this
              category — see ghDisplay above + useGithubConnectionStatus).
              green = connected & healthy, amber = transient issue /
              retrying, red = disconnected / never connected / failed. */}
          <button
            type="button"
            data-testid="chat-github-status"
            title={ghDisplay.title}
            onClick={() => { if (ghDisplay.onClick) ghDisplay.onClick(); }}
            style={{
              position: "relative",
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              width: 38,
              height: 32,
              borderRadius: 8,
              background: "transparent",
              border: "1px solid var(--border, rgba(255,200,120,0.16))",
              cursor: ghDisplay.onClick ? "pointer" : "default",
              color: "var(--text-dim)",
            }}
          >
            <Github size={15} />
            <span
              aria-hidden="true"
              data-testid="chat-github-status-dot"
              data-status={ghDisplay.color}
              className={ghDisplay.color === "amber" ? "animate-pulse" : undefined}
              style={{
                position: "absolute",
                top: 4,
                right: 4,
                width: 6,
                height: 6,
                borderRadius: 999,
                background: GH_DOT_COLOR[ghDisplay.color],
                boxShadow: `0 0 6px ${GH_DOT_COLOR[ghDisplay.color]}b3`,
              }}
            />
          </button>
          {/* Iter 154 — legacy `chat-maxx-btn` removed. Maxx is now
              selected via the ModeSelector pill on the right; the
              standalone toggle was redundant and confused users. */}
          {/* Iter 212m-195 — Vanguard caption pulled INTO the toolbar
              row as the middle flex-1 element. Previously it lived
              as a separate row underneath (`composer-footer-caption`)
              which made the composer visually two-tier. User wanted
              a single bottom row with icons on left, security line
              centered, mode toggles + send on right — this span is
              exactly that middle piece. Retains original testid so
              existing tests keep passing. */}
          <span
            data-testid="composer-footer-caption"
            style={{
              flex: 1, textAlign: "center", padding: "0 12px",
              fontSize: 10, color: "var(--text-faint, #666)",
              fontFamily: "'JetBrains Mono', monospace",
              letterSpacing: "0.04em",
              userSelect: "none",
            }}>
            ORA · Vanguard reviews every change before it ships.
          </span>
          {/* Iter 212m-97 — REMOVED in-composer <ModeSelector>. Swift/
              Pro/Maxx now lives ONLY in the TopBar (single source of
              truth). The two are kept in sync via the
              `aurem:set-chat-mode` ↔ `aurem:chat-mode-changed`
              custom-event bridge so the chat backend still receives
              the right `mode` payload. */}
          {/* Iter 145 — agent selector hidden. AUREM is default for
              everyone; ORA runs as a silent shadow-learner in the
              backend (see services/ora_learning.py). */}
          {false && agents.length > 1 && (
            <select
              data-testid="chat-agent-select"
              value={agent}
              onChange={(e) => setAgent(e.target.value)}
              title="Pick which agent answers this chat"
              style={{
                fontSize: 11,
                fontFamily: "'JetBrains Mono', monospace",
                padding: "4px 8px",
                background: "var(--bg)",
                color: "var(--text)",
                border: "1px solid var(--border)",
                borderRadius: 6,
                cursor: "pointer",
              }}
            >
              {agents.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.label}
                </option>
              ))}
            </select>
          )}
          {/* Iter 212m-149 → 212m-163 — Intent Tier Indicator. */}
          <IntentTierIndicator liveText={input} lastTier={lastIntentTier} />
          <CharCounter value={input} max={20000} style={{ marginRight: 8 }} />
          {/* 2026-08-21 — founder request: Swift/Pro/Maxx (+ Loop
              sub-choice) moved here from the TopBar — same spot the
              old standalone "LOOP OFF/ON" toggle used to live. */}
          <ModeLoopPill
            mode={chatMode}
            onModeChange={setChatMode}
            execMode={execMode}
            onExecModeChange={handleExecModeChange}
          />


          {busy ? (
            <>
              {/* Iter 284 — queue-next affordance.
                  When the loop is running AND the user has typed a new
                  prompt in LOOP mode, show a visible send button that
                  fires the exact same send() handler.  Previously only
                  the stop button rendered here, so the queue-next
                  feature (Iter 279) was only reachable via keyboard
                  Enter — undiscoverable. */}
              {execMode === EXEC_MODES.LOOP && input.trim() && sessionId && !exhausted && (
                <button
                  type="button" data-testid="chat-queue-send"
                  onClick={() => send()}
                  title="Queue this message — runs when the current loop finishes"
                  style={{
                    display: "inline-flex", alignItems: "center",
                    justifyContent: "center",
                    width: 48, height: 48, borderRadius: "50%",
                    flexShrink: 0, minWidth: 48,
                    background: "rgba(255,102,8,0.9)",
                    border: "1px solid rgba(255,102,8,0.9)",
                    color: "#0A0A0A", cursor: "pointer",
                    boxShadow: "0 0 20px -6px rgba(255,102,8,0.7)",
                    transition: "background 140ms, transform 100ms",
                    marginRight: 6,
                  }}
                >
                  <Send size={15} strokeWidth={2.5} style={{ pointerEvents: "none" }} />
                </button>
              )}
              <button
                type="button" data-testid="chat-stop"
                onClick={stopArmed ? stop : undefined}
                disabled={!stopArmed}
                title={stopArmed ? "Stop streaming" : "Stopping available in a moment…"}
                aria-label="Stop generation"
                style={{
                  display: "inline-flex", alignItems: "center", justifyContent: "center",
                  width: 48, height: 48, borderRadius: "50%",
                  flexShrink: 0, minWidth: 48,
                  background: "rgba(255,102,8,0.18)",
                  border: "1px solid rgba(255,102,8,0.45)",
                  color: "#FF6608",
                  cursor: stopArmed ? "pointer" : "default",
                  opacity: stopArmed ? 1 : 0.5,
                  transition: "background 140ms, transform 100ms, opacity 200ms",
                }}
              >
                <Square size={14} strokeWidth={2.5} />
              </button>
            </>
          ) : (
            <button
              type="button" data-testid="chat-send"
              // Iter 212m-132 — Bug fix: mouse click was sometimes
              // not firing because the `disabled` attribute could be
              // stale during the click frame (browser respects DOM
              // disabled, but React's render hadn't flipped it to
              // `false` yet after the user typed the last char +
              // immediately clicked).  Enter worked because the
              // onKeyDown bypassed this gate entirely.  Fix:
              //   1. ALWAYS keep the button clickable — the send()
              //      function already gates on the same conditions
              //      (`!text && !readyAttachments.length || busy ||
              //       !sessionId` at line ~1228).  Visual
              //      "disabled" treatment stays via inline styles.
              //   2. Drop the redundant e.preventDefault() and
              //      e.stopPropagation() — button has type="button"
              //      so it has no default-submit action to prevent.
              //   3. Add `onPointerDown` as a redundant earlier-fire
              //      handler in case the click event gets eaten by
              //      a transient overlay (defence in depth).
              //   4. Explicit `pointer-events:auto` + relative
              //      z-index:5 to guarantee no ancestor with
              //      `pointer-events:none` swallows the click.
              //   5. Use `aria-disabled` for SR users instead of
              //      the native `disabled` attribute.
              aria-disabled={!input.trim() || !sessionId || exhausted}
              aria-label="Send message"
              onPointerDown={(e) => {
                // Native pointer event fires BEFORE click — if any
                // overlay is at z-index above this button, this is
                // our last reliable hook to capture the press.
                // We do NOT send() here (it would double-fire with
                // the synthetic onClick); we just guarantee focus
                // moves to the button so React's onClick will land.
                if (e.currentTarget) e.currentTarget.focus();
              }}
              onClick={() => {
                // Iter 212m-132 — call send() with no event arg
                // (matches the Enter-key path at onKeyDown so both
                // entrypoints take the IDENTICAL code path inside
                // send()).  send() already handles all gating.
                send();
              }}
              title={
                exhausted
                  ? "Tokens exhausted — upgrade your plan"
                  : execMode === EXEC_MODES.LOOP
                    ? "Run loop"
                    : "Send"
              }
              style={{
                display: "inline-flex", alignItems: "center", justifyContent: "center",
                width: 48, height: 48, borderRadius: "50%",
                // 2026-08-20 — was collapsing to ~0px wide on a real
                // 390px mobile viewport (nothing in this toolbar row
                // protected it from flexbox shrink) — users could
                // type a message but had no usable Send button.
                flexShrink: 0, minWidth: 48,
                background: (!input.trim() || !sessionId || exhausted)
                  ? "rgba(255,102,8,0.25)"
                  : "#FF6608",
                border: "none",
                color: (!input.trim() || !sessionId || exhausted) ? "#7A3B0B" : "#0A0A0A",
                cursor: (!input.trim() || !sessionId || exhausted) ? "not-allowed" : "pointer",
                opacity: (!input.trim() || !sessionId || exhausted) ? 0.55 : 1,
                boxShadow: (!input.trim() || !sessionId || exhausted)
                  ? "none"
                  : "0 0 20px -6px rgba(255,102,8,0.7)",
                transition: "background 140ms, opacity 140ms, transform 100ms",
                // Iter 212m-132 — guarantee click reachability.
                pointerEvents: "auto",
                position: "relative",
                zIndex: 5,
              }}
            >
              <Send size={15} strokeWidth={2.5} style={{ pointerEvents: "none" }} />
            </button>
          )}
        </div>
        {/* Iter 212m-195 — old separate footer caption row removed;
            the caption now lives INSIDE the toolbar between left icons
            and right toggles (flex-1 span above), matching the v2
            single-bottom-row layout. */}
        </div>
      </form>
    </div>
  );
}
