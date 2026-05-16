export type Severity = "low" | "medium" | "high" | "critical";

export type Incident = {
  id: string;
  title: string;
  category: string;
  location: string;
  latitude: number;
  longitude: number;
  severity: Severity;
  risk_score: number;
  status: string;
  summary: string;
  created_at: string;
  updated_at: string;
};

export type AnalyticsOverview = {
  active_incidents: number;
  critical_incidents: number;
  average_risk_score: number;
  categories: { category: string; count: number }[];
  severities: { severity: string; count: number }[];
  risk_trend: { label: string; average_risk: number }[];
};

export type ChatResponse = {
  answer: string;
  confidence: number;
  citations: { title: string; publisher: string; url: string }[];
  retrieved_incident_ids: string[];
};

export type AgentRun = {
  id: string;
  incident_id: string;
  agent_name: string;
  status: string;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  created_at: string;
};

