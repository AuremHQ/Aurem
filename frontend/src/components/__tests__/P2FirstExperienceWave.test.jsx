/**
 * P2 First-Experience Wave — named tests (2026-09-07).
 *   P2-A t_p2a_single_connect_hero          — one "Connect your repo" hero, FinishSetupBanner gone
 *   P2-B t_p2b_first_scan_nudge_*           — nudge only when no scan ever ran; prefill "/scan"; dismiss sticks
 *   P2-C t_p2c_no_jargon_in_first_experience — banned-jargon guard over first-experience surfaces
 *   P2-F t_p2f_first_reply_identity         — first assistant message says "I'm ORA, by Aurem"
 * (P2-D → ShipUIUnification.r3_physical.test.jsx · P2-E → UserNotificationBell.p2a.test.jsx ·
 *  P2-G → PrMiniGuide.r6.test.jsx — run alongside.)
 */
import fs from "fs";
import path from "path";
import { describe, it, expect } from "vitest";

const SRC = path.resolve(__dirname, "..", "..");
const read = (p) => fs.readFileSync(path.join(SRC, p), "utf8");

describe("P2-A · first-session declutter", () => {
  // 2026-09-09 — founder ticket Part 0: ConnectRepoBanner itself is
  // now fully removed from Dashboard (not just deduped to one copy).
  // It's superseded by the mandatory repo-connect gate (asGate=true
  // NewUserWizard, shown before chat is reachable on first load) plus
  // a single small post-skip toggle-badge (EngagementNudge) once a
  // user has explicitly skipped that gate — never more than ONE
  // connect-related affordance visible in chat at a time.
  it("t_p2a_single_connect_hero: Dashboard has no ConnectRepoBanner, no FinishSetupBanner, and exactly one gated connect entry point", () => {
    const dash = read("pages/Dashboard.jsx");
    expect(dash).not.toMatch(/import\s+FinishSetupBanner/);
    expect(fs.existsSync(path.join(SRC, "components/FinishSetupBanner.jsx"))).toBe(false);
    expect(dash).not.toMatch(/import\s+ConnectRepoBanner/);
    expect(dash.match(/<ConnectRepoBanner\b/g) || []).toHaveLength(0);
    expect(dash.match(/\{showGate\s*&&/g) || []).toHaveLength(1);
    expect(dash.match(/<EngagementNudge\s+variant="post_skip"/g) || []).toHaveLength(1);
  });
});

describe("P2-B · new-user first-scan nudge", () => {
  // 2026-09-09 — founder ticket Part 0 (round 2): the standalone
  // FirstScanNudge banner (a second competing chat banner, circled
  // in the same screenshot as EngagementNudge's badge) is deleted.
  // Its exact behavior — endpoint, dismiss-key format, message copy,
  // and the "/scan" prefill handoff — now lives inside
  // OraGuideMascot's `secondaryNudge` system (single floating entry
  // point). Static source checks, same style as P2-C below, since
  // fully rendering the mascot needs react-router + several hooks
  // mocked for no additional regression value over asserting the
  // literal behavior survived the move intact.
  const mascot = read("components/OraGuideMascot.jsx");

  it("t_p2b_first_scan_nudge_shows_when_no_scan_ever_ran: calls the same status endpoint, gated on project + no user message yet", () => {
    expect(mascot).toMatch(/api\.get\(\s*"\/onboarding\/first-scan\/status"/);
    expect(mascot).toMatch(/status\s*!==\s*"skipped"/);
    expect(mascot).toMatch(/!effectiveProjectId.*hasUserMessage|hasUserMessage.*!effectiveProjectId|if \(!effectiveProjectId[\s\S]{0,40}hasUserMessage\)/);
  });

  it("t_p2b_first_scan_nudge_hidden_when_scan_exists_or_user_typed: hasUserMessage clears any pending first_scan nudge", () => {
    expect(mascot).toMatch(/aurem:user-message-sent/);
    expect(mascot).toMatch(/prev\?\.kind !== "first_scan"/);
  });

  it("t_p2b_first_scan_nudge_click_prefills_scan_and_dismiss_sticks: exact same event/message and localStorage dismiss key as the original", () => {
    // 2026-09-21 — upgraded from a text-prefill handoff to a direct
    // one-click scan trigger (same aurem:run-scan event a real
    // "/bug-hunt" dropdown pick uses) — the scan actually runs
    // instead of just filling the composer with "/scan" text the
    // user still had to send themselves. Same underlying contract
    // (clicking the nudge kicks off a scan), stronger mechanism.
    expect(mascot).toMatch(/secondaryNudge\.kind === "first_scan"[\s\S]{0,80}aurem:run-scan.*commandId:\s*"scan"/);
    expect(mascot).toMatch(/FIRST_SCAN_DISMISS_KEY\s*=\s*\(pid\)\s*=>\s*`aurem_first_scan_nudge_dismissed_\$\{pid\}`/);
    expect(mascot).toMatch(/New here\? Start with a quick scan of your site/);
  });
});


describe("P2-C · jargon sweep guard", () => {
  const SURFACES = [
    "components/ConnectRepoBanner.jsx", "components/NewUserWizard.jsx",
    "components/OperationHistory.jsx", "components/ShipApproveCard.jsx",
    "components/PrMiniGuide.jsx", "components/OraGuideMascot.jsx",
    "components/FirstScanCard.jsx", "components/RollbackConfirmModal.jsx",
    "components/TaskProgressCard.jsx", "components/OraPreviewPanel.jsx",
    "pages/Integrations.jsx", "pages/Deploy.jsx",
  ];
  const BANNED = /\b(SSE|HMAC|JWT|webhook|orchestrator|tool call|stdout|traceback|installation_id|payload|force-push|force push|backend|ORAContext|BINContext|Ops history)\b/;

  it("t_p2c_no_jargon_in_first_experience: no banned developer-internal jargon in visible JSX text", () => {
    const hits = [];
    for (const f of SURFACES) {
      let src = read(f).replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/[^\n]*/gm, "");
      for (const m of src.matchAll(/>([^<>{}]{6,})</g)) {
        const t = m[1];
        if (/=>|\breturn\b|\(\)/.test(t)) continue;
        if (BANNED.test(t)) hits.push(`${f}: ${t.trim().slice(0, 80)}`);
      }
    }
    expect(hits).toEqual([]);
  });
});

describe("P2-F · first reply identity", () => {
  it("t_p2f_first_reply_identity: the WELCOME message opens with \"I'm ORA, by Aurem\" and the backend voice contract matches", () => {
    const chat = read("components/ChatPanel.jsx");
    expect(chat).toMatch(/const WELCOME = \{[\s\S]*?content:\s*"I'm ORA, by Aurem\./);
    const identity = fs.readFileSync(path.resolve(SRC, "../../backend/services/identity.py"), "utf8");
    expect(identity).toMatch(/I'm ORA, by Aurem\./);
  });
});
