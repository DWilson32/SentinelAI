"use client";

import { Activity, Bot } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { investigateIncident } from "@/lib/api";
import type { AgentRun, Incident } from "@/lib/types";
import { SeverityBadge } from "@/components/SeverityBadge";

export function IncidentList({ incidents }: { incidents: Incident[] }) {
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [loadingId, setLoadingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeIncidentId, setActiveIncidentId] = useState<string | null>(null);
  const activeIncident = incidents.find((incident) => incident.id === activeIncidentId);

  async function runInvestigation(incidentId: string) {
    setLoadingId(incidentId);
    setActiveIncidentId(incidentId);
    setError(null);
    try {
      const result = await investigateIncident(incidentId);
      setRuns(result);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Investigation failed");
    } finally {
      setLoadingId(null);
    }
  }

  return (
    <div className="grid gap-4 xl:grid-cols-[1.15fr_0.85fr]">
      {activeIncidentId && (
        <div className="fixed bottom-4 right-4 z-50 max-w-sm rounded-md border border-line bg-white p-4 text-sm shadow-lg">
          <p className="font-semibold text-ink">{activeIncident?.title ?? "Investigation"}</p>
          {loadingId === activeIncidentId && <p className="mt-1 text-muted">Investigation running...</p>}
          {error && <p className="mt-1 text-red-700">{error}</p>}
          {!loadingId && !error && runs.length > 0 && (
            <p className="mt-1 text-emerald-700">Investigation complete: {runs.length} agent steps generated.</p>
          )}
        </div>
      )}
      <section className="rounded-lg border border-line bg-panel p-4 shadow-soft">
        <div className="mb-4 flex items-center justify-between gap-3">
          <h2 className="text-base font-semibold text-ink">Active Incidents</h2>
          <Activity className="text-sea" size={20} aria-hidden="true" />
        </div>
        <div className="space-y-3">
          {incidents.map((incident) => (
            <article key={incident.id} className="rounded-md border border-line p-4">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <SeverityBadge severity={incident.severity} />
                    <span className="text-xs font-medium text-muted">{incident.category}</span>
                  </div>
                  <h3 className="mt-2 text-base font-semibold text-ink">{incident.title}</h3>
                  <p className="mt-1 text-sm text-muted">{incident.location}</p>
                  <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-700">{incident.summary}</p>
                </div>
                <div className="flex min-w-28 flex-col items-start gap-2 sm:items-end">
                  <span className="text-2xl font-bold text-ink">{incident.risk_score}</span>
                  <Link
                    href={`/incidents/${incident.id}`}
                    className="inline-flex items-center gap-2 rounded-md border border-line px-3 py-2 text-sm font-semibold text-ink hover:bg-slate-50"
                  >
                    Details
                  </Link>
                  <button
                    type="button"
                    onClick={() => runInvestigation(incident.id)}
                    disabled={loadingId === incident.id}
                    className="inline-flex items-center gap-2 rounded-md bg-ink px-3 py-2 text-sm font-semibold text-white hover:bg-slate-700"
                  >
                    <Bot size={16} aria-hidden="true" />
                    {loadingId === incident.id ? "Running" : "Investigate"}
                  </button>
                </div>
              </div>
              {activeIncidentId === incident.id && (
                <div className="mt-3 rounded-md border border-line bg-slate-50 p-3 text-sm">
                  {loadingId === incident.id && <p className="font-semibold text-ink">Investigation running...</p>}
                  {error && <p className="font-semibold text-red-700">{error}</p>}
                  {!loadingId && !error && runs.length > 0 && (
                    <p className="font-semibold text-emerald-700">
                      Investigation complete: {runs.length} agent steps generated. See Agent Activity below.
                    </p>
                  )}
                </div>
              )}
            </article>
          ))}
        </div>
      </section>

      <section className="rounded-lg border border-line bg-panel p-4 shadow-soft">
        <div className="mb-4 flex items-center justify-between gap-3">
          <h2 className="text-base font-semibold text-ink">Agent Activity</h2>
          <Bot className="text-sea" size={20} aria-hidden="true" />
        </div>
        {runs.length === 0 ? (
          <div className="space-y-3">
            <p className="text-sm leading-6 text-muted">
              Run a LangGraph investigation workflow: research, verification, prediction, strategy, and executive report.
            </p>
            {error && <p className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</p>}
          </div>
        ) : (
          <div className="space-y-3">
            {error && <p className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</p>}
            {runs.map((run) => (
              <article key={run.id} className="rounded-md border border-line p-3">
                <div className="flex items-center justify-between gap-2">
                  <h3 className="text-sm font-semibold text-ink">{run.agent_name}</h3>
                  <span className="rounded bg-emerald-50 px-2 py-1 text-xs font-semibold text-emerald-700">{run.status}</span>
                </div>
                <pre className="mt-2 whitespace-pre-wrap break-words rounded bg-slate-50 p-2 text-xs leading-5 text-slate-700">
                  {JSON.stringify(run.output, null, 2)}
                </pre>
              </article>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
