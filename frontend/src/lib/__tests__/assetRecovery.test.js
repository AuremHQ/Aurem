import { describe, it, expect, vi } from "vitest";
import fs from "node:fs";
import path from "node:path";

// Runs the REAL inline script from index.html against a fake window, so this
// guards what actually ships rather than a copy of it.
const html = fs.readFileSync(path.resolve(__dirname, "../../../index.html"), "utf8");
const block = html.split("ora-asset-recovery")[1];
const code = block.slice(block.indexOf("<script>") + 8, block.indexOf("</script>"));

function boot() {
  const store = {};
  let listener = null;
  const win = {
    addEventListener: (type, fn, capture) => { if (type === "error") listener = { fn, capture }; },
    sessionStorage: {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = v; },
    },
    location: { href: "https://auremcto.com/", replace: vi.fn() },
  };
  new Function("window", code)(win);
  return { win, fire: (target) => listener.fn({ target }) , listener: () => listener };
}

const scriptEl = (src) => ({ tagName: "SCRIPT", src });

describe("index.html asset-recovery script", () => {
  it("listens in the capture phase (resource errors do not bubble)", () => {
    expect(boot().listener().capture).toBe(true);
  });

  it("retries once with a cache-busting URL when an /assets/ script fails", () => {
    const { win, fire } = boot();
    fire(scriptEl("https://auremcto.com/assets/index-abc123.js"));
    expect(win.location.replace).toHaveBeenCalledTimes(1);
    expect(win.location.replace.mock.calls[0][0]).toMatch(/^https:\/\/auremcto\.com\/\?_r=\d+$/);
  });

  it("does not loop: a second failure in the same session does nothing", () => {
    const { win, fire } = boot();
    fire(scriptEl("https://auremcto.com/assets/index-abc123.js"));
    fire(scriptEl("https://auremcto.com/assets/index-abc123.js"));
    expect(win.location.replace).toHaveBeenCalledTimes(1);
  });

  it("ignores failures that are not our own /assets/ scripts", () => {
    const { win, fire } = boot();
    fire(scriptEl("https://www.googletagmanager.com/gtag/js?id=AW-1"));
    fire({ tagName: "IMG", src: "https://auremcto.com/assets/x.png" });
    expect(win.location.replace).not.toHaveBeenCalled();
  });
});
