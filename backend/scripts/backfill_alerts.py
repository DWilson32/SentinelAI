"""Alert on incidents that already crossed the threshold before alerting existed.

A sync only alerts on the incidents it touched, so incidents stored earlier never
get an alert however severe they are. This runs the same rule over every
incident once. Thereafter the sync keeps up on its own.

Runs against DATABASE_URL. A dry run by default, read-only on Postgres; --apply
raises the alerts and, with a webhook configured, delivers them.

    python scripts/backfill_alerts.py
    python scripts/backfill_alerts.py --apply
    python scripts/backfill_alerts.py --apply --investigate
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, text  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.database import SessionLocal, is_postgres, migrate_db_tables  # noqa: E402
from app.db.models import IncidentModel  # noqa: E402
from app.services.agent_service import agent_service  # noqa: E402
from app.services.alert_service import alert_service  # noqa: E402
from app.services.credibility import SEVERITY_ORDER  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="raise the alerts, rather than only report")
    parser.add_argument(
        "--investigate",
        action="store_true",
        help="also investigate the alerted incidents that never have been (spends model calls)",
    )
    parser.add_argument("--limit", type=int, default=50, help="most alerts to raise in one run (default 50)")
    args = parser.parse_args()
    if args.apply:
        migrate_db_tables()

    floor = SEVERITY_ORDER.index(settings.alert_min_severity)
    with SessionLocal() as db:
        if not args.apply and is_postgres():
            db.execute(text("SET TRANSACTION READ ONLY"))
        ids = [
            row.id
            for row in db.execute(
                select(IncidentModel.id, IncidentModel.severity, IncidentModel.risk_score)
                .where(IncidentModel.severity.in_(SEVERITY_ORDER[floor:]))
                .order_by(IncidentModel.risk_score.desc())
            ).all()
        ]
        print(f"{len(ids)} incident(s) at or above {settings.alert_min_severity}.")
        print(f"Delivery endpoint: {'configured' if (settings.alert_webhook_url or '').strip() else 'none'}")

        # The same per-band, active-only rule a sync applies.
        settings.alert_max_per_sync = args.limit
        pending = alert_service.pending(db, ids)
        print(f"\n{len(pending)} alert(s) to raise, of incidents still being reported:")
        for incident, escalated in pending:
            mark = "escalation" if escalated else "new"
            print(f"  [{incident.severity:>8}] risk {incident.risk_score:>5.1f}  {mark:>10}  {incident.title[:72]}")

        if not args.apply:
            db.rollback()
            print("\nDry run: nothing changed. Re-run with --apply to raise them.")
            return

        raised = alert_service.raise_for(db, ids)
        delivered = alert_service.deliver(db, raised)
        print(f"\nRaised {len(raised)}; delivered {delivered}.")
        for alert in raised:
            if alert.delivery_error:
                print(f"  undelivered: {alert.id} {alert.delivery_error[:120]}")

        if args.investigate:
            investigated = agent_service.investigate_priorities(db, [alert.incident_id for alert in raised])
            print(f"Investigated {len(investigated)}: {', '.join(investigated) or 'none'}")


if __name__ == "__main__":
    main()
