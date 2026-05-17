import { AlertTriangle, Gauge, RadioTower } from "lucide-react";
import { ChatPanel } from "@/components/ChatPanel";
import { CrisisMap } from "@/components/CrisisMap";
import { DashboardCharts } from "@/components/DashboardCharts";
import { IncidentList } from "@/components/IncidentList";
import { MetricCard } from "@/components/MetricCard";
import { RealDataSync } from "@/components/RealDataSync";
import { getAnalytics, getIncidents } from "@/lib/api";

export default async function Home() {
  const [incidents, analytics] = await Promise.all([getIncidents(), getAnalytics()]);

  return (
    <main className="min-h-screen">
      <header className="border-b border-line bg-white">
        <div className="mx-auto flex max-w-7xl flex-col gap-4 px-5 py-5 md:flex-row md:items-center md:justify-between">
          <div>
            <p className="text-sm font-semibold uppercase tracking-normal text-sea">SentinelAI</p>
            <h1 className="mt-1 text-2xl font-bold text-ink md:text-3xl">Autonomous Crisis Intelligence</h1>
          </div>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <div className="flex items-center gap-2 rounded-md border border-line px-3 py-2 text-sm text-muted">
              <RadioTower size={16} aria-hidden="true" />
              Public feeds ready
            </div>
            <RealDataSync />
          </div>
        </div>
      </header>

      <div className="mx-auto grid max-w-7xl gap-5 px-5 py-6">
        <section className="grid gap-4 md:grid-cols-3">
          <MetricCard label="Active Incidents" value={analytics.active_incidents} detail="Tracked across public and local feeds" icon={RadioTower} />
          <MetricCard label="Critical Alerts" value={analytics.critical_incidents} detail="Require immediate investigation" icon={AlertTriangle} />
          <MetricCard label="Average Risk" value={analytics.average_risk_score} detail="Composite score across live incidents" icon={Gauge} />
        </section>

        <CrisisMap incidents={incidents} />
        <DashboardCharts analytics={analytics} />
        <ChatPanel />
        <IncidentList incidents={incidents} />
      </div>
    </main>
  );
}
