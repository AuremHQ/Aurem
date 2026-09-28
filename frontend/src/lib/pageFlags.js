/**
 * lib/pageFlags.js — MASTER TASK 2026-09-28 R5/Part1#3,4,5
 *
 * Module-singleton mirror of the default-OFF nav/page flags returned
 * by /auth/me (`automations_page_enabled`, `analytics_page_enabled`,
 * `ship_wall_enabled`) — same cross-component pattern as
 * lib/chipFlag.js. Default false (hidden) until /auth/me resolves.
 */
let _flags = {
  automations: false,
  analytics: false,
  wall: false,
};

export function setPageFlags(meResponseData) {
  _flags = {
    automations: meResponseData?.automations_page_enabled === true,
    analytics: meResponseData?.analytics_page_enabled === true,
    wall: meResponseData?.ship_wall_enabled === true,
  };
}

export function isAutomationsPageEnabled() {
  return _flags.automations;
}

export function isAnalyticsPageEnabled() {
  return _flags.analytics;
}

export function isShipWallEnabled() {
  return _flags.wall;
}
