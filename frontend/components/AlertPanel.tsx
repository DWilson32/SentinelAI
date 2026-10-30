"use client";

import Link from "next/link";
import { BellRing, Check, CloudOff } from "lucide-react";
import { useState } from "react";
import { SeverityBadge } from "@/components/SeverityBadge";
import type { Alert, AlertsOverview } from "@/lib/types";

function age(timestamp: string): string {
  const minutes = Math.round((Date.now() - new Date(timestamp).getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

// What happened to the alert after it was raised. An undelivered alert is the
// case worth showing loudly: a silent delivery failure is worse than no alerting.
function Delivery({ alert, configured }: { alert: Alert; configured: boolean }) {
  if (alert.delivery_error) {
    return (
      <span className="inline-flex items-center gap-1 text-xs font-medium text-amber-700" title={alert.delivery_error}>
        <CloudOff size={13} aria-hidden="true" />
        Not delivered
      </span>
    );
  }
  if (alert.delivered_at) {
    return <span className="text-xs text-muted">Delivered</span>;
  }
  return <span className="text-xs text-muted">{configured ? "Queued" : "Not sent anywhere"}</span>;
}

export function AlertPanel({ initial }: { initial: AlertsOverview }) {
  const [alerts, setAlerts] = useState(initial.alerts);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const open = alerts.filter((alert) => !alert.acknowledged_at);

  async function acknowledge(id: string) {
    setBusy(id);
    setError(null);
    try {
      const response = await fetch(`/api/alerts/${id}/acknowledge`, { method: "POST" });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail ?? `Failed with status ${response.status}`);
      }
      const acknowledged: Alert = await response.json();
      setAlerts((current) => current.map((alert) => (alert.id === id ? acknowledged : alert)));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Could not acknowledge the alert");
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="rounded-lg border border-line bg-panel p-4 shadow-soft">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-ink">Alerts</h2>
          <p className="mt-1 text-sm text-muted">
            {open.length > 0
              ? `${open.length} awaiting acknowledgement`
              : alerts.length > 0
                ? "All acknowledged"
                : "No incident has crossed the alerting threshold"}
            {initial.delivery_configured ? " · delivered to the configured endpoint" : " · no delivery endpoint configured"}
          </p>
        </div>
        <BellRing className={open.length > 0 ? "text-amber-600" : "text-sea"} size={20} aria-hidden="true" />
      </div>

      {error && (
        <p className="mb-3 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</p>
      )}

      {alerts.length === 0 ? (
        <p className="rounded-md border border-line bg-slate-50 px-3 py-6 text-center text-sm text-muted">
          Alerts are raised when an incident is rated medium or above on corroborated evidence.
        </p>
      ) : (
        <ul className="space-y-2">
          {alerts.map((alert) => (
            <li
              key={alert.id}
              className={`rounded-md border p-3 ${
                alert.acknowledged_at ? "border-line bg-slate-50" : "border-amber-200 bg-amber-50/40"
              }`}
            >
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <SeverityBadge severity={alert.severity} />
                    <span className="text-xs text-muted">{age(alert.created_at)}</span>
                    <Delivery alert={alert} configured={initial.delivery_configured} />
                  </div>
                  <Link
                    href={`/incidents/${alert.incident_id}`}
                    className="mt-2 block text-sm font-semibold text-ink hover:text-sea"
                  >
                    {alert.incident_title}
                  </Link>
                  <p className="mt-1 text-sm leading-6 text-slate-700">{alert.reason}</p>
                </div>
                {alert.acknowledged_at ? (
                  <span className="inline-flex shrink-0 items-center gap-1 text-xs font-medium text-muted">
                    <Check size={14} aria-hidden="true" />
                    Acknowledged
                  </span>
                ) : (
                  <button
                    type="button"
                    onClick={() => acknowledge(alert.id)}
                    disabled={busy === alert.id}
                    className="inline-flex min-h-9 shrink-0 items-center gap-1 rounded-md border border-line bg-white px-3 text-xs font-semibold text-ink hover:bg-slate-50 disabled:opacity-60"
                  >
                    <Check size={14} aria-hidden="true" />
                    {busy === alert.id ? "Saving" : "Acknowledge"}
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
