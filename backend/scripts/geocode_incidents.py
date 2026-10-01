"""Locate incidents stored before headlines were geocoded. Runs against
DATABASE_URL.

Incidents with feed coordinates are marked "reported". Those stored at (0, 0)
are located from their headline, or failing that from the headline of another
of their sources. A dry run by default, read-only on Postgres; --apply writes
the old values to a JSON backup, saves, then re-embeds the changed incidents,
whose location is part of their search text.

    python scripts/geocode_incidents.py
    python scripts/geocode_incidents.py --apply --backup geocode-backup.json
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from app.db.database import SessionLocal, is_postgres, migrate_db_tables  # noqa: E402
from app.db.models import IncidentModel  # noqa: E402
from app.services import geocoder  # noqa: E402
from app.services.rag_index_service import rag_index_service  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="save, rather than only report")
    parser.add_argument("--backup", type=Path, help="where to write the old values first (required with --apply)")
    args = parser.parse_args()
    if args.apply and not args.backup:
        parser.error("--apply requires --backup")
    if args.apply:
        migrate_db_tables()  # adds the geo_precision column if the app has not yet

    with SessionLocal() as db:
        if not args.apply and is_postgres():
            db.execute(text("SET TRANSACTION READ ONLY"))
        incidents = db.scalars(
            select(IncidentModel).where(IncidentModel.geo_precision.is_(None)).options(selectinload(IncidentModel.sources))
        ).all()

        changes = []
        for incident in incidents:
            if incident.latitude or incident.longitude:
                changes.append((incident, None, "reported"))
                continue
            titles = [incident.title] + [
                source.title for source in sorted(incident.sources, key=lambda s: s.published_at, reverse=True)
            ]
            place = next(filter(None, (geocoder.locate(title) for title in titles)), None)
            changes.append((incident, place, place.precision if place else None))

        located = [(incident, place) for incident, place, _ in changes if place is not None]
        print(
            f"{len(incidents)} incidents without a precision: "
            f"{sum(1 for *_, p in changes if p == 'reported')} have feed coordinates, "
            f"{len(located)} located from headlines, "
            f"{sum(1 for *_, p in changes if p is None)} stay unlocated."
        )
        print("By precision:", dict(Counter(p for *_, p in changes if p not in (None, "reported"))))
        for incident, place in located:
            print(f"  {place.precision:8s} {place.label[:38]:38s} <- {incident.title[:70]}")

        if not args.apply:
            print("\nDry run: nothing changed. Re-run with --apply --backup FILE to save.")
            return

        backup = [
            {
                "id": incident.id,
                "location": incident.location,
                "latitude": incident.latitude,
                "longitude": incident.longitude,
                "geo_precision": incident.geo_precision,
            }
            for incident, _, precision in changes
            if precision is not None
        ]
        args.backup.write_text(json.dumps(backup, indent=2), encoding="utf-8")
        print(f"\nBackup of {len(backup)} rows written to {args.backup}")

        for incident, place, precision in changes:
            if precision is None:
                continue
            incident.geo_precision = precision
            if place is not None:
                incident.location, incident.latitude, incident.longitude = place.label, place.latitude, place.longitude
        db.commit()
        result = rag_index_service.sync_all(db)
        print(f"Saved. Re-embedded {result.embedded} chunks whose location changed.")


if __name__ == "__main__":
    main()
