import type { Severity } from "@/lib/types";

const styles: Record<Severity, string> = {
  low: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  medium: "bg-amber-50 text-amber-700 ring-amber-200",
  high: "bg-orange-50 text-orange-700 ring-orange-200",
  critical: "bg-red-50 text-red-700 ring-red-200",
};

// `note` explains a severity capped by weak evidence; shown on hover, with a
// marker so the cap is visible without hovering.
export function SeverityBadge({ severity, note }: { severity: Severity; note?: string | null }) {
  return (
    <span
      className={`inline-flex items-center rounded px-2 py-1 text-xs font-semibold ring-1 ${styles[severity]}`}
      title={note ?? undefined}
    >
      {severity.toUpperCase()}
      {note ? <span aria-label="capped by evidence">&nbsp;&#8595;</span> : null}
    </span>
  );
}

