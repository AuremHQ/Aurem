import MarketingNav from "../components/marketing/MarketingNav";
import usePageMeta from "../lib/usePageMeta";
 
const TERMS = [
  { term: "ORA", def: "The autonomous AI software engineer built by Aurem. ORA connects to a GitHub repository, plans a change, writes the code, runs security scans, and commits to your branches after your approval." },
  { term: "Loop", def: "The five-phase run ORA performs for every task: plan, approve, build, scan, and commit. You describe the task, ORA shows the plan, and only after your approval does it write the code." },
  { term: "Harness", def: "The live view of a Loop run. The harness streams each phase as it happens so you can watch the work." },
  { term: "Vanguard", def: "The pre-commit security scan. Vanguard checks every change before it lands and blocks critical findings." },
  { term: "Ship", def: "A completed task committed to GitHub. A ship is only done when the commit lands and the task reports done with a commit SHA." },
  { term: "Task", def: "A concrete file-edit job submitted to ORA. A task must name the files and the change. Vague tasks are rejected and need clarification." },
  { term: "Project", def: "A connected GitHub repository. Tasks, commits, and health scores all belong to a project." },
  { term: "MCP", def: "Model Context Protocol. ORA exposes an MCP server at /api/aurem-dev/mcp (Streamable HTTP, JSON-RPC 2.0) with tools to list projects, ship code, check task status, and list recent commits." },
  { term: "Founder plan", def: "The $9 per month flat plan. Paid plans are $9, $19, and $49 per month. There is no token billing." },
  { term: "Token billing", def: "Billing by tokens used. ORA does not do this. Plans are flat monthly prices." },
  { term: "Wrapped", def: "Your year in shipping with ORA: tasks shipped, repos touched, and streaks." },
  { term: "Codebase Health", def: "A score for a connected repository that shows what to fix first." },
  { term: "Bug Hunt", def: "A run that finds bugs in a connected repository and fixes them." },
  { term: "Ship Wall", def: "The public wall of shipped work. It is off by default and each user chooses whether to show their ships." },
];
 
export default function GlossaryPage() {
  usePageMeta({
    title: "Glossary - ORA by Aurem",
    description: "Glossary of ORA and Aurem terms: Loop, harness, Vanguard, ship, task, MCP, and more.",
    canonical: "https://auremcto.com/glossary",
  });
  return (
    <div data-testid="glossary-page">
      <MarketingNav />
      <div style={{ maxWidth: 820, margin: "0 auto", padding: "40px 24px 80px", color: "var(--text, #e8e3d3)", fontFamily: "Inter, system-ui, sans-serif", lineHeight: 1.65 }}>
        <h1 data-testid="glossary-title">Glossary</h1>
        <p>Terms used across ORA by Aurem, in plain language.</p>
        {TERMS.map((t) => (
          <section key={t.term} style={{ marginTop: 28 }}>
            <h2 style={{ fontSize: 20, margin: "0 0 6px" }}>{t.term}</h2>
            <p style={{ margin: 0 }}>{t.def}</p>
          </section>
        ))}
        <footer style={{ marginTop: 60, paddingTop: 20, borderTop: "1px solid var(--border, #2a2a2a)", fontSize: 11, color: "var(--text-faint)" }}>
          <a href="/terms">Terms of Service</a> | <a href="/privacy">Privacy Policy</a>
        </footer>
      </div>
    </div>
  );
}
