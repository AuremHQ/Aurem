/**
 * breakpoints.config.js — Master Plan S4 (Responsive / Viewport).
 *
 * Single source of truth for the widths CI checks key screens at.
 * Values match Tailwind's DEFAULT breakpoints (this repo doesn't
 * override `theme.screens` in tailwind.config.js) so a layout that
 * passes here matches the same breakpoints every `sm:`/`md:`/`lg:`
 * class in the codebase already targets.
 */
module.exports = {
  mobile:  { width: 375,  height: 812, label: "mobile (< sm)" },
  sm:      { width: 640,  height: 800, label: "sm" },
  tablet:  { width: 768,  height: 1024, label: "tablet (md)" },
  laptop:  { width: 1024, height: 800, label: "laptop (lg)" },
  desktop: { width: 1440, height: 900, label: "desktop (xl+)" },
};
