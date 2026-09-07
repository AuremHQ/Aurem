/**
 * P2 First-Experience Wave — named tests (2026-09-07).
 *   P2-A t_p2a_single_connect_hero          — one "Connect your repo" hero, FinishSetupBanner gone
 *   P2-B t_p2b_first_scan_nudge_*           — nudge only when no scan ever ran; prefill "/scan"; dismiss sticks
 *   P2-C t_p2c_no_jargon_in_first_experience — banned-jargon guard over first-experience surfaces
 *   P2-F t_p2f_first_reply_identity         — first assistant message says "I'm ORA, by Aurem"
 * (P2-D → ShipUIUnification.r3_physical.test.jsx · P2-E → UserNotificationBell.p2a.test.jsx ·
 *  P2-G → PrMiniGuide.r6.test.jsx — run alongside.)
 */
import React from "react";
import fs from "fs";
import path from "path";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

const SRC = path.resolve(__dirname, "..", "..");
const read = (p) => fs.readFileSync(path.join(SRC, p), "utf8");

vi.mock("../../lib/api", () => ({
  api: { get: vi.fn(), post: vi.fn(async () => ({ data: {} })) },
  API_BASE: "http://test",
}));
import { api } from "../../lib/api";
import FirstScanNudge from "../FirstScanNudge";

describe("P2-A · first-session declutter", () => {
  it("t_p2a_single_connect_hero: Dashboard mounts exactly one ConnectRepoBanner hero and no FinishSetupBanner", () => {
    const dash = read("pages/Dashboard.jsx");
    expect(dash).not.toMatch(/import\s+FinishSetupBanner/);
    expect(fs.existsSync(path.join(SRC, "components/FinishSetupBanner.jsx"))).toBe(false);
    const mounts = dash.match(/<ConnectRepoBanner\b/g) || [];
    expect(mounts.length).toBe(1);
    expect(read("components/ConnectRepoBanner.jsx")).toMatch(/Connect your repo/i);
  });
});

describe("P2-B · new-user first-scan nudge", () => {
  beforeEach(() => { localStorage.clear(); api.get.mockReset(); });

  it("t_p2b_first_scan_nudge_shows_when_no_scan_ever_ran", async () => {
    api.get.mockResolvedValue({ data: { status: "skipped" } });
    render(<FirstScanNudge projectId="p1" hasUserMessage={false} />);
    await waitFor(() => expect(screen.getByTestId("first-scan-nudge")).toBeInTheDocument());
    expect(api.get).toHaveBeenCalledWith("/onboarding/first-scan/status", { params: { project_id: "p1" } });
  });

  it("t_p2b_first_scan_nudge_hidden_when_scan_exists_or_user_typed", async () => {
    api.get.mockResolvedValue({ data: { status: "ready" } });
    const { rerender } = render(<FirstScanNudge projectId="p1" hasUserMessage={false} />);
    await waitFor(() => expect(api.get).toHaveBeenCalled());
    expect(screen.queryByTestId("first-scan-nudge")).toBeNull();
    api.get.mockResolvedValue({ data: { status: "skipped" } });
    rerender(<FirstScanNudge projectId="p2" hasUserMessage={true} />);
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(2));
    expect(screen.queryByTestId("first-scan-nudge")).toBeNull();
  });

  it("t_p2b_first_scan_nudge_click_prefills_scan_and_dismiss_sticks", async () => {
    api.get.mockResolvedValue({ data: { status: "skipped" } });
    const seen = [];
    window.addEventListener("ora:prefill", (e) => seen.push(e.detail.message));
    const { unmount } = render(<FirstScanNudge projectId="p1" hasUserMessage={false} />);
    await waitFor(() => screen.getByTestId("first-scan-nudge-scan-btn"));
    fireEvent.click(screen.getByTestId("first-scan-nudge-scan-btn"));
    expect(seen).toEqual(["/scan"]);
    expect(screen.queryByTestId("first-scan-nudge")).toBeNull();
    expect(localStorage.getItem("aurem_first_scan_nudge_dismissed_p1")).toBe("1");
    unmount();
    api.get.mockClear();
    render(<FirstScanNudge projectId="p1" hasUserMessage={false} />);
    expect(api.get).not.toHaveBeenCalled();
    expect(screen.queryByTestId("first-scan-nudge")).toBeNull();
  });
});

describe("P2-C · jargon sweep guard", () => {
  const SURFACES = [
    "components/ConnectRepoBanner.jsx", "components/NewUserWizard.jsx",
    "components/OperationHistory.jsx", "components/ShipApproveCard.jsx",
    "components/PrMiniGuide.jsx", "components/FirstScanNudge.jsx",
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
