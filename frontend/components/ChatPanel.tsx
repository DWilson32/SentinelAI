"use client";

import { Send, Sparkles } from "lucide-react";
import { FormEvent, useState } from "react";
import { askSentinel } from "@/lib/api";
import type { ChatResponse } from "@/lib/types";

export function ChatPanel() {
  const [query, setQuery] = useState("What is the highest risk incident right now?");
  const [response, setResponse] = useState<ChatResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!query.trim()) {
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const result = await askSentinel(query);
      setResponse(result);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "RAG chat failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="rounded-lg border border-line bg-panel p-4 shadow-soft">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-ink">RAG Intelligence Chat</h2>
          <p className="mt-1 text-sm text-muted">Semantic search over indexed sources with citations.</p>
        </div>
        <Sparkles className="text-sea" size={20} aria-hidden="true" />
      </div>
      <form onSubmit={submit} className="flex flex-col gap-3 sm:flex-row">
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className="min-h-11 flex-1 rounded-md border border-line bg-white px-3 py-2 text-sm outline-none focus:border-sea"
          placeholder="Ask SentinelAI..."
        />
        <button
          type="submit"
          disabled={loading}
          className="inline-flex min-h-11 items-center justify-center gap-2 rounded-md bg-sea px-4 py-2 text-sm font-semibold text-white hover:bg-cyan-700"
        >
          <Send size={16} aria-hidden="true" />
          {loading ? "Thinking" : "Ask"}
        </button>
      </form>
      {error && (
        <p className="mt-3 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {error}
        </p>
      )}
      {response && (
        <div className="mt-4 rounded-md border border-line bg-slate-50 p-4">
          <p className="text-sm leading-6 text-slate-800">{response.answer}</p>
          <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted">
            <span>Confidence {(response.confidence * 100).toFixed(0)}%</span>
            {response.citations.map((citation) => (
              <a key={citation.url} href={citation.url} className="rounded bg-white px-2 py-1 text-sea ring-1 ring-line">
                {citation.publisher}
              </a>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
