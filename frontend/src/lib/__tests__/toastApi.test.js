import { describe, it, expect, vi, beforeEach } from "vitest";

const rawToast = vi.fn(() => 42);
const dismissToast = vi.fn();
vi.mock("../../components/Toast", () => ({
  toast: (...a) => rawToast(...a),
  dismissToast: (...a) => dismissToast(...a),
}));

import { toast } from "../toastApi";

beforeEach(() => { rawToast.mockReset(); dismissToast.mockReset(); });

describe("lib/toastApi — sonner-compatible facade over components/Toast", () => {
  it.each([
    ["error", "error"],
    ["success", "success"],
    ["info", "info"],
    ["warning", "warn"],
  ])("toast.%s routes to the real renderer with kind=%s", (method, kind) => {
    toast[method]("hello", { id: "x", duration: 100 });
    expect(rawToast).toHaveBeenCalledTimes(1);
    expect(rawToast.mock.calls[0][0]).toEqual({ message: "hello", kind, id: "x", duration: 100 });
  });

  it("bare toast(msg) is an info toast and returns the renderer id", () => {
    expect(toast("plain")).toBe(42);
    expect(rawToast.mock.calls[0][0]).toMatchObject({ message: "plain", kind: "info" });
  });

  it("toast.dismiss forwards to dismissToast", () => {
    toast.dismiss("abc");
    expect(dismissToast).toHaveBeenCalledWith("abc");
  });
});
