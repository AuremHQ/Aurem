export async function captureContext(windowMs) {
    const errors = [];
    const origConsoleError = console.error;
    const onError = (e) => {
        errors.push(String(e.message || e).slice(0, 600));
    };
    const onRejection = (e) => {
        errors.push(("Unhandled rejection: " + String(e.reason)).slice(0, 600));
    };
    console.error = (...args) => {
        try {
            errors.push(args
                .map((a) => (typeof a === "string" ? a : JSON.stringify(a)))
                .join(" ")
                .slice(0, 600));
        }
        catch {
            /* noop — never let capture itself throw */
        }
        origConsoleError.apply(console, args);
    };
    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onRejection);
    await new Promise((resolve) => setTimeout(resolve, windowMs));
    console.error = origConsoleError;
    window.removeEventListener("error", onError);
    window.removeEventListener("unhandledrejection", onRejection);
    const visibleText = (document.body?.innerText || "").slice(0, 8000);
    let networkMetrics = [];
    try {
        const entries = performance.getEntriesByType("resource");
        networkMetrics = entries.slice(-100).map((e) => ({
            name: e.name,
            duration: Math.round(e.duration),
            transferSize: e.transferSize,
            initiatorType: e.initiatorType,
        }));
    }
    catch {
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
