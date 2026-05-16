import type { Incident } from "@/lib/types";
import { SeverityBadge } from "@/components/SeverityBadge";

export function CrisisMap({ incidents }: { incidents: Incident[] }) {
  return (
    <section className="rounded-lg border border-line bg-panel p-4 shadow-soft">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-base font-semibold text-ink">Global Crisis Map</h2>
        <span className="text-sm text-muted">MVP projection</span>
      </div>
      <div className="map-grid relative mt-4 min-h-80 overflow-hidden rounded-md border border-line bg-cyan-50">
        {incidents.map((incident, index) => (
          <div
            key={incident.id}
            className="absolute w-56 rounded-md border border-line bg-white p-3 shadow-soft"
            style={{
              left: `${14 + index * 27}%`,
              top: `${18 + (index % 2) * 38}%`,
            }}
          >
            <SeverityBadge severity={incident.severity} />
            <p className="mt-2 text-sm font-semibold text-ink">{incident.category}</p>
            <p className="mt-1 text-xs text-muted">{incident.location}</p>
            <div className="mt-2 h-2 rounded bg-slate-100">
              <div className="h-2 rounded bg-signal" style={{ width: `${incident.risk_score}%` }} />
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

