// sonner-compatible facade over the app's real toast renderer
// (components/Toast.jsx). sonner's <Toaster/> is never mounted, so any
// `import { toast } from "sonner"` is a silent no-op — use this instead.
import { toast as rawToast, dismissToast } from "../components/Toast";

const KIND_MAP = { info: "info", success: "success", error: "error", warning: "warn" };

function emit(kind, message, opts) {
  return rawToast({ message, kind: KIND_MAP[kind], ...(opts || {}) });
}

export function toast(message, opts) { return emit("info", message, opts); }
toast.info    = (message, opts) => emit("info", message, opts);
toast.success = (message, opts) => emit("success", message, opts);
toast.error   = (message, opts) => emit("error", message, opts);
toast.warning = (message, opts) => emit("warning", message, opts);
toast.dismiss = (id) => dismissToast(id);
