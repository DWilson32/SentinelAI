from datetime import datetime, timezone
import logging
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import AgentRunModel, IncidentModel, ReportModel
from app.schemas.agent import AgentRun
from app.agents.llm import metered
from app.services.credibility import verification_summary
from app.services.embedding_service import embedding_service
from app.services.incident_service import incident_service
from app.services.vector_store import vector_store

logger = logging.getLogger(__name__)

# Investigations kept per incident. Re-running one used to delete the previous
# run outright, losing the audit trail the agents exist to provide. Keeping every
# one would let anyone grow the tables without limit through the public
# endpoints, so beyond this many the oldest are dropped. Reports are kept to the
# same number.
KEEP_INVESTIGATIONS = 20


class AgentService:
    def investigate(self, db: Session, incident_id: str) -> list[AgentRun]:
        incident = incident_service.get_incident(db, incident_id)
        if incident is None:
            return []

        rag_context = self._build_rag_context(db, incident)
        # Counts the model calls and tokens this investigation costs.
        with metered() as meter:
            try:
                from app.agents.investigation_graph import run_investigation

                steps = run_investigation(incident, rag_context)
            except Exception as exc:
                logger.exception("LangGraph investigation failed; using fallback workflow: %s", exc)
                steps = self._fallback_steps(incident)

        # Steps of one investigation share a timestamp and a number; earlier
        # investigations stay as history. Runs from before numbering count as 1.
        previous = db.scalars(select(AgentRunModel.input).where(AgentRunModel.incident_id == incident_id)).all()
        number = max((run_input.get("investigation", 1) for run_input in previous), default=0) + 1
        created_at = datetime.now(timezone.utc)
        run_input = {
            "incident_id": incident_id,
            "title": incident.title,
            "workflow": "langgraph",
            "investigation": number,
            "llm": meter.as_dict(),
        }
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

        db.flush()
        prune_history(db, incident_id)
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
                "output": verification_summary(
                    incident.evidence.model_dump() if incident.evidence else None, source_count
                ),
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

    def _build_rag_context(self, db: Session, incident) -> str:
        """Related incidents, not this one.

        The agents already receive this incident's own sources directly, so
        retrieving them again adds nothing. What retrieval can contribute is
        similar events elsewhere — earlier floods in the region, prior quakes on
        the same fault.
        """
        if not vector_store.available:
            return ""
        try:
            if vector_store.count(db) == 0:
                return ""
            query = f"{incident.title} {incident.category} {incident.location} {incident.summary}"
            hits = vector_store.search(
                db,
                embedding_service.embed([query])[0],
                limit=4,
                exclude_incident_id=incident.id,
                min_similarity=settings.rag_min_similarity,
            )
        except Exception as exc:
            logger.warning("RAG context lookup failed; investigating without it: %s", exc)
            db.rollback()
            return ""
        return "\n".join(
            f"- {hit.incident_title} ({hit.location}, {hit.severity}): {hit.content[:220]}"
            for hit in hits
        )

    def _to_schema(self, run: AgentRunModel) -> AgentRun:
        return AgentRun(
            id=run.id,
            incident_id=run.incident_id,
            agent_name=run.agent_name,
            status=run.status,
            input=run.input,
            output=run.output,
            created_at=run.created_at,
            investigation=(run.input or {}).get("investigation", 1),
        )


def prune_history(db: Session, incident_id: str) -> None:
    """Drop investigations and reports beyond the newest KEEP_INVESTIGATIONS."""
    for model in (AgentRunModel, ReportModel):
        stamps = db.scalars(
            select(model.created_at)
            .where(model.incident_id == incident_id)
            .distinct()
            .order_by(model.created_at.desc())
        ).all()
        if len(stamps) > KEEP_INVESTIGATIONS:
            oldest_kept = stamps[KEEP_INVESTIGATIONS - 1]
            db.execute(
                delete(model)
                .where(model.incident_id == incident_id, model.created_at < oldest_kept)
                .execution_options(synchronize_session=False)
            )


agent_service = AgentService()
