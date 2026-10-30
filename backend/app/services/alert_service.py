"""Raising alerts, and getting them to a person.

Until now the system detected and displayed: an incident could be rated high,
corroborated by three independent sources, and sit on a dashboard nobody had
open. Detection without delivery is not monitoring.

An alert is raised when an incident is at or above `alert_min_severity`, once per
incident per severity band. The band matters rather than the event: an incident
that escalates from medium to high alerts again, because that is new
information, while the same incident being re-reported at the same severity does
not. Severity already passes through the credibility gate, so an alert cannot be
raised on a single unverified report.

Delivery is recorded on the alert rather than gating it. An alert nobody could
be told about is still an alert, and the dashboard shows it with its delivery
error attached, because a silent delivery failure is the thing that makes an
alerting system worse than none.
"""

import logging
from datetime import datetime, timezone
from uuid import uuid4

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.db.models import AlertModel, IncidentModel
from app.schemas.alert import Alert, AlertsOverview
from app.services.credibility import SEVERITY_ORDER

logger = logging.getLogger(__name__)


def _rank(severity: str) -> int:
    try:
        return SEVERITY_ORDER.index(severity)
    except ValueError:
        return -1


class AlertService:
    def raise_for(self, db: Session, incident_ids: list[str]) -> list[AlertModel]:
        """Alert on the incidents among these that have crossed into a new band.

        Returns the alerts raised, newest band first. Does not deliver them;
        deliver() does that, so a slow endpoint cannot hold up an ingest.
        """
        if not settings.alerts_enabled or not incident_ids:
            return []
        floor = _rank(settings.alert_min_severity)
        incidents = db.scalars(
            select(IncidentModel).where(IncidentModel.id.in_(incident_ids))
        ).all()

        raised: list[AlertModel] = []
        for incident in sorted(incidents, key=lambda item: -item.risk_score):
            if len(raised) >= settings.alert_max_per_sync:
                break
            if _rank(incident.severity) < floor:
                continue
            alerted = db.scalars(
                select(AlertModel.severity).where(AlertModel.incident_id == incident.id)
            ).all()
            if alerted and max(_rank(severity) for severity in alerted) >= _rank(incident.severity):
                continue
            raised.append(self._build(db, incident, escalated=bool(alerted)))

        if raised:
            db.commit()
        return raised

    def _build(self, db: Session, incident: IncidentModel, *, escalated: bool) -> AlertModel:
        evidence = incident.evidence or {}
        sources = evidence.get("independent_sources")
        corroboration = (
            f"{sources} independent source{'' if sources == 1 else 's'}" if sources else "a single report"
        )
        verb = "escalated to" if escalated else "rated"
        alert = AlertModel(
            id=f"alt-{uuid4().hex[:12]}",
            incident_id=incident.id,
            severity=incident.severity,
            reason=(
                f"{incident.category} in {incident.location} {verb} {incident.severity} "
                f"at risk {incident.risk_score:g}, on {corroboration}."
            ),
            risk_score=incident.risk_score,
            credibility=evidence.get("credibility"),
            independent_sources=sources,
            created_at=datetime.now(timezone.utc),
        )
        db.add(alert)
        return alert

    def deliver(self, db: Session, alerts: list[AlertModel]) -> int:
        """POST each alert to the configured endpoint; record what happened.

        Never raises: a failed delivery is recorded on the alert and the caller
        carries on, because an ingest must not fail over an unreachable webhook.
        """
        url = (settings.alert_webhook_url or "").strip()
        if not url or not alerts:
            return 0
        delivered = 0
        with httpx.Client(timeout=settings.alert_webhook_timeout_seconds) as client:
            for alert in alerts:
                try:
                    response = client.post(url, json=self._payload(db, alert))
                    response.raise_for_status()
                    alert.delivered_at = datetime.now(timezone.utc)
                    alert.delivery_error = None
                    delivered += 1
                except Exception as exc:  # noqa: BLE001 - recorded, never raised
                    alert.delivery_error = f"{type(exc).__name__}: {exc}"[:400]
                    logger.warning("Alert %s could not be delivered: %s", alert.id, exc)
        db.commit()
        return delivered

    def _payload(self, db: Session, alert: AlertModel) -> dict:
        incident = db.get(IncidentModel, alert.incident_id)
        return {
            # "text" and "content" are what chat webhooks read; the rest is for
            # anything that wants the structured alert.
            "text": f"[{alert.severity.upper()}] {incident.title if incident else alert.incident_id}",
            "content": f"[{alert.severity.upper()}] {incident.title if incident else alert.incident_id}\n{alert.reason}",
            "alert": {
                "id": alert.id,
                "severity": alert.severity,
                "reason": alert.reason,
                "risk_score": alert.risk_score,
                "credibility": alert.credibility,
                "independent_sources": alert.independent_sources,
                "created_at": alert.created_at.isoformat(),
            },
            "incident": {
                "id": alert.incident_id,
                "title": incident.title if incident else None,
                "category": incident.category if incident else None,
                "location": incident.location if incident else None,
            },
        }

    def overview(self, db: Session, *, limit: int = 50, unacknowledged_only: bool = False) -> AlertsOverview:
        query = select(AlertModel).options(joinedload(AlertModel.incident))
        if unacknowledged_only:
            query = query.where(AlertModel.acknowledged_at.is_(None))
        rows = db.scalars(
            query.order_by(AlertModel.created_at.desc(), AlertModel.id.desc()).limit(limit)
        ).all()
        return AlertsOverview(
            unacknowledged=self.unacknowledged_count(db),
            total=db.scalar(select(func.count()).select_from(AlertModel)) or 0,
            delivery_configured=bool((settings.alert_webhook_url or "").strip()),
            alerts=[self._to_schema(row) for row in rows],
        )

    def unacknowledged_count(self, db: Session) -> int:
        return (
            db.scalar(
                select(func.count()).select_from(AlertModel).where(AlertModel.acknowledged_at.is_(None))
            )
            or 0
        )

    def acknowledge(self, db: Session, alert_id: str) -> Alert | None:
        alert = db.get(AlertModel, alert_id)
        if alert is None:
            return None
        if alert.acknowledged_at is None:
            alert.acknowledged_at = datetime.now(timezone.utc)
            db.commit()
        return self._to_schema(alert)

    def _to_schema(self, alert: AlertModel) -> Alert:
        incident = alert.incident
        return Alert(
            id=alert.id,
            incident_id=alert.incident_id,
            incident_title=incident.title if incident else alert.incident_id,
            incident_location=incident.location if incident else "Unknown",
            severity=alert.severity,
            reason=alert.reason,
            risk_score=alert.risk_score,
            credibility=alert.credibility,
            independent_sources=alert.independent_sources,
            created_at=alert.created_at,
            acknowledged_at=alert.acknowledged_at,
            delivered_at=alert.delivered_at,
            delivery_error=alert.delivery_error,
        )


alert_service = AlertService()
