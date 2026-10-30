"""The work that follows an ingest, as its own operation.

Storing a sync's reports is quick. Investigating what it turned up and
delivering alerts is not: three investigations are model calls, and delivery is
an HTTP round trip to someone else's endpoint. Both ran inside the sync request,
which made a request that only needed to store reports wait on all of it.

They are now one call, triggered after the sync rather than inside it, and they
work from derived state rather than a queue: what needs investigating is "active,
severe, no investigation on file", and what needs alerting is the same question
asked of the alerts table. That means the call is idempotent and safe to repeat,
nothing is lost if it fails, and the sync no longer depends on it.

It is also the seam a separate worker would take: the follow-up already has its
own entry point and its own trigger, so moving it off this process is a
deployment change rather than a code change.
"""

import logging

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.services.agent_service import agent_service
from app.services.alert_service import alert_service
from app.services.incident_service import incident_service

logger = logging.getLogger(__name__)


class FollowUp(BaseModel):
    considered: int
    investigated: int
    alerted: int
    delivered: int
    delivery_configured: bool
    message: str


class MonitorService:
    def follow_up(self, db: Session) -> FollowUp:
        """Investigate and alert on the incidents that warrant it. Idempotent."""
        active = incident_service.active_ids(db)

        investigated: list[str] = []
        try:
            investigated = agent_service.investigate_priorities(db, active)
        except Exception as exc:  # noqa: BLE001 - alerting must still run
            logger.warning("Follow-up investigation failed: %s", exc)
            db.rollback()

        alerted = delivered = 0
        try:
            raised = alert_service.raise_for(db, active)
            alerted = len(raised)
            delivered = alert_service.deliver(db, raised)
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            logger.warning("Follow-up alerting failed: %s", exc)
            db.rollback()

        overview_configured = alert_service.overview(db, limit=1).delivery_configured
        return FollowUp(
            considered=len(active),
            investigated=len(investigated),
            alerted=alerted,
            delivered=delivered,
            delivery_configured=overview_configured,
            message=(
                f"Considered {len(active)} active incident(s); investigated {len(investigated)}; "
                f"raised {alerted} alert(s), delivered {delivered}."
            ),
        )


monitor_service = MonitorService()
