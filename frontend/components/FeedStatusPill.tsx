import type { FeedsOverview } from "@/lib/types";

// Replaces a static "feeds ready" label that stayed green while ReliefWeb
// returned 403 for months and GDELT was being rate-limited. A native <details>
// element gives the per-feed breakdown with no client JavaScript.

const DOT: Record<string, string> = {
  healthy: "bg-emerald-500",
  degraded: "bg-amber-500",
  failing: "bg-red-500",
  stalled: "bg-red-500",
  unknown: "bg-slate-400",
};

function ago(iso: string | null, now: number): string {
  if (!iso) return "never";
  const minutes = Math.max(0, Math.round((now - new Date(iso).getTime()) / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

function headline(status: FeedsOverview, now: number): string {
  switch (status.overall) {
    case "unknown":
      return "Feeds: awaiting first sync";
    case "stalled":
      return `Sync stalled · last run ${ago(status.last_sync_at, now)}`;
    case "healthy":
      return `All ${status.total} feeds healthy · synced ${ago(status.last_sync_at, now)}`;
    default:
      return `${status.healthy} of ${status.total} feeds healthy · synced ${ago(status.last_sync_at, now)}`;
  }
}

export function FeedStatusPill({ status }: { status: FeedsOverview | null }) {
  if (!status) {
    return (
      <div className="flex items-center gap-2 rounded-md border border-line px-3 py-2 text-sm text-muted">
        <span className="h-2 w-2 rounded-full bg-slate-400" aria-hidden="true" />
        Feed status unavailable
      </div>
    );
  }

  // Rendered per request on the server, so this is the time the page was built.
  const now = Date.now();

  return (
    <details className="group relative">
      <summary className="flex cursor-pointer list-none items-center gap-2 rounded-md border border-line px-3 py-2 text-sm text-muted hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sea [&::-webkit-details-marker]:hidden">
        <span className={`h-2 w-2 shrink-0 rounded-full ${DOT[status.overall]}`} aria-hidden="true" />
        <span>{headline(status, now)}</span>
        <span className="text-xs transition-transform group-open:rotate-180" aria-hidden="true">
          ▾
        </span>
      </summary>

      <div className="absolute left-0 z-20 mt-2 w-80 max-w-[calc(100vw-2.5rem)] rounded-md border border-line bg-white p-3 text-sm shadow-lg sm:left-auto sm:right-0">
        <ul className="space-y-3">
          {status.feeds.map((feed) => (
            <li key={feed.feed}>
              <div className="flex items-center justify-between gap-3">
                <span className="flex items-center gap-2 font-medium text-ink">
                  <span className={`h-2 w-2 shrink-0 rounded-full ${DOT[feed.state]}`} aria-hidden="true" />
                  {feed.label}
                </span>
                <span className="text-xs text-muted">{feed.state}</span>
              </div>
              <p className="mt-0.5 pl-4 text-xs text-muted">
                {feed.state === "unknown"
                  ? "Not fetched yet"
                  : `Last success ${ago(feed.last_success_at, now)} · ${feed.last_item_count} items last run`}
              </p>
              {(feed.last_error || feed.note) && (
                <p className="mt-0.5 line-clamp-2 pl-4 text-xs text-muted" title={feed.last_error ?? feed.note ?? ""}>
                  {feed.last_error ?? feed.note}
                </p>
              )}
            </li>
          ))}
        </ul>
      </div>
    </details>
  );
}
