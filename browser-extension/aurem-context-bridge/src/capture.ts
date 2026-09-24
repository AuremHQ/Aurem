/**
 * src/capture.ts — the function INJECTED into the active tab via
 * `chrome.scripting.executeScript({ func: captureContext, args: [...] })`.
 *
 * IMPORTANT constraint: this function is serialized by Chrome and
 * re-executed inside the target page's own JS context, completely
 * isolated from this extension's other modules. It must be 100%
 * self-contained — no imports, no closures over anything outside its
 * own parameters. Only browser globals (`window`, `document`,
 * `location`, `performance`, `console`) are available.
 *
 * KNOWN LIMITATION (by design, not an oversight): with only
 * `activeTab`/`scripting` permissions (no `debugger`, no persistent
 * background injection on every site), there is NO way to retrieve
 * console.error calls that already happened before this function
 * runs — browsers don't expose a "console history" API to page JS.
 * This captures errors that happen DURING a short listening window
 * after the founder clicks "Send Context to ORA" — the popup's
 * countdown UI tells them to trigger/reproduce the issue during that
 * window. Network timing (`performance.getEntriesByType("resource")`)
 * IS retroactive — the browser already buffers that, no listening
 * window needed for it.
 */
export interface CapturedData {
  url: string;
  title: string;
  visibleText: string;
  consoleErrors: string[];
  networkMetrics: Array<{
    name: string;
    duration: number;
    transferSize?: number;
    initiatorType?: string;
  }>;
}

export async function captureContext(windowMs: number): Promise<CapturedData> {
  const errors: string[] = [];
  const origConsoleError = console.error;

  const onError = (e: ErrorEvent) => {
    errors.push(String(e.message || e).slice(0, 600));
  };
  const onRejection = (e: PromiseRejectionEvent) => {
    errors.push(("Unhandled rejection: " + String(e.reason)).slice(0, 600));
  };

  console.error = (...args: unknown[]) => {
    try {
      errors.push(
        args
          .map((a) => (typeof a === "string" ? a : JSON.stringify(a)))
          .join(" ")
          .slice(0, 600)
      );
    } catch {
      /* noop — never let capture itself throw */
    }
    origConsoleError.apply(console, args as []);
  };
  window.addEventListener("error", onError);
  window.addEventListener("unhandledrejection", onRejection);

  await new Promise((resolve) => setTimeout(resolve, windowMs));

  console.error = origConsoleError;
  window.removeEventListener("error", onError);
  window.removeEventListener("unhandledrejection", onRejection);

  const visibleText = (document.body?.innerText || "").slice(0, 8000);

  let networkMetrics: CapturedData["networkMetrics"] = [];
  try {
    const entries = performance.getEntriesByType(
      "resource"
    ) as PerformanceResourceTiming[];
    networkMetrics = entries.slice(-100).map((e) => ({
      name: e.name,
      duration: Math.round(e.duration),
      transferSize: (e as unknown as { transferSize?: number }).transferSize,
      initiatorType: e.initiatorType,
    }));
  } catch {
    /* Performance API unavailable on this page — degrade gracefully */
  }

  return {
    url: location.href,
    title: document.title,
    visibleText,
    consoleErrors: errors.slice(0, 20),
    networkMetrics,
  };
}
