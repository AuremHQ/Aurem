// Phase 1 mobile view (2026-11-24) -- full-screen menu sheet opened by the
// hamburger in MarketingNav at <=720px. Every row is a 48px tap target and
// the sheet respects the device safe-area insets (top + bottom).
import { useEffect } from "react";
import { Link } from "react-router-dom";
import { X } from "lucide-react";
import { getToken, getUser, isAdminOrFounder } from "../../lib/api";

const ROWS = [
  { id: "features", label: "Features", to: "/#features", hash: true },
  { id: "pricing", label: "Pricing", to: "/#pricing", hash: true },
  { id: "integrations", label: "Integrations", to: "/integrations" },
  { id: "reviews", label: "Reviews", to: "/#reviews", hash: true },
  { id: "bughunt", label: "Bug Hunt", to: "/bug-hunt", adminOnly: true },
  { id: "signin", label: "Sign in", to: "/login" },
];

export default function MobileNavSheet({ open, onClose, returnTo = "" }) {
  const showBugHunt = Boolean(getToken() && isAdminOrFounder(getUser()));
  const nextQuery = returnTo ? `?next=${encodeURIComponent(returnTo)}` : "";

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event) => {
      if (event.key === "Escape") onClose?.();
    };
    document.addEventListener("keydown", onKey);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="mobile-nav-sheet"
      data-testid="mobile-nav-sheet"
      role="dialog"
      aria-modal="true"
      aria-label="Menu"
    >
      <style>{CSS}</style>
      <div className="mns-header">
        <span className="mns-logo">ORA <span className="mns-logo-sub">by Aurem</span></span>
        <button
          type="button"
          className="mns-close"
          data-testid="mobile-nav-close"
          aria-label="Close menu"
          onClick={() => onClose?.()}
        >
          <X size={22} aria-hidden="true" />
        </button>
      </div>
      <nav className="mns-rows" aria-label="Site">
        {ROWS.filter((row) => !row.adminOnly || showBugHunt).map((row) => (
          <Link
            key={row.id}
            to={row.id === "signin" ? `/login${nextQuery}` : row.to}
            className="mns-row"
            data-testid={`mobile-nav-${row.id}`}
            onClick={() => onClose?.()}
          >
            {row.label}
          </Link>
        ))}
      </nav>
      <Link to={`/signup${nextQuery}`} className="mns-cta" data-testid="mobile-nav-signup-cta" onClick={() => onClose?.()}>
        Start free
      </Link>
    </div>
  );
}

const CSS = `
.mobile-nav-sheet {
  position: fixed;
  inset: 0;
  z-index: 100;
  display: flex;
  flex-direction: column;
  background: rgba(10, 14, 26, 0.98);
  backdrop-filter: blur(18px);
  -webkit-backdrop-filter: blur(18px);
  padding-top: max(16px, env(safe-area-inset-top, 0px));
  padding-bottom: max(24px, env(safe-area-inset-bottom, 0px));
  padding-left: 20px;
  padding-right: 20px;
  color: #f8fafc;
}
.mobile-nav-sheet .mns-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 56px;
}
.mobile-nav-sheet .mns-logo {
  font-weight: 700;
  letter-spacing: 0.08em;
}
.mobile-nav-sheet .mns-logo-sub {
  font-weight: 400;
  color: #94a3b8;
  letter-spacing: 0;
}
.mobile-nav-sheet .mns-close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  min-width: 44px;
  min-height: 44px;
  border-radius: 50%;
  border: 1px solid rgba(255, 255, 255, 0.12);
  background: transparent;
  color: #f8fafc;
  cursor: pointer;
}
.mobile-nav-sheet .mns-rows {
  display: flex;
  flex-direction: column;
  margin-top: 16px;
}
.mobile-nav-sheet .mns-row {
  display: flex;
  align-items: center;
  min-height: 48px;
  padding: 12px 4px;
  font-size: 18px;
  font-weight: 600;
  color: #f8fafc;
  text-decoration: none;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}
.mobile-nav-sheet .mns-row:active {
  color: #f59e0b;
}
.mobile-nav-sheet .mns-cta {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 48px;
  margin-top: 24px;
  padding: 12px 18px;
  border-radius: 999px;
  background: #f59e0b;
  color: #0a0e1a;
  font-weight: 700;
  text-decoration: none;
}
`;
