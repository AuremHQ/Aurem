import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../../lib/checkupApi", async () => {
  const actual = await vi.importActual("../../lib/checkupApi");
  return { ...actual, fetchCheckupFlags: vi.fn() };
});
vi.mock("../../lib/api", async () => {
  const actual = await vi.importActual("../../lib/api");
  return { ...actual, getToken: vi.fn(() => "t"), api: { get: vi.fn(), post: vi.fn() } };
});

import { fetchCheckupFlags } from "../../lib/checkupApi";
import { useCleanChatUi, resetCheckupFlagsCache } from "../../hooks/useCheckupFlags";

function Probe() {
  const clean = useCleanChatUi();
  return <div>{clean ? null : <span data-testid="floating-helper" />}</div>;
}

beforeEach(() => { resetCheckupFlagsCache(); vi.clearAllMocks(); });

describe("chat_clean_ui flag", () => {
  it("keeps floating helpers when the flag is off (today's behaviour)", async () => {
    fetchCheckupFlags.mockResolvedValue({ chat_clean_ui: false });
    render(<Probe />);
    await waitFor(() => expect(fetchCheckupFlags).toHaveBeenCalled());
    expect(screen.getByTestId("floating-helper")).toBeInTheDocument();
  });

  it("keeps floating helpers when flags can't be loaded (fail-closed)", async () => {
    fetchCheckupFlags.mockResolvedValue({});
    render(<Probe />);
    await waitFor(() => expect(fetchCheckupFlags).toHaveBeenCalled());
    expect(screen.getByTestId("floating-helper")).toBeInTheDocument();
  });

  it("hides floating helpers only when the flag is on", async () => {
    fetchCheckupFlags.mockResolvedValue({ chat_clean_ui: true });
    render(<Probe />);
    await waitFor(() => expect(screen.queryByTestId("floating-helper")).toBeNull());
  });
});

describe("fetchCheckupFlags", () => {
  it("makes no request for anonymous visitors (no 401 -> no session-expired event)", async () => {
    const real = await vi.importActual("../../lib/checkupApi");
    const apiMod = await import("../../lib/api");
    apiMod.getToken.mockReturnValue("");
    expect(await real.fetchCheckupFlags()).toEqual({});
    expect(apiMod.api.get).not.toHaveBeenCalled();
  });
});
