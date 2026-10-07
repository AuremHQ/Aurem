// After a deploy the old hashed JS chunks are gone, so opening a lazy
// route from a tab loaded before the deploy throws "Failed to fetch
// dynamically imported module". Vite fires `vite:preloadError` for it;
// one reload fetches the new index.html and lands on the SAME URL.
// The timestamp guard stops a reload loop if the chunk is truly broken.
const KEY = "aurem_chunk_reload_at";
const MIN_GAP_MS = 30000;

export function shouldReloadForChunkError(now = Date.now()) {
  try {
    const last = Number(sessionStorage.getItem(KEY) || 0);
    if (now - last < MIN_GAP_MS) return false;
    sessionStorage.setItem(KEY, String(now));
  } catch { /* storage blocked — reload once anyway */ }
  return true;
}

export function installChunkReload(target = window, reload = () => window.location.reload()) {
  const onError = (event) => {
    if (shouldReloadForChunkError()) {
      event.preventDefault?.();
      reload();
    }
  };
  target.addEventListener("vite:preloadError", onError);
  return () => target.removeEventListener("vite:preloadError", onError);
}
