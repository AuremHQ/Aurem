// Phase 1 mobile view (2026-11-24) -- bottom tab bar for the logged-in
// shell. Four tabs (Chat / Projects / Ship / Menu), 56px tall plus the
// device safe-area inset at the bottom, icon + label per tab. It is hidden
// on desktop (min-width: 901px) and auto-hides while the on-screen keyboard
// is open (tracked with useVisualViewport) so it never covers the composer.
import { FolderKanban, Menu, MessageSquare, Rocket } from "lucide-react";
import { useVisualViewport } from "../../hooks/useVisualViewport";

const TABS = [
  { id: "chat", label: "Chat", Icon: MessageSquare },
  { id: "projects", label: "Projects", Icon: FolderKanban },
  { id: "ship", label: "Ship", Icon: Rocket },
  { id: "menu", label: "Menu", Icon: Menu },
];

export default function BottomTabBar({ activeTab = "chat", onTabChange }) {
  const { isKeyboardOpen } = useVisualViewport();
  // Stay mounted (so the hook's shared --kb-height variable is not torn
  // down) but hide while the keyboard is open.
  return (
    <nav
      className="mobile-bottom-tab-bar"
      data-testid="mobile-tab-bar"
      aria-label="Primary"
      style={isKeyboardOpen ? { display: "none" } : undefined}
    >
      <style>{CSS}</style>
      {TABS.map(({ id, label, Icon }) => {
        const active = id === activeTab;
        return (
          <button
            key={id}
            type="button"
            className={`mbb-tab${active ? " is-active" : ""}`}
            data-testid={`mobile-tab-${id}`}
            aria-current={active ? "page" : undefined}
            aria-label={label}
            onClick={() => onTabChange?.(id)}
          >
            <Icon size={22} strokeWidth={2} aria-hidden="true" />
            <span className="mbb-label">{label}</span>
          </button>
        );
      })}
    </nav>
  );
}

const CSS = `
.mobile-bottom-tab-bar {
  /* In-flow: the shell mounts this as the last child of its flex column,
     so it sits below the chat area (never over the composer) and the chat
     area shrinks to make room. Hidden on desktop by the media query below. */
  position: relative;
  flex-shrink: 0;
  width: 100%;
  z-index: 60;
  display: flex;
  height: calc(56px + env(safe-area-inset-bottom, 0px));
  padding-bottom: env(safe-area-inset-bottom, 0px);
  background: rgba(10, 14, 26, 0.92);
  backdrop-filter: blur(18px);
  -webkit-backdrop-filter: blur(18px);
  border-top: 1px solid rgba(255, 255, 255, 0.08);
}
.mobile-bottom-tab-bar .mbb-tab {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  min-width: 44px;
  min-height: 56px;
  color: #94a3b8;
  background: none;
  border: none;
  cursor: pointer;
  font-family: inherit;
}
.mobile-bottom-tab-bar .mbb-tab.is-active {
  color: #f59e0b;
}
.mobile-bottom-tab-bar .mbb-label {
  font-size: 11px;
  line-height: 1.2;
  font-weight: 600;
}
@media (min-width: 901px) {
  .mobile-bottom-tab-bar {
    display: none;
  }
}
`;
