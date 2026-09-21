/**
 * LazyVideo.jsx — Iter 391 · viewport-lazy demo video
 *
 * Purpose
 * -------
 * PageSpeed Insights Mobile flagged 45 MB of demo videos (5×~9-12 MB
 * each) on the Landing page as "Enormous network payload". Even
 * `preload="metadata"` fetches a first-frame + moov atom immediately
 * on page load, which serializes with the LCP paint on Slow 4G and
 * ballooned mobile LCP to 8.0 s.
 *
 * This component defers the entire <video> element until the card
 * scrolls within a generous rootMargin of the viewport, then swaps
 * in the real <video> with `preload="none"` so the file is fetched
 * only when the user actually taps play. Before that, we render a
 * tinted placeholder that matches the exact CSS shape (`video-thumb`
 * wrapper already sets aspect-ratio in Landing.css) so there is
 * ZERO layout shift when the video hydrates.
 *
 * 2026-09-22 · Bug fix — reproduced "controls show, click play, stays
 * a grey box at 0:00 forever" via `video.play()` -> rejected with
 * `MEDIA_ERR_SRC_NOT_SUPPORTED` / Chromium's
 * `PipelineStatus::DEMUXER_ERROR_NO_SUPPORTED_STREAMS`. The MP4 file
 * itself is a completely standard H.264/AAC file (verified with
 * ffprobe) — the browser hitting this simply has no licensed H.264
 * decoder at all (`video.canPlayType('video/mp4; codecs="avc1..."')`
 * returns `""`), which is the default for open-source Chromium
 * builds (common on Linux — a real slice of a developer-tool
 * audience, not just headless test runners) since H.264/AAC support
 * is a licensed codec pack Google Chrome bundles but plain Chromium
 * does not. A single `src=` attribute gives the browser no fallback
 * to try, so it just fails silently into the stuck-at-0:00 state.
 * Fix: use `<source>` children instead of a single `src=` — MP4/H.264
 * listed FIRST (best quality + universal hardware decode on the vast
 * majority of real browsers), a VP9/WebM re-encode listed second as
 * the fallback for browsers that reject the first. The browser tries
 * each `<source>` in order and plays the first one it actually
 * supports, per the HTML spec — no JS feature-detection needed.
 */
import { useEffect, useRef, useState } from "react";

export default function LazyVideo({ src, webmSrc, tint = "", "data-testid": testId }) {
  const ref = useRef(null);
  const [inView, setInView] = useState(false);

  useEffect(() => {
    if (!ref.current) return;
    if (typeof IntersectionObserver === "undefined") {
      setInView(true);
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setInView(true);
            io.disconnect();
            break;
          }
        }
      },
      { rootMargin: "200px 0px" },
    );
    io.observe(ref.current);
    return () => io.disconnect();
  }, []);

  return (
    <div ref={ref} className={`video-thumb ${tint}`} data-testid={testId}>
      {inView ? (
        <video controls playsInline preload="none" poster="">
          <source src={src} type="video/mp4" />
          {webmSrc && <source src={webmSrc} type="video/webm" />}
        </video>
      ) : (
        // Placeholder respects the parent's aspect-ratio so no CLS.
        // Solid tint from the parent class is enough; no children.
        <div aria-hidden="true" style={{ width: "100%", height: "100%" }} />
      )}
    </div>
  );
}
