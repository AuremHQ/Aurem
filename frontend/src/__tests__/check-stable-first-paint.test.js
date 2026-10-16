/**
 * src/__tests__/check-stable-first-paint.test.js — Master Plan S3.
 * Unit-tests the checker's detection logic directly (importing
 * `checkFile` from the pure helper rather than shelling out, so it runs
 * fast inside the existing `yarn test` (vitest) suite alongside
 * everything else — no new CI job needed just to test the gate
 * itself, only to RUN it (see quality-gate.yml `stable-first-paint`
 * job, which shells out to the script against the real diff).
 */
import { describe, it, expect } from "vitest";
import { checkFile } from "../utils/stableFirstPaint.mjs";

describe("check-stable-first-paint", () => {
  it("flags a useState initializer that reads localStorage directly", () => {
    const src = `
      function Comp() {
        const [theme, setTheme] = useState(localStorage.getItem("theme") || "dark");
        return null;
      }
    `;
    const violations = checkFile("Comp.jsx", src);
    expect(violations.length).toBe(1);
    expect(violations[0].message).toContain("localStorage.");
  });

  it("flags window./document./navigator./matchMedia/Math.random/Date.now", () => {
    const cases = [
      `useState(window.innerWidth)`,
      `useState(document.title)`,
      `useState(navigator.language)`,
      `useState(matchMedia("(prefers-color-scheme: dark)").matches)`,
      `useState(Math.random())`,
      `useState(Date.now())`,
      `useState(new Date())`,
    ];
    for (const line of cases) {
      const violations = checkFile("x.jsx", `function C(){ const [a,b]=${line}; }`);
      expect(violations.length).toBe(1);
    }
  });

  it("does NOT flag a deterministic default with the real value set in useEffect", () => {
    const src = `
      function Comp() {
        const [theme, setTheme] = useState("dark");
        useEffect(() => {
          const stored = localStorage.getItem("theme");
          if (stored) setTheme(stored);
        }, []);
        return null;
      }
    `;
    expect(checkFile("Comp.jsx", src)).toEqual([]);
  });

  it("does NOT flag a useState guarded by typeof window !== 'undefined'", () => {
    const src = `useState(typeof window !== "undefined" ? window.innerWidth : 1024)`;
    expect(checkFile("x.jsx", `function C(){ const [a,b]=${src}; }`)).toEqual([]);
  });

  it("does NOT flag ordinary useState calls", () => {
    const src = `
      function Comp() {
        const [open, setOpen] = useState(false);
        const [items, setItems] = useState([]);
        const [count, setCount] = useState(0);
        return null;
      }
    `;
    expect(checkFile("Comp.jsx", src)).toEqual([]);
  });

  it("reports the correct line number for a multi-line useState", () => {
    const src = [
      "function Comp() {",
      "  const [x, setX] = useState(",
      "    localStorage.getItem('k')",
      "  );",
      "}",
    ].join("\n");
    const violations = checkFile("Comp.jsx", src);
    expect(violations.length).toBe(1);
    expect(violations[0].line).toBe(2);
  });
});
