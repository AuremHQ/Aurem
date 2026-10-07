// Plain-English labels for the checkup UI (S10: no raw ids, paths or jargon).
export const STAGE_LABELS = {
  runs: "Does it run?",
  safe: "Is it safe?",
  permissions: "Who can see what?",
  launch: "Is it ready to launch?",
};
export const STAGE_ORDER = ["runs", "safe", "permissions", "launch"];

export const SEVERITY_LABEL = {
  critical: "Fix now",
  high: "Should fix",
  medium: "Worth fixing",
  low: "Minor",
  info: "FYI",
};

export function verdictSentence(score, count) {
  if (count === 0) return "Everything we can check looks good.";
  if (score >= 80) return "Mostly good — a few small things to tidy up.";
  if (score >= 50) return "It works, but some things need attention before real users arrive.";
  return "There are serious problems to fix before you launch.";
}

export function itemPrompt(item) {
  return `Please help me fix this: ${item.title_plain}. ${item.impact_plain}`;
}
