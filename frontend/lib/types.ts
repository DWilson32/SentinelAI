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

export type Source = {
  id: string;
  title: string;
  url: string;
  publisher: string;
  credibility_score: number;
  published_at: string;
  raw_text: string;
};

export type TimelineEvent = {
  timestamp: string;
  label: string;
  description: string;
};

export type IncidentDetail = Incident & {
  sources: Source[];
  timeline: TimelineEvent[];
  recommended_actions: string[];
  risk_explanation: {
    confidence: number;
    drivers: string[];
    feature_importance: Record<string, number>;
  };
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

export type IngestResponse = {
  provider: "manual" | "mock" | "public" | "gnews" | "newsapi";
  created_count: number;
  skipped_count: number;
  incidents: {
    incident_id: string;
    title: string;
    category: string;
    severity: string;
    risk_score: number;
    source_url: string;
    created: boolean;
  }[];
  message: string;
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

export type Report = {
  id: string;
  incident_id: string;
  report_type: string;
  content: string;
  created_at: string;
};

export type ReportCreateResponse = {
  report: Report;
  generated_from_agent_runs: boolean;
};
