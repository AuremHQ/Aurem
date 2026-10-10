import { describe, it, expect } from "vitest";
import { ADMIN_NAV, visibleAdminNav, buildGroupedAdminNav } from "../adminNav";

describe("ADMIN_NAV", () => {
  it("lists each sidebar id exactly once", () => {
    const ids = ADMIN_NAV.map((n) => n.id);
    const dupes = ids.filter((id, i) => ids.indexOf(id) !== i);
    expect(dupes).toEqual([]);
  });

  it("gives the Owner every entry and a member only what they hold", () => {
    expect(visibleAdminNav({ is_owner: true })).toHaveLength(ADMIN_NAV.length);
    const member = visibleAdminNav({ is_owner: false, screens: { "users.all": "view" } });
    expect(member.map((n) => n.id)).toEqual(["users"]);
  });

  it("groups entries under one header per section", () => {
    const grouped = buildGroupedAdminNav({});
    const headers = grouped.filter((g) => g.group).map((g) => g.group);
    expect(new Set(headers).size).toBe(headers.length);
  });
});
