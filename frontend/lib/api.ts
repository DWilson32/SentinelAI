import type { AgentRun, AnalyticsOverview, ChatResponse, Incident, IncidentDetail, IngestResponse, Report, ReportCreateResponse } from "@/lib/types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
    cache: "no-store",
  });

  if (!response.ok) {
    throw new Error(`API request failed: ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export function getIncidents() {
  return request<Incident[]>("/incidents");
}

export function getIncident(incidentId: string) {
  return request<IncidentDetail>(`/incidents/${incidentId}`);
}

export function getAnalytics() {
  return request<AnalyticsOverview>("/analytics/overview");
}

export function askSentinel(query: string) {
  return request<ChatResponse>("/chat", {
    method: "POST",
    body: JSON.stringify({ query }),
  });
}

// Routed through the same-origin proxy at app/api/sync so the admin key stays
// on the server. Calling the backend directly from the browser would require
// shipping the key to the client.
export async function syncRealData() {
  const response = await fetch("/api/sync", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    cache: "no-store",
  });

  if (!response.ok) {
    throw new Error(`Sync failed: ${response.status}`);
  }

  return response.json() as Promise<IngestResponse>;
}

export function investigateIncident(incidentId: string) {
  return request<AgentRun[]>(`/agents/investigate/${incidentId}`, {
    method: "POST",
  });
}

export function getAgentRuns(incidentId: string) {
  return request<AgentRun[]>(`/agents/runs/${incidentId}`);
}

export function getReports(incidentId: string) {
  return request<Report[]>(`/reports/${incidentId}`);
}

export function generateReport(incidentId: string) {
  return request<ReportCreateResponse>(`/reports/${incidentId}`, {
    method: "POST",
  });
}
