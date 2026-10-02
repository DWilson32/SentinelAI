"""Split reports out of an incident when the rules no longer join them as one event.

Without arguments, lists each incident holding two news reports that the
merge rules now keep apart: the later gives fewer deaths than the earlier
(entity_resolution.toll_falls), and their headlines differ. Given an incident
and source ids, moves those sources to an incident of their own with
EntityResolver.split. Runs against DATABASE_URL.

A dry run by default: the split is made in a transaction and rolled back.
--apply writes the incident's old values to a JSON backup first. Re-index
afterwards (POST /api/rag/reindex) so the moved reports are embedded with
their new incident's title.

    python scripts/split_incident.py
    python scripts/split_incident.py INCIDENT_ID SOURCE_ID [SOURCE_ID ...]
    python scripts/split_incident.py INCIDENT_ID SOURCE_ID --apply --backup split-backup.json
"""

import argparse
import json
import sys
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from app.db.database import SessionLocal  # noqa: E402
from app.db.models import IncidentAliasModel, IncidentModel  # noqa: E402
from app.services.casualties import read  # noqa: E402
from app.services.entity_resolution import Report, entity_resolver, toll_falls  # noqa: E402
from app.services.headlines import normalized_title  # noqa: E402

KEPT = ("title", "summary", "location", "latitude", "longitude", "geo_precision", "risk_score", "severity",
        "severity_note", "status", "evidence", "recommended_actions")


def report(incident: IncidentModel, source) -> Report:
    return Report(url=source.url, title=source.title, category=incident.category, published_at=source.published_at)


def candidates(db) -> None:
    incidents = db.scalars(select(IncidentModel).options(selectinload(IncidentModel.sources))).all()
    found = 0
    for incident in incidents:
        news = [source for source in incident.sources if not report(incident, source).structured]
        for a, b in combinations(news, 2):
            if normalized_title(a.title) != normalized_title(b.title) and toll_falls(report(incident, a), report(incident, b)):
                found += 1
                earlier, later = sorted((a, b), key=lambda source: source.published_at)
                print(f"{incident.id}  {incident.title[:80]}")
                for source in (earlier, later):
                    print(f"   {source.id}  {str(source.published_at)[:16]}  {read(source.title)[0]:>4} dead  {source.title[:70]}")
    print(f"\n{found} pair(s) the rules now keep apart.")


def split(db, incident_id: str, source_ids: list[str], apply: bool, backup_path: Path | None) -> None:
    incident = db.get(IncidentModel, incident_id)
    if incident is None:
        sys.exit(f"No incident {incident_id}")
    backup = {
        "incident": {"id": incident.id, **{column: getattr(incident, column) for column in KEPT}},
        "moved_sources": source_ids,
        "aliases": [
            {"alias_id": alias.alias_id, "merged_at": alias.merged_at}
            for alias in db.scalars(select(IncidentAliasModel).where(IncidentAliasModel.incident_id == incident_id))
        ],
    }
    moving = [source for source in incident.sources if source.id in source_ids]
    earlier = [source for source in incident.sources if source.id not in source_ids]
    tolls = ", then ".join(str(read(source.title)[0]) for source in sorted(earlier + moving, key=lambda s: s.published_at))
    reason = f"a later report gives fewer deaths ({tolls}), so they are different events"

    new = entity_resolver.split(db, incident_id, source_ids, reason)
    for label, item in (("Kept", incident), ("New ", new)):
        print(f"{label} {item.id}: {item.title[:80]}")
        print(f"     {item.location} ({item.geo_precision}) · risk {item.risk_score} {item.severity}"
              f" · {item.evidence['independent_sources']} independent source(s) · {len(item.sources)} report(s)")

    if not apply:
        db.rollback()
        print("\nDry run: nothing changed. Re-run with --apply --backup FILE to save.")
        return
    backup_path.write_text(json.dumps(backup, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    db.commit()
    print(f"\nSaved. Old values backed up to {backup_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("incident", nargs="?", help="the incident to split")
    parser.add_argument("sources", nargs="*", help="the source ids to move out of it")
    parser.add_argument("--apply", action="store_true", help="save, rather than only report")
    parser.add_argument("--backup", type=Path, help="where to write the old values first (required with --apply)")
    args = parser.parse_args()
    if args.apply and not args.backup:
        parser.error("--apply requires --backup")
    if args.incident and not args.sources:
        parser.error("name the source ids to move")

    with SessionLocal() as db:
        if args.incident:
            split(db, args.incident, args.sources, args.apply, args.backup)
        else:
            candidates(db)


if __name__ == "__main__":
    main()
