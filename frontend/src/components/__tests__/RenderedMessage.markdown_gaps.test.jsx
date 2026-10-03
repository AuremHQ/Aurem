import React from "react";
import { render } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import RenderedMessage from "../RenderedMessage";

const html = (t) => render(<RenderedMessage text={t} />).container;

describe("RenderedMessage markdown gaps", () => {
  it("bold containing a code span renders as strong, no literal asterisks", () => {
    const c = html("**Done — `6c0ea98` pushed.**");
    expect(c.textContent).not.toContain("**");
    expect(c.querySelector("strong")).not.toBeNull();
  });
  it("__bold__ renders as strong", () => {
    const c = html("this is __important__ now");
    expect(c.textContent).not.toContain("__");
    expect(c.querySelector("strong")).not.toBeNull();
  });
  it("headings render without literal hashes", () => {
    const c = html("## What I found\nbody");
    expect(c.textContent).not.toContain("##");
    expect(c.querySelector("[data-testid='ora-heading']")).not.toBeNull();
  });
  it("bullet lists render without literal markers", () => {
    const c = html("- one\n- two with **bold**");
    expect(c.textContent).not.toMatch(/^\s*-\s/m);
    expect(c.textContent).not.toContain("**");
    expect(c.querySelectorAll("[data-testid='ora-list-item']").length).toBe(2);
  });
  it("star bullets and numbered lists render", () => {
    const c = html("* a\n1. b");
    expect(c.querySelectorAll("[data-testid='ora-list-item']").length).toBe(2);
  });
  it("never injects raw HTML or unsafe links", () => {
    const c = html("<img src=x onerror=alert(1)> [x](javascript:alert(1))");
    expect(c.querySelector("img")).toBeNull();
    expect(c.querySelector("a[href^='javascript']")).toBeNull();
  });
  it("code fence content is not re-parsed", () => {
    const c = html("```\n**x**\n- y\n```");
    expect(c.textContent).toContain("**x**");
  });
});
