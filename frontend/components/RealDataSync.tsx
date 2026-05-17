"use client";

import { RefreshCw } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { syncRealData } from "@/lib/api";

export function RealDataSync() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function syncFeeds() {
    setLoading(true);
    setError(null);
    try {
      const response = await syncRealData();
      setMessage(`Real feeds synced: ${response.created_count} new, ${response.skipped_count} existing.`);
      router.refresh();
    } catch {
      setError("Real feed sync failed. Check backend network access and try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col items-start gap-2 sm:items-end">
      <button
        type="button"
        onClick={syncFeeds}
        disabled={loading}
        className="inline-flex min-h-10 items-center justify-center gap-2 rounded-md bg-ink px-4 py-2 text-sm font-semibold text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-70"
      >
        <RefreshCw size={16} aria-hidden="true" className={loading ? "animate-spin" : ""} />
        {loading ? "Syncing" : "Sync real feeds"}
      </button>
      {(message || error) && (
        <p className={`max-w-xs text-sm ${error ? "text-red-700" : "text-muted"}`}>
          {error || message}
        </p>
      )}
    </div>
  );
}
