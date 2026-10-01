import Link from "next/link";
import { redirect } from "next/navigation";
import { ArrowLeft, Bot, ExternalLink, ShieldCheck } from "lucide-react";
import { ReportPanel } from "@/components/ReportPanel";
import { SeverityBadge } from "@/components/SeverityBadge";
import { getAgentRuns, getIncident, getReports } from "@/lib/api";
import type { AgentRun } from "@/lib/types";

// Newest first. Re-running an investigation adds to the history instead of
// replacing it; runs from before investigations were numbered count as 1.
function byInvestigation(runs: AgentRun[]): [number, AgentRun[]][] {
  const groups = new Map<number, AgentRun[]>();
  for (const run of runs) {
    const number = run.investigation ?? 1;
    groups.set(number, [...(groups.get(number) ?? []), run]);
  }
  return [...groups.entries()].sort((a, b) => b[0] - a[0]);
}

function InvestigationRuns({ number, runs }: { number: number; runs: AgentRun[] }) {
  return (
    <div className="space-y-3">
      <p className="text-xs font-semibold uppercase tracking-wide text-muted">
        {`Investigation ${number} · ${runs[0].created_at.slice(0, 16).replace("T", " ")} UTC`}
      </p>
      {runs.map((run) => (
        <article key={run.id} className="rounded-md border border-line p-3">
          <p className="text-sm font-semibold text-ink">{run.agent_name}</p>
          <pre className="mt-2 whitespace-pre-wrap break-words rounded bg-slate-50 p-2 text-xs leading-5 text-slate-700">
            {JSON.stringify(run.output, null, 2)}
          </pre>
        </article>
      ))}
    </div>
  );
}

const PLACEMENT: Record<string, string> = {
  city: "located from the headline",
  region: "approximate: region named in the headline",
  country: "approximate: country named in the headline",
};

export default async function IncidentDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [incident, runs, reports] = await Promise.all([getIncident(id), getAgentRuns(id), getReports(id)]);
  // A merged duplicate resolves to the incident it became; move to its address.
  if (incident.id !== id) {
    redirect(`/incidents/${incident.id}`);
  }

  const featureEntries = Object.entries(incident.risk_explanation.feature_importance).sort((a, b) => b[1] - a[1]);
  const investigations = byInvestigation(runs);

  return (
    <main className="min-h-screen bg-[#f4f7fb]">
      <header className="border-b border-line bg-white">
        <div className="mx-auto flex max-w-7xl flex-col gap-4 px-5 py-5">
          <Link href="/" className="inline-flex w-fit items-center gap-2 text-sm font-semibold text-sea">
            <ArrowLeft size={16} aria-hidden="true" />
            Dashboard
          </Link>
          <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <SeverityBadge severity={incident.severity} note={incident.severity_note} />
                <span className="rounded bg-slate-100 px-2 py-1 text-xs font-semibold text-muted">{incident.category}</span>
                {/* Shown instead of the stored status, which was set once from severity at
                    ingest ("investigating" for high) and implied work nobody was doing. */}
                <span
                  className="rounded bg-slate-100 px-2 py-1 text-xs font-semibold text-muted"
                  title="Active while still being reported: 72 hours, or 7 days for floods"
                >
                  {incident.active === false ? "archived" : "active"}
                </span>
              </div>
              <h1 className="mt-3 max-w-4xl text-3xl font-bold text-ink">{incident.title}</h1>
              <p className="mt-2 text-sm text-muted">
                {/* One string: JSX would drop the space around an inline expression. */}
                {PLACEMENT[incident.geo_precision ?? ""]
                  ? `${incident.location} · ${PLACEMENT[incident.geo_precision ?? ""]}`
                  : incident.location}
              </p>
            </div>
            <div className="rounded-lg border border-line bg-panel p-4 shadow-soft lg:min-w-56">
              <p className="flex items-center gap-2 text-sm text-muted">
                {/* An explicit space: flex gap only spaces it visually, so without this
                    the text reads "Risk Scoreheuristic" to copy-paste and screen readers. */}
                Risk Score{" "}
                <span
                  className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted"
                  title="Weights are hand-tuned, not learned from labelled outcomes"
                >
                  heuristic
                </span>
              </p>
              <p className="mt-1 text-4xl font-bold text-ink">{incident.risk_score}</p>
              {/* The API field is still named "confidence"; it is relabelled here because
                  it measures how decisive the keyword signal is, not whether the rating is right. */}
              <p
                className="mt-2 text-sm text-muted"
                title="How strongly the matched keywords push the score one way. Not a probability that the rating is correct."
              >
                Signal strength {(incident.risk_explanation.confidence * 100).toFixed(0)}%
              </p>
              {incident.severity_note && (
                <p className="mt-2 max-w-64 text-xs leading-5 text-muted">{incident.severity_note}</p>
              )}
            </div>
          </div>
        </div>
      </header>

      <div className="mx-auto grid max-w-7xl gap-5 px-5 py-6">
        <section className="rounded-lg border border-line bg-panel p-4 shadow-soft">
          <h2 className="text-base font-semibold text-ink">Situation Summary</h2>
          <p className="mt-3 text-sm leading-6 text-slate-700">{incident.summary}</p>
        </section>

        <div className="grid gap-5 lg:grid-cols-[0.95fr_1.05fr]">
          <section className="rounded-lg border border-line bg-panel p-4 shadow-soft">
            <h2 className="text-base font-semibold text-ink">Recommended Actions</h2>
            <div className="mt-3 space-y-2">
              {incident.recommended_actions.map((action) => (
                <div key={action} className="rounded-md border border-line bg-slate-50 px-3 py-2 text-sm text-slate-700">
                  {action}
                </div>
              ))}
            </div>
          </section>

          <section className="rounded-lg border border-line bg-panel p-4 shadow-soft">
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-base font-semibold text-ink">Risk Explanation</h2>
              <ShieldCheck className="text-sea" size={20} aria-hidden="true" />
            </div>
            <div className="mt-3 grid gap-3 md:grid-cols-2">
              <div>
                <p className="text-sm font-semibold text-ink">Drivers</p>
                <ul className="mt-2 space-y-2">
                  {incident.risk_explanation.drivers.map((driver) => (
                    <li key={driver} className="text-sm text-slate-700">{driver}</li>
                  ))}
                </ul>
              </div>
              <div>
                <p className="text-sm font-semibold text-ink">Feature Importance</p>
                <div className="mt-2 space-y-2">
                  {featureEntries.map(([feature, value]) => (
                    <div key={feature}>
                      <div className="flex justify-between gap-2 text-xs text-muted">
                        <span>{feature.replaceAll("_", " ")}</span>
                        <span>{Math.round(value * 100)}%</span>
                      </div>
                      <div className="mt-1 h-2 rounded bg-slate-100">
                        <div className="h-2 rounded bg-sea" style={{ width: `${Math.round(value * 100)}%` }} />
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </section>
        </div>

        <section className="rounded-lg border border-line bg-panel p-4 shadow-soft">
          <h2 className="text-base font-semibold text-ink">
            Sources <span className="font-normal text-muted">({incident.sources.length})</span>
          </h2>
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            {incident.sources.map((source) => (
              <a key={source.id} href={source.url} className="rounded-md border border-line p-3 hover:bg-slate-50">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold text-ink">{source.title}</p>
                    <p className="mt-1 text-xs text-muted">{source.publisher} - credibility {(source.credibility_score * 100).toFixed(0)}%</p>
                  </div>
                  <ExternalLink className="shrink-0 text-sea" size={16} aria-hidden="true" />
                </div>
                <p className="mt-2 text-sm leading-6 text-slate-700">{source.raw_text}</p>
              </a>
            ))}
          </div>
        </section>

        <div className="grid gap-5 lg:grid-cols-2">
          <section className="rounded-lg border border-line bg-panel p-4 shadow-soft">
            <h2 className="text-base font-semibold text-ink">Timeline</h2>
            <div className="mt-3 space-y-3">
              {incident.timeline.map((event) => (
                <article key={`${event.timestamp}-${event.label}`} className="rounded-md border border-line p-3">
                  <p className="text-sm font-semibold text-ink">{event.label}</p>
                  <p className="mt-1 text-xs text-muted">{new Date(event.timestamp).toLocaleString()}</p>
                  <p className="mt-2 text-sm leading-6 text-slate-700">{event.description}</p>
                </article>
              ))}
            </div>
          </section>

          <section className="rounded-lg border border-line bg-panel p-4 shadow-soft">
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-base font-semibold text-ink">Agent Runs</h2>
              <Bot className="text-sea" size={20} aria-hidden="true" />
            </div>
            {investigations.length === 0 ? (
              <p className="mt-3 text-sm text-muted">Run an investigation from the dashboard or generate a report to populate agent outputs.</p>
            ) : (
              <div className="mt-3 space-y-3">
                <InvestigationRuns number={investigations[0][0]} runs={investigations[0][1]} />
                {investigations.length > 1 && (
                  <details className="rounded-md border border-line p-3">
                    <summary className="cursor-pointer text-sm text-muted">
                      {`Earlier investigations (${investigations.length - 1})`}
                    </summary>
                    <div className="mt-3 space-y-4">
                      {investigations.slice(1).map(([number, group]) => (
                        <InvestigationRuns key={number} number={number} runs={group} />
                      ))}
                    </div>
                  </details>
                )}
              </div>
            )}
          </section>
        </div>

        <ReportPanel incidentId={incident.id} initialReports={reports} />
      </div>
    </main>
  );
}

