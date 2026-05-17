from datetime import datetime

from pydantic import BaseModel


class Report(BaseModel):
    id: str
    incident_id: str
    report_type: str
    content: str
    created_at: datetime


class ReportCreateResponse(BaseModel):
    report: Report
    generated_from_agent_runs: bool

