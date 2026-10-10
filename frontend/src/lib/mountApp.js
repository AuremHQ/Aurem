// Production serves the build-time SEO snapshot (`.ora-seo-snapshot`) inside
// #root until React replaces it. If mounting throws, that snapshot would stay
// on screen as a styled but dead page, so swap it for a plain notice instead.
export function mountApp(createRoot, rootEl, element) {
  try {
    createRoot(rootEl).render(element);
    try { window.sessionStorage.removeItem("ora_asset_reload"); } catch { /* storage blocked */ }
    return true;
  } catch (err) {
    console.error("[ora] app mount failed", err);
    rootEl.querySelectorAll(".ora-seo-snapshot").forEach((node) => node.remove());
    const note = rootEl.ownerDocument.createElement("p");
    note.textContent = "Something went wrong loading ORA. Please refresh the page.";
    rootEl.appendChild(note);
    return false;
  }
}
