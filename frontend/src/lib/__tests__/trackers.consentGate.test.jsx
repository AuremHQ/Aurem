/**
 * Tracker consent gate (2026-09-29) — supersedes the Iter 388-ag
 * "no consent gate" Meta Pixel test, which locked in the opposite
 * behaviour (pixels firing for every visitor). That test's own header
 * said to delete it in the PR that restores the gate.
 *
 * This suite RUNS the real inline scripts from index.html in jsdom:
 * no tracker library may be injected until the visitor has opted in
 * (and Global Privacy Control is off), and a grant that arrives after
 * page load must start the loaders via cookieConsentStorage.
 */
import React from "react";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { applyConsentToTrackers } from "../cookieConsentStorage";
import CookieConsentBanner from "../../components/CookieConsentBanner";

const html = fs.readFileSync(
  path.resolve(__dirname, "..", "..", "..", "index.html"),
  "utf-8",
);

function inlineScript(marker) {
  const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
  const hit = scripts.find((s) => s.includes(marker));
  if (!hit) throw new Error(`no inline script containing ${marker}`);
  return hit;
}

const TRACKER_HOSTS = [
  "connect.facebook.net",
  "bzrcdn.openai.com",
  "googletagmanager.com",
];

function injectedTrackerScripts() {
  return [...document.querySelectorAll("script[src]")]
    .map((s) => s.getAttribute("src"))
    .filter((src) => TRACKER_HOSTS.some((h) => src.includes(h)));
}

function setConsent(cats) {
  localStorage.setItem(
    "aurem_consent",
    JSON.stringify({ v: 1, ts: new Date().toISOString(), choice: "custom", cats }),
  );
}

// Run the page's own bootstrap scripts, with the interaction-or-idle
// timer replaced so the deferred callbacks can be fired on demand.
function bootPage() {
  const deferred = [];
  window.__auremDeferThirdParty = (fn) => deferred.push(fn);
  // Only the consent helper from the first script — the defer helper is stubbed above.
  const helper = inlineScript("window.__auremHasConsent");
  const consentPart = helper.slice(helper.indexOf("window.__auremHasConsent"));
  // eslint-disable-next-line no-new-func
  new Function(consentPart)();
  for (const marker of ["fbevents.js", "oaiq.min.js", "googletagmanager.com/gtag/js"]) {
    // eslint-disable-next-line no-new-func
    new Function(inlineScript(marker))();
  }
  return () => deferred.forEach((fn) => fn());
}

function resetGlobals() {
  for (const k of [
    "fbq", "_fbq", "_fbqBooted", "oaiq", "__oaiqBooted", "gtag", "dataLayer",
    "__gtagBooted", "__auremBootPixels", "__auremHasConsent", "__auremDeferThirdParty",
  ]) delete window[k];
  document.head.querySelectorAll("script[src]").forEach((s) => s.remove());
  // the loaders insert before the first <script>, so keep one around
  const anchor = document.createElement("script");
  document.head.appendChild(anchor);
  localStorage.clear();
  Object.defineProperty(navigator, "globalPrivacyControl", { value: false, configurable: true });
}

describe("index.html trackers are consent-gated", () => {
  beforeEach(resetGlobals);
  afterEach(resetGlobals);

  it("loads nothing for a first-time visitor with no decision", () => {
    const fire = bootPage();
    fire();
    expect(injectedTrackerScripts()).toEqual([]);
  });

  it("loads nothing after 'reject non-essential' (analytics/marketing off)", () => {
    setConsent({ necessary: true, functional: true, analytics: false, marketing: false });
    const fire = bootPage();
    fire();
    expect(injectedTrackerScripts()).toEqual([]);
  });

  it("marketing consent loads Meta, OpenAI and gtag", () => {
    setConsent({ necessary: true, functional: true, analytics: false, marketing: true });
    const fire = bootPage();
    fire();
    const srcs = injectedTrackerScripts().join(" ");
    expect(srcs).toContain("connect.facebook.net");
    expect(srcs).toContain("bzrcdn.openai.com");
    expect(srcs).toContain("googletagmanager.com");
  });

  it("analytics-only consent loads gtag but not the marketing pixels", () => {
    setConsent({ necessary: true, functional: true, analytics: true, marketing: false });
    const fire = bootPage();
    fire();
    const srcs = injectedTrackerScripts().join(" ");
    expect(srcs).toContain("googletagmanager.com");
    expect(srcs).not.toContain("connect.facebook.net");
    expect(srcs).not.toContain("bzrcdn.openai.com");
  });

  it("Global Privacy Control overrides a stored 'accept all'", () => {
    setConsent({ necessary: true, functional: true, analytics: true, marketing: true });
    Object.defineProperty(navigator, "globalPrivacyControl", { value: true, configurable: true });
    const fire = bootPage();
    fire();
    expect(injectedTrackerScripts()).toEqual([]);
  });

  it("ignores a consent record with the wrong version", () => {
    localStorage.setItem(
      "aurem_consent",
      JSON.stringify({ v: 99, cats: { marketing: true, analytics: true } }),
    );
    const fire = bootPage();
    fire();
    expect(injectedTrackerScripts()).toEqual([]);
  });

  it("granting consent after page load starts the loaders via applyConsentToTrackers", () => {
    const fire = bootPage();
    fire();
    expect(injectedTrackerScripts()).toEqual([]);

    applyConsentToTrackers({ necessary: true, functional: true, analytics: true, marketing: true });
    const srcs = injectedTrackerScripts().join(" ");
    expect(srcs).toContain("connect.facebook.net");
    expect(srcs).toContain("bzrcdn.openai.com");
    expect(srcs).toContain("googletagmanager.com");
  });

  it("re-applying the same consent does not inject a tracker twice", () => {
    bootPage();
    const cats = { necessary: true, functional: true, analytics: true, marketing: true };
    applyConsentToTrackers(cats);
    applyConsentToTrackers(cats);
    expect(injectedTrackerScripts()).toHaveLength(3);
  });

  it("analytics-only grant after load does not start the marketing pixels", () => {
    bootPage();
    applyConsentToTrackers({ necessary: true, functional: true, analytics: true, marketing: false });
    const srcs = injectedTrackerScripts().join(" ");
    expect(srcs).toContain("googletagmanager.com");
    expect(srcs).not.toContain("connect.facebook.net");
    expect(srcs).not.toContain("bzrcdn.openai.com");
  });

  it("rejecting before anything loaded drops queued conversion events", () => {
    bootPage();
    window.fbq("track", "CompleteRegistration");
    window.oaiq("measure", "registration_completed");
    applyConsentToTrackers({ necessary: true, functional: true, analytics: false, marketing: false });
    expect(window.fbq.queue).toEqual([]);
    expect(window.oaiq.q).toEqual([]);
  });

  it("revoking after the pixels ran swaps in no-ops so later events go nowhere", () => {
    bootPage();
    window._fbqBooted = true;
    window.__oaiqBooted = true;
    const sendMeta = vi.fn();
    window.fbq = sendMeta;
    window.oaiq = sendMeta;
    applyConsentToTrackers({ necessary: true, functional: true, analytics: false, marketing: false });
    window.fbq("track", "Purchase");
    window.oaiq("measure", "subscription_created");
    expect(sendMeta).not.toHaveBeenCalled();
  });

  it("updates gtag consent mode on every decision", () => {
    bootPage();
    const spy = vi.fn();
    window.gtag = spy;
    applyConsentToTrackers({ necessary: true, functional: true, analytics: true, marketing: false });
    expect(spy).toHaveBeenCalledWith("consent", "update", {
      ad_storage: "denied",
      analytics_storage: "granted",
      ad_user_data: "denied",
      ad_personalization: "denied",
    });
  });

  it("no <noscript> tracking pixel fires for visitors who cannot give consent", () => {
    expect(html).not.toMatch(/facebook\.com\/tr\?/);
  });
});

describe("cookie banner buttons start (or don't start) the trackers", () => {
  let boots;
  beforeEach(() => {
    resetGlobals();
    boots = { meta: vi.fn(), openai: vi.fn(), gtag: vi.fn() };
    window.__auremBootPixels = boots;
  });
  afterEach(resetGlobals);

  const renderBanner = () =>
    render(
      <MemoryRouter>
        <CookieConsentBanner />
      </MemoryRouter>,
    );

  it("'Accept all' starts Meta, OpenAI and Google and stores the choice", () => {
    renderBanner();
    fireEvent.click(screen.getByTestId("cookie-accept-btn"));
    expect(boots.meta).toHaveBeenCalledTimes(1);
    expect(boots.openai).toHaveBeenCalledTimes(1);
    expect(boots.gtag).toHaveBeenCalledTimes(1);
    expect(JSON.parse(localStorage.getItem("aurem_consent")).cats.marketing).toBe(true);
  });

  it("'Reject non-essential' starts nothing and stores the refusal", () => {
    renderBanner();
    fireEvent.click(screen.getByTestId("cookie-reject-btn"));
    expect(boots.meta).not.toHaveBeenCalled();
    expect(boots.openai).not.toHaveBeenCalled();
    expect(boots.gtag).not.toHaveBeenCalled();
    const cats = JSON.parse(localStorage.getItem("aurem_consent")).cats;
    expect(cats.marketing).toBe(false);
    expect(cats.analytics).toBe(false);
  });

  it("analytics-only via Manage preferences starts Google but not the marketing pixels", () => {
    renderBanner();
    fireEvent.click(screen.getByTestId("cookie-manage-btn"));
    fireEvent.click(screen.getByTestId("cookie-cat-analytics").querySelector("input"));
    fireEvent.click(screen.getByTestId("cookie-save-btn"));
    expect(boots.gtag).toHaveBeenCalledTimes(1);
    expect(boots.meta).not.toHaveBeenCalled();
    expect(boots.openai).not.toHaveBeenCalled();
  });

  it("a returning visitor's stored consent is applied on load without a click", () => {
    setConsent({ necessary: true, functional: true, analytics: true, marketing: true });
    renderBanner();
    expect(screen.queryByTestId("cookie-consent-banner")).toBeNull();
    expect(boots.meta).toHaveBeenCalledTimes(1);
  });
});
