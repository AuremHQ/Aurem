import { toast } from "./toastApi";

// Copy text to the clipboard and report the outcome honestly.
// Returns true when the text was copied, false when it was not.
// - success: message toasted on success (omit for no success toast; most callers already show a "Copied" state on their own button).
// - error: message toasted on failure (omit for the default message, false to stay silent when the caller shows its own error).
// - duration: toast duration in ms, passed through to the toast.
export async function copyText(text, options = {}) {
  const { success, error, duration } = options;
  const opts = duration ? { duration } : undefined;
  try {
    await navigator.clipboard.writeText(text);
    if (success) toast.success(success, opts);
    return true;
  } catch (err) {
    if (error !== false) {
      toast.error(error || "Couldn't copy — clipboard access was denied.", opts);
    }
    return false;
  }
}

export default copyText;
