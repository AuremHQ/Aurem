import { describe, it, expect, vi, beforeEach } from "vitest";
import { mountApp } from "../mountApp";

function rootWithSnapshot() {
  document.body.innerHTML =
    '<div id="root"><div class="ora-seo-snapshot"><h1>ORA</h1></div></div>';
  return document.getElementById("root");
}

describe("mountApp", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    vi.restoreAllMocks();
  });

  it("renders the app and leaves the root alone on success", () => {
    const root = rootWithSnapshot();
    const render = vi.fn();
    const createRoot = vi.fn(() => ({ render }));
    expect(mountApp(createRoot, root, "ELEMENT")).toBe(true);
    expect(createRoot).toHaveBeenCalledWith(root);
    expect(render).toHaveBeenCalledWith("ELEMENT");
    expect(root.querySelector(".ora-seo-snapshot")).not.toBeNull();
  });

  it("clears the one-shot asset-reload flag after a successful mount", () => {
    window.sessionStorage.setItem("ora_asset_reload", "1");
    mountApp(() => ({ render() {} }), rootWithSnapshot(), "X");
    expect(window.sessionStorage.getItem("ora_asset_reload")).toBeNull();
  });

  it("replaces the dead SEO snapshot with a notice when mounting throws", () => {
    const root = rootWithSnapshot();
    const err = vi.spyOn(console, "error").mockImplementation(() => {});
    const createRoot = () => { throw new Error("boom"); };
    expect(mountApp(createRoot, root, "X")).toBe(false);
    expect(root.querySelector(".ora-seo-snapshot")).toBeNull();
    expect(root.textContent).toMatch(/refresh/i);
    expect(err).toHaveBeenCalled();
  });
});
