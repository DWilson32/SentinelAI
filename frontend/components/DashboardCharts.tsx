"use client";

import { Area, AreaChart, Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { AnalyticsOverview } from "@/lib/types";

export function DashboardCharts({ analytics }: { analytics: AnalyticsOverview }) {
  // The trend is built only from recorded snapshots, so early on there may be a
  // single point. Showing a one-point "line" would imply history that is not
  // there yet, so the chart is withheld until there are at least two readings.
  const trendPoints = analytics.risk_trend.length;

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <section className="rounded-lg border border-line bg-panel p-4 shadow-soft">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-base font-semibold text-ink">Risk Trend</h2>
          <span className="text-sm text-muted">
            {trendPoints > 1 ? "Recorded over the last 24h" : "Collecting history"}
          </span>
        </div>
        <div className="mt-4 h-64">
          {trendPoints > 1 ? (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={analytics.risk_trend}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="label" tickLine={false} axisLine={false} />
                <YAxis tickLine={false} axisLine={false} domain={[0, 100]} />
                <Tooltip />
                <Area type="monotone" dataKey="average_risk" stroke="#0891b2" fill="#cffafe" />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <div className="flex h-full flex-col items-center justify-center gap-2 rounded-md border border-dashed border-line px-6 text-center">
              <p className="text-3xl font-bold text-ink">
                {analytics.risk_trend[0]?.average_risk ?? analytics.average_risk_score}
              </p>
              <p className="max-w-xs text-sm text-muted">
                Only one reading recorded so far. The trend line appears once a second snapshot is
                taken on the next feed sync.
              </p>
            </div>
          )}
        </div>
      </section>

      <section className="rounded-lg border border-line bg-panel p-4 shadow-soft">
        <h2 className="text-base font-semibold text-ink">Severity Distribution</h2>
        <div className="mt-4 h-64">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={analytics.severities}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis dataKey="severity" tickLine={false} axisLine={false} />
              <YAxis allowDecimals={false} tickLine={false} axisLine={false} />
              <Tooltip />
              <Bar dataKey="count" fill="#ef4444" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </section>
    </div>
  );
}

