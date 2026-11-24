---
name: desktop-qa
description: Desktop QA checks for the ORA web app — the desktop viewport widths 1280x720, 1536x864 and 1920x1080, the window-controls-overlay gate, scrollbar-gutter, and no horizontal overflow at 1920px. Use it when verifying desktop layout, the installed PWA title-bar area, or the desktop polish in frontend/src/styles/desktop-polish.css.
---

# desktop-qa

Desktop QA for ORA. Run these checks after any change that touches desktop layout, viewports, scrollbars, or the installed PWA. Do not change visuals or layout as part of QA — report what fails and where.

## Widths to check

The desktop test sizes live in `frontend/breakpoints.config.js`:
- `desktop1280` — 1280x720
- `desktop1536` — 1536x864
- `desktop1920` — 1920x1080

The Playwright viewport check (`frontend/tests/visual/viewport_check.spec.js`) iterates every entry in that config over the key screens (`/`, `/why-ora`, `/demo`, `/login`) and fails on horizontal overflow. Run it from `frontend`:

```sh
cd frontend && npm run check:viewport
```

## Window-controls-overlay gate

The overlay is detect-only. `frontend/src/hooks/useWindowControlsOverlay.js` exposes `{ supported, visible, titlebarArea }` from `navigator.windowControlsOverlay` (`visible`, `getTitlebarAreaRect()`, debounced `geometrychange`). It renders no title bar. Gate any overlay-aware layout on `supported && visible` — on browsers without the API (`supported` is false) nothing changes. To check by hand, install the PWA in Chrome or Edge with window-controls-overlay enabled and confirm the hook reports `visible: true` and a non-empty `titlebarArea`, and that nothing renders a title bar.

## Scrollbar gutter

`frontend/src/styles/desktop-polish.css` (imported by `frontend/src/main.jsx`) sets `scrollbar-gutter: stable` on `html`, thin dark scrollbars, and `font-variant-numeric: tabular-nums` on `body`. Verify:
- `grep -n "scrollbar-gutter" frontend/src/styles/desktop-polish.css` shows the rule.
- Load a long page and a short page at 1920px: content does not shift sideways when the vertical scrollbar appears or disappears.
- Scrollbars are thin and dark; numbers in counters and timers do not jitter as digits change.

## No horizontal overflow at 1920px

At 1920x1080, `document.documentElement.scrollWidth` must be no wider than `clientWidth` (the viewport check allows 1px of rounding). Check by hand in DevTools at 1920x1080, or run `npm run check:viewport` in `frontend`, which covers 1920px automatically through `desktop1920` in the breakpoints config.

## Commands

```sh
cd frontend && npm run build   # production build must pass
cd frontend && npm run lint    # eslint must pass
cd frontend && npm test        # vitest suite
cd frontend && npm run check:viewport  # Playwright viewport check incl. 1920px
```
