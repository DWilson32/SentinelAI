from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AgentRunModel, ReportModel
from app.schemas.report import Report, ReportCreateResponse
from app.services.agent_service import agent_service
from app.services.incident_service import incident_service


class ReportService:
    def list_reports(self, db: Session, incident_id: str) -> list[Report]:
        reports = db.scalars(
            select(ReportModel).where(ReportModel.incident_id == incident_id).order_by(ReportModel.created_at.desc())
        ).all()
        return [self._to_schema(report) for report in reports]

    def generate_report(self, db: Session, incident_id: str) -> ReportCreateResponse | None:
        incident = incident_service.get_incident(db, incident_id)
        if incident is None:
            return None

        runs = db.scalars(
            select(AgentRunModel).where(AgentRunModel.incident_id == incident_id).order_by(AgentRunModel.created_at)
        ).all()
        generated_from_agent_runs = bool(runs)
        if not runs:
            agent_service.investigate(db, incident_id)
            runs = db.scalars(
                select(AgentRunModel).where(AgentRunModel.incident_id == incident_id).order_by(AgentRunModel.created_at)
            ).all()

        content = self._compose_markdown_report(incident, runs)
        report = ReportModel(
            id=f"rpt-{uuid4()}",
            incident_id=incident_id,
            report_type="executive_markdown",
            content=content,
            created_at=datetime.now(timezone.utc),
        )
        db.add(report)
        db.commit()
        db.refresh(report)
        return ReportCreateResponse(report=self._to_schema(report), generated_from_agent_runs=generated_from_agent_runs)

    def _compose_markdown_report(self, incident, runs: list[AgentRunModel]) -> str:
        run_outputs = {run.agent_name: run.output for run in runs}
        research = run_outputs.get("Research Agent", {})
        verification = run_outputs.get("Verification Agent", {})
        prediction = run_outputs.get("Prediction Agent", {})
        strategy = run_outputs.get("Strategy Agent", {})
        report = run_outputs.get("Report Agent", {})
        actions = strategy.get("recommended_actions") or incident.recommended_actions

        sources = "\n".join(
            f"- [{source.title}]({source.url}) - {source.publisher}, credibility {source.credibility_score:.2f}"
            for source in incident.sources
        )
        timeline = "\n".join(
            f"- {event.timestamp.isoformat()} - {event.label}: {event.description}"
            for event in incident.timeline
        )
        action_lines = "\n".join(f"- {action}" for action in actions)
        drivers = "\n".join(f"- {driver}" for driver in incident.risk_explanation.drivers)

        return (
            f"# {incident.title}\n\n"
            f"**Location:** {incident.location}\n"
            f"**Category:** {incident.category}\n"
            f"**Severity:** {incident.severity}\n"
            f"**Risk Score:** {incident.risk_score:.0f}/100\n"
            f"**Confidence:** {incident.risk_explanation.confidence:.0%}\n\n"
            f"## Executive Brief\n\n"
            f"{report.get('brief') or incident.summary}\n\n"
            f"## Research Assessment\n\n"
            f"{research.get('finding') or incident.summary}\n\n"
            f"## Verification\n\n"
            f"{verification.get('finding') or 'No verification run is available.'}\n\n"
            f"## Prediction\n\n"
            f"{prediction.get('finding') or 'No prediction run is available.'}\n\n"
            f"## Risk Drivers\n\n"
            f"{drivers or '- No drivers available.'}\n\n"
            f"## Recommended Actions\n\n"
            f"{action_lines or '- No actions available.'}\n\n"
            f"## Timeline\n\n"
            f"{timeline or '- No timeline events available.'}\n\n"
            f"## Sources\n\n"
            f"{sources or '- No sources available.'}\n"
        )

    def _to_schema(self, report: ReportModel) -> Report:
        return Report(
            id=report.id,
            incident_id=report.incident_id,
            report_type=report.report_type,
            content=report.content,
            created_at=report.created_at,
        )


report_service = ReportService()

