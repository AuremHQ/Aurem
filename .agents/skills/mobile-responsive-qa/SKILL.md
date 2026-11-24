---
name: mobile-responsive-qa
version: "1.0.0"
description: Run the mobile responsive QA checks for the ORA by Aurem frontend -- viewport overflow, 44px tap targets, keyboard behaviour, and the Lighthouse PWA audit -- at 375/390/430px plus landscape.
metadata:
  author: Aurem
  scope: frontend mobile view
---

# Mobile Responsive QA

Run the mobile checks after any change that touches layout, navigation, the
chat composer, toasts, sheets, or the tab bar. Do not weaken, skip, or delete
any existing check; add new screens to the existing suites instead.

## When to Use

- After changing `frontend/src/index.css`, `frontend/index.html`, the chat
  composer, `MarketingNav`, `Landing`, toasts, sheets, or the tab bar.
- Before opening a PR that touches the mobile view.
- When a real-device bug report mentions a 390px viewport, the keyboard, or
  a collapsed/hidden Send button.

## When NOT to Use

- Backend-only changes with no UI surface (run the backend suite instead).
- Desktop-only layout work above the 900px breakpoint (still run the
  viewport spec if shared CSS changed).

## Instructions

### 1. Viewport overflow checks (Playwright)

```bash
cd frontend
npx playwright test tests/visual/viewport_check.spec.js
```

The spec asserts, for every key screen (landing, why-ora, demo, login,
dashboard, /ora chat, pricing, signup) at every breakpoint in
`breakpoints.config.js`, that `document.documentElement.scrollWidth` is no
more than 1px wider than `clientWidth` -- i.e. no page-level horizontal
scroll. It also runs the same screens at 375px, 390px, and 430px widths
(the three most common phone widths) and in landscape (812x375).

### 2. `overflow-x: clip` check

`html, body` must use `overflow-x: clip` (with an `@supports not
(overflow-x: clip)` fallback to `hidden` for Safari < 16). `hidden` on BOTH
`html` and `body` creates a scroll container and breaks `position: sticky`
(sticky nav, Accept bar, sticky comparison-table column). Verify:

```bash
grep -n "overflow-x: clip" frontend/src/index.css
```

and confirm the sticky first column of the Landing comparison table still
sticks while the table scrolls horizontally.

### 3. 44px tap-target assert

Every interactive element on a mobile viewport must be at least 44x44 CSS
px (WCAG 2.2 target size). The viewport spec measures the bounding boxes
of buttons and links on the key screens at 390px and fails any target under
44px in either dimension. Send/Stop in the chat composer are 48x48px and
must never collapse (flex-shrink: 0) -- this regressed on 2026-08-20 when
Send collapsed to ~0px on a real 390px viewport.

### 4. Keyboard behaviour (iOS)

- The chat textarea must be `font-size: 16px` (below 16px iOS Safari zooms
  the page on focus).
- With the keyboard open the composer must rise above it
  (`useVisualViewport` sets `--kb-height`; the composer translates up by
  that amount), the chat area shrinks, and the view auto-scrolls to the
  bottom only if the user was already at the bottom.
- The bottom tab bar auto-hides while the keyboard is open.
- Toasts sit top-centre at `top: calc(env(safe-area-inset-top) + 64px)` so
  they are never hidden behind the composer when the keyboard is open.

### 5. Safe-area insets

Every bottom-anchored surface -- composer, tab bar, action bars, sheets --
must include `env(safe-area-inset-bottom)` in its bottom padding. Check with:

```bash
grep -rn "safe-area-inset-bottom" frontend/src/components frontend/src/pages frontend/src/index.css
```

### 6. Lighthouse PWA audit

```bash
cd frontend
npx lhci autorun --config=.lighthouserc.json
```

or run Lighthouse in Chrome DevTools (mobile emulation, 390px) and confirm
the PWA category has no failing audit: installable manifest, Apple touch
icon / `apple-mobile-web-app-capable`, `viewport-fit=cover`, and
`interactive-widget=resizes-content` in the viewport meta (Android Chrome
108+ resizes the layout for the keyboard; iOS ignores it).

## Checkpoints

- [ ] `npx playwright test tests/visual/viewport_check.spec.js` passes at
  375/390/430px and landscape, with no horizontal overflow on any key screen.
- [ ] No tap target under 44x44px at 390px; Send/Stop are 48x48px.
- [ ] `overflow-x: clip` present with the Safari < 16 fallback; sticky
  elements still stick.
- [ ] Composer tracks the keyboard; tab bar hides; toasts are top-centre.
- [ ] Lighthouse PWA audit passes.
- [ ] Fonts (Jost/Cinzel/JetBrains Mono) and breakpoints (900px main,
  768/600 refinements, Tailwind defaults) unchanged.
