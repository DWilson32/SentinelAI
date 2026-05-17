import Link from "next/link";
import { ArrowLeft, Bot, ExternalLink, ShieldCheck } from "lucide-react";
import { ReportPanel } from "@/components/ReportPanel";
import { SeverityBadge } from "@/components/SeverityBadge";
import { getAgentRuns, getIncident, getReports } from "@/lib/api";

export default async function IncidentDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [incident, runs, reports] = await Promise.all([getIncident(id), getAgentRuns(id), getReports(id)]);

  const featureEntries = Object.entries(incident.risk_explanation.feature_importance).sort((a, b) => b[1] - a[1]);

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
                <SeverityBadge severity={incident.severity} />
                <span className="rounded bg-slate-100 px-2 py-1 text-xs font-semibold text-muted">{incident.category}</span>
                <span className="rounded bg-slate-100 px-2 py-1 text-xs font-semibold text-muted">{incident.status}</span>
              </div>
              <h1 className="mt-3 max-w-4xl text-3xl font-bold text-ink">{incident.title}</h1>
              <p className="mt-2 text-sm text-muted">{incident.location}</p>
            </div>
            <div className="rounded-lg border border-line bg-panel p-4 shadow-soft lg:min-w-56">
              <p className="text-sm text-muted">Risk Score</p>
              <p className="mt-1 text-4xl font-bold text-ink">{incident.risk_score}</p>
              <p className="mt-2 text-sm text-muted">Confidence {(incident.risk_explanation.confidence * 100).toFixed(0)}%</p>
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
          <h2 className="text-base font-semibold text-ink">Sources</h2>
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
            {runs.length === 0 ? (
              <p className="mt-3 text-sm text-muted">Run an investigation from the dashboard or generate a report to populate agent outputs.</p>
            ) : (
              <div className="mt-3 space-y-3">
                {runs.map((run) => (
                  <article key={run.id} className="rounded-md border border-line p-3">
                    <p className="text-sm font-semibold text-ink">{run.agent_name}</p>
                    <pre className="mt-2 whitespace-pre-wrap break-words rounded bg-slate-50 p-2 text-xs leading-5 text-slate-700">
                      {JSON.stringify(run.output, null, 2)}
                    </pre>
                  </article>
                ))}
              </div>
            )}
          </section>
        </div>

        <ReportPanel incidentId={incident.id} initialReports={reports} />
      </div>
    </main>
  );
}

