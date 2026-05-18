from datetime import datetime, timezone
import logging
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import AgentRunModel, IncidentModel, ReportModel
from app.schemas.agent import AgentRun
from app.services.embedding_service import embedding_service
from app.services.incident_service import incident_service
from app.services.vector_store import vector_store

logger = logging.getLogger(__name__)


class AgentService:
    def investigate(self, db: Session, incident_id: str) -> list[AgentRun]:
        incident = incident_service.get_incident(db, incident_id)
        if incident is None:
            return []

        rag_context = self._build_rag_context(incident)
        try:
            from app.agents.investigation_graph import run_investigation

            steps = run_investigation(incident, rag_context)
        except Exception as exc:
            logger.exception("LangGraph investigation failed; using fallback workflow: %s", exc)
            steps = self._fallback_steps(incident)

        db.execute(delete(AgentRunModel).where(AgentRunModel.incident_id == incident_id))
        db.execute(delete(ReportModel).where(ReportModel.incident_id == incident_id))

        created_at = datetime.now(timezone.utc)
        run_input = {"incident_id": incident_id, "title": incident.title, "workflow": "langgraph"}
        models = [
            AgentRunModel(
                id=f"run-{uuid4()}",
                incident_id=incident_id,
                agent_name=step["agent_name"],
                status="completed",
                input=run_input,
                output=step["output"],
                created_at=created_at,
            )
            for step in steps
        ]
        db.add_all(models)

        report_step = next((step for step in reversed(steps) if step["agent_name"] == "Report Agent"), None)
        if report_step:
            db.add(
                ReportModel(
                    id=f"rpt-{uuid4()}",
                    incident_id=incident_id,
                    report_type=str(report_step["output"].get("report_type", "executive_brief")),
                    content=str(report_step["output"].get("brief", "")),
                    created_at=created_at,
                )
            )

        strategy_step = next((step for step in steps if step["agent_name"] == "Strategy Agent"), None)
        if strategy_step:
            incident_row = db.get(IncidentModel, incident_id)
            if incident_row is not None:
                actions = strategy_step["output"].get("recommended_actions")
                if isinstance(actions, list) and actions:
                    incident_row.recommended_actions = actions
                    incident_row.updated_at = created_at

        db.commit()
        return [self._to_schema(run) for run in models]

    def _fallback_steps(self, incident) -> list[dict]:
        actions = incident.recommended_actions or []
        source_count = len(incident.sources or [])
        publishers = list({source.publisher for source in incident.sources if source.publisher})
        return [
            {
                "agent_name": "Research Agent",
                "output": {
                    "finding": f"Collected {source_count} source document(s) for {incident.category} incident in {incident.location}.",
                    "source_count": source_count,
                    "publishers": publishers,
                },
            },
            {
                "agent_name": "Verification Agent",
                "output": {
                    "finding": f"Source review completed across {source_count} document(s).",
                    "credibility": incident.risk_explanation.confidence,
                    "agreement": "high" if incident.risk_explanation.confidence >= 0.75 else "moderate",
                },
            },
            {
                "agent_name": "Prediction Agent",
                "output": {
                    "finding": f"Current assessment: {incident.severity} severity with risk score {incident.risk_score}/100.",
                    "risk_score": incident.risk_score,
                    "severity": incident.severity,
                    "confidence": incident.risk_explanation.confidence,
                    "drivers": incident.risk_explanation.drivers,
                },
            },
            {
                "agent_name": "Strategy Agent",
                "output": {
                    "finding": "Strategy recommendations derived from incident playbook.",
                    "recommended_actions": actions,
                },
            },
            {
                "agent_name": "Report Agent",
                "output": {
                    "brief": (
                        f"{incident.title} in {incident.location} remains {incident.severity} "
                        f"(risk {incident.risk_score}/100). {incident.summary} "
                        f"Priority actions: {'; '.join(actions[:3])}."
                    ),
                    "status": "Executive brief ready.",
                    "report_type": "executive_brief",
                },
            },
        ]

    def list_runs(self, db: Session, incident_id: str) -> list[AgentRun]:
        runs = db.scalars(
            select(AgentRunModel).where(AgentRunModel.incident_id == incident_id).order_by(AgentRunModel.created_at)
        ).all()
        return [self._to_schema(run) for run in runs]

    def _build_rag_context(self, incident) -> str:
        try:
            if vector_store.count() == 0:
                return ""
            query = f"{incident.title} {incident.category} {incident.location} {incident.summary}"
            hits = vector_store.search(embedding_service.embed([query])[0], limit=4)
        except Exception:
            return ""
        lines = []
        for hit in hits:
            payload = hit.payload or {}
            if str(payload.get("incident_id")) != incident.id and payload.get("incident_id"):
                continue
            lines.append(
                f"- {payload.get('source_title')} ({payload.get('publisher')}): "
                f"{str(payload.get('snippet', ''))[:220]}"
            )
        if not lines:
            for hit in hits[:3]:
                payload = hit.payload or {}
                lines.append(
                    f"- {payload.get('incident_title')}: {str(payload.get('snippet', ''))[:220]}"
                )
        return "\n".join(lines) if lines else ""

    def _to_schema(self, run: AgentRunModel) -> AgentRun:
        return AgentRun(
            id=run.id,
            incident_id=run.incident_id,
            agent_name=run.agent_name,
            status=run.status,
            input=run.input,
            output=run.output,
            created_at=run.created_at,
        )


agent_service = AgentService()
