/**
 * breakpoints.config.js — Master Plan S4 (Responsive / Viewport).
 *
 * Single source of truth for the widths CI checks key screens at.
 */

module.exports = {
  mobile:  { width: 375,  height: 812,  label: "mobile (< sm)" },
  sm:      { width: 640,  height: 800,  label: "sm" },
  tablet:  { width: 768,  height: 1024, label: "tablet (md)" },
  laptop:  { width: 1024, height: 800,  label: "laptop (lg)" },
  desktop1280: { width: 1280, height: 720,  label: "desktop (1280x720)" },
  desktop: { width: 1440, height: 900,  label: "desktop (xl)" },
  desktop1536: { width: 1536, height: 864,  label: "desktop (1536x864)" },
  desktop1920: { width: 1920, height: 1080, label: "desktop (1920x1080)" },
};
