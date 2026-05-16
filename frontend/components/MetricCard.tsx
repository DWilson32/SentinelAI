import type { LucideIcon } from "lucide-react";

type MetricCardProps = {
  label: string;
  value: string | number;
  detail: string;
  icon: LucideIcon;
};

export function MetricCard({ label, value, detail, icon: Icon }: MetricCardProps) {
  return (
    <section className="rounded-lg border border-line bg-panel p-4 shadow-soft">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-sm text-muted">{label}</p>
          <p className="mt-2 text-3xl font-bold tracking-normal text-ink">{value}</p>
        </div>
        <div className="flex size-10 items-center justify-center rounded-md bg-slate-100 text-sea">
          <Icon size={20} aria-hidden="true" />
        </div>
      </div>
      <p className="mt-3 text-sm text-muted">{detail}</p>
    </section>
  );
}

