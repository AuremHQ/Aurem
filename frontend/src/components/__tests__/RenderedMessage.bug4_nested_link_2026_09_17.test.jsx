/**
 * Bug 4 fix (live QA report) — markdown truncation on nested bracket
 * syntax: a badge (image-in-link, `[![alt](src)](href)`) cut off
 * mid-render. This is the exact one-line regression test the report
 * itself specified.
 */
import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import RenderedMessage from "../RenderedMessage";

describe("RenderedMessage — nested bracket / badge markdown (Bug 4 fix)", () => {
  it("renders a badge (image-in-link) without truncation", () => {
    const md = "[![Build Status](https://img.shields.io/badge/build-passing-brightgreen)](https://github.com/org/repo/actions)";
    render(<RenderedMessage text={md} />);
    const link = screen.getByRole("link");
    expect(link).toHaveAttribute("href", "https://github.com/org/repo/actions");
    const img = screen.getByRole("img");
    expect(img).toHaveAttribute("alt", "Build Status");
    expect(img).toHaveAttribute("src", "https://img.shields.io/badge/build-passing-brightgreen");
    // The whole thing must be consumed — no leftover raw "](...)"
    // text dangling after the rendered node (the exact truncation
    // symptom this bug produced before the fix).
    expect(document.body.textContent).not.toMatch(/\]\(https:\/\/github/);
  });

  it("renders a bare link with bold text inside it", () => {
    render(<RenderedMessage text="[**click here**](https://example.com)" />);
    const link = screen.getByRole("link");
    expect(link).toHaveAttribute("href", "https://example.com");
    expect(link.querySelector("strong")?.textContent).toBe("click here");
  });

  it("renders a bare image on its own", () => {
    render(<RenderedMessage text="![a diagram](https://example.com/diagram.png)" />);
    const img = screen.getByRole("img");
    expect(img).toHaveAttribute("src", "https://example.com/diagram.png");
    expect(img).toHaveAttribute("alt", "a diagram");
  });

  it("blocks an unsafe javascript: href instead of rendering it", () => {
    render(<RenderedMessage text="[click me](javascript:alert(1))" />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(document.body.textContent).toContain("click me");
  });

  it("still renders plain text with brackets that are not markdown links", () => {
    render(<RenderedMessage text="See section [1] for details, not a link." />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(document.body.textContent).toContain("[1]");
  });
});
