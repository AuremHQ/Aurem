/**
 * 2026-10 — Item 1 ("Fix List → robot") proof test.
 *
 * Founder requirement: items appear most-important-first, and the
 * rotation never STARTS on a test file or a third-party library
 * file, even if that file happens to carry the highest severity
 * finding returned by the backend.
 */
import { describe, it, expect } from "vitest";
import { isLowPriorityFixFile, orderFixListItems } from "../OraGuideMascot";

describe("OraGuideMascot — Fix List ordering never starts on a test/vendor file", () => {
  it("isLowPriorityFixFile recognizes common test paths", () => {
    expect(isLowPriorityFixFile("backend/tests/test_auth.py")).toBe(true);
    expect(isLowPriorityFixFile("frontend/src/components/__tests__/Foo.test.jsx")).toBe(true);
    expect(isLowPriorityFixFile("frontend/src/foo.spec.js")).toBe(true);
    expect(isLowPriorityFixFile("backend/tests/test_billing.py".split("/").pop())).toBe(true);
  });

  it("isLowPriorityFixFile recognizes vendor/third-party paths", () => {
    expect(isLowPriorityFixFile("frontend/node_modules/react/index.js")).toBe(true);
    expect(isLowPriorityFixFile("vendor/stripe/lib.php")).toBe(true);
    expect(isLowPriorityFixFile("backend/.venv/lib/site-packages/requests/api.py")).toBe(true);
  });

  it("isLowPriorityFixFile does NOT flag ordinary source files", () => {
    expect(isLowPriorityFixFile("backend/routers/auth.py")).toBe(false);
    expect(isLowPriorityFixFile("frontend/src/components/ChatPanel.jsx")).toBe(false);
    expect(isLowPriorityFixFile(null)).toBe(false);
    expect(isLowPriorityFixFile(undefined)).toBe(false);
  });

  it("a CRITICAL finding in a test file is still pushed behind a MEDIUM finding in real source", () => {
    const items = [
      { finding_id: "1", severity: "critical", file: "backend/tests/test_payments.py", title: "Hardcoded test token" },
      { finding_id: "2", severity: "medium", file: "backend/routers/payments.py", title: "Missing input validation" },
    ];
    const ordered = orderFixListItems(items);
    // Backend already sorts by severity before this runs — this test
    // locks the ADDITIONAL rule: regardless of severity, a test/
    // vendor file never ends up first.
    expect(ordered[0].file).toBe("backend/routers/payments.py");
    expect(ordered[1].file).toBe("backend/tests/test_payments.py");
  });

  it("preserves relative order within each group (stable partition)", () => {
    const items = [
      { finding_id: "a", file: "backend/routers/a.py" },
      { finding_id: "b", file: "backend/tests/test_b.py" },
      { finding_id: "c", file: "backend/routers/c.py" },
      { finding_id: "d", file: "backend/node_modules/d.js" },
    ];
    const ordered = orderFixListItems(items).map((i) => i.finding_id);
    expect(ordered).toEqual(["a", "c", "b", "d"]);
  });

  it("all-test-files edge case: rotation must still include every item (no data loss)", () => {
    const items = [
      { finding_id: "x", file: "backend/tests/test_x.py" },
      { finding_id: "y", file: "backend/tests/test_y.py" },
    ];
    expect(orderFixListItems(items).map((i) => i.finding_id).sort()).toEqual(["x", "y"]);
  });
});
