"use client";

import { FileText } from "lucide-react";
import { useState } from "react";
import { generateReport } from "@/lib/api";
import type { Report } from "@/lib/types";

export function ReportPanel({ incidentId, initialReports }: { incidentId: string; initialReports: Report[] }) {
  const [reports, setReports] = useState(initialReports);
  const [loading, setLoading] = useState(false);

  async function createReport() {
    setLoading(true);
    try {
      const response = await generateReport(incidentId);
      setReports([response.report, ...reports]);
    } finally {
      setLoading(false);
    }
  }

  const latest = reports[0];

  return (
    <section className="rounded-lg border border-line bg-panel p-4 shadow-soft">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="text-base font-semibold text-ink">Executive Report</h2>
          <p className="mt-1 text-sm text-muted">{latest ? `Latest report: ${new Date(latest.created_at).toLocaleString()}` : "No report generated yet."}</p>
        </div>
        <button
          type="button"
          onClick={createReport}
          className="inline-flex min-h-10 items-center justify-center gap-2 rounded-md bg-ink px-4 py-2 text-sm font-semibold text-white hover:bg-slate-700"
        >
          <FileText size={16} aria-hidden="true" />
          {loading ? "Generating" : "Generate Report"}
        </button>
      </div>
      {latest && (
        <pre className="mt-4 max-h-[34rem] overflow-auto whitespace-pre-wrap rounded-md bg-slate-50 p-4 text-sm leading-6 text-slate-800">
          {latest.content}
        </pre>
      )}
    </section>
  );
}

