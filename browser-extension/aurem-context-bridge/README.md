# AUREM Admin Context Bridge (Chrome Extension, Manifest V3)

Founder/admin-only tool: one click sends a **sanitized** snapshot of
your active tab (visible text, console errors during a short capture
window, and network timing) into an ORA chat session, so ORA can
reference it in that conversation — without AUREM ever needing
standing access to that tab or storing any new credential on the
backend.

## Install (unpacked — no Chrome Web Store review needed)

1. `cd browser-extension/aurem-context-bridge && yarn install && yarn build`
   (compiles `src/*.ts` → `src/*.js`; re-run after any source edit —
   there's no watch mode, this is intentionally minimal tooling.)
2. Open `chrome://extensions`, enable **Developer mode** (top-right toggle).
3. Click **Load unpacked**, select this `aurem-context-bridge` folder.
4. Pin the extension (puzzle-piece icon → pin) so it's always visible.

## Before you use it in production

`manifest.json`'s `host_permissions` is set to this preview
environment's URL by default:
```json
"host_permissions": ["https://bin-context-pat.preview.emergentagent.com/*"]
```
**Change this to your real AUREM domain** (e.g.
`https://auremcto.com/*`) before loading the extension there, then
reload it from `chrome://extensions`. This permission is what lets
the extension's own popup call your API without the browser's normal
cross-origin restrictions blocking it — it is scoped to your AUREM
domain only, never the pages you capture from.

## First use

1. Click the extension icon. First run asks for your **AUREM URL**
   (same domain as above) — saved locally, once.
2. Sign in with your existing AUREM admin/founder email + password.
   The JWT this returns is stored **only in this extension's local
   storage** (`chrome.storage.local`) — nothing new is created on the
   backend for this.
3. Open the ORA chat session you want the context to land in (any
   AUREM tab), and copy its **Session ID** — a small badge now shows
   it in AUREM's top bar (visible to admins/founders only), click it
   to copy.
4. Switch to the tab you actually want to capture (e.g. a vendor
   portal), click the extension icon, paste the session ID, click
   **Send Context to ORA**.
5. It captures visible text instantly, then listens for console
   errors for ~4 seconds — **reproduce the issue during that window**
   if you want the specific error captured (see limitation below) —
   then sanitizes and sends. Watch for the ✓ confirmation.
6. Switch back to the AUREM tab with that chat session open — a new
   message from ORA appears with the received context.

## What gets captured, and what doesn't

- **Visible text** (`document.body.innerText`, capped at 8,000
  chars): captured instantly at click time. Always available.
- **Network timing** (`performance.getEntriesByType("resource")`):
  retroactive — the browser already buffers this, so it reflects
  activity from BEFORE you clicked too, up to the browser's buffer
  limit. Shows request name, duration, transfer size.
- **Console errors**: **NOT retroactive.** With only `activeTab` +
  `scripting` permissions (no `debugger`, no persistent background
  script on every site you visit — deliberately, to keep the
  permission footprint minimal), there is no way to read
  `console.error` calls that already happened before you clicked.
  This captures errors that occur DURING the ~4s window after you
  click — the popup's countdown is your cue to reproduce the issue
  right then. If you need full historical console capture, that
  would require the `debugger` permission (shows Chrome's "this
  extension is debugging this tab" banner) — intentionally left out
  of this build; ask if you want that traded in.

## Security

- Permissions: `activeTab`, `scripting`, `storage` only. No
  `<all_urls>`, no persistent content scripts, no background page
  watching your browsing.
- Three independent sanitization passes strip passwords, bearer
  tokens, API keys (`sk-…`, `ghp_…`, AWS keys), and JWT-shaped tokens
  before anything leaves your machine: (1) in the injected capture
  function isn't itself a sanitizer, but (2) this extension's own
  `src/sanitize.ts` runs on the raw capture before it's ever sent, and
  (3) the backend (`routers/admin_context_bridge.py`) runs the SAME
  pattern family again server-side — it never trusts the client's
  "already sanitized" claim alone.
- Every capture requires an explicit click — nothing runs in the
  background, nothing auto-captures.
- Backend endpoint is admin/founder-gated (`require_admin_dep`, the
  same dependency every other `/admin/*` route in this codebase uses).

## Out of scope (per spec)

No background screen recording, no automated form submission — this
only ever runs when you click the button, on the tab you're looking
at, once.
