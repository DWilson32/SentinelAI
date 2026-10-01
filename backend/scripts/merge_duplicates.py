"""Merge incidents that describe the same event.

For data ingested before entity resolution existed; new reports are resolved as
they arrive. Runs against DATABASE_URL.

A dry run by default, read-only on Postgres: it prints the groups it would
merge. --apply writes the affected rows to a JSON backup, merges every group in
one transaction, then re-embeds the moved sources so search finds them under
their new incident.

    python scripts/merge_duplicates.py
    python scripts/merge_duplicates.py --apply --backup merge-backup.json
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db.database import SessionLocal, engine, is_postgres  # noqa: E402
from app.db.models import (  # noqa: E402
    AgentRunModel,
    IncidentAliasModel,
    IncidentModel,
    ReportModel,
    SourceChunkModel,
    SourceModel,
    TimelineEventModel,
)
from app.services.entity_resolution import MergeGroup, entity_resolver  # noqa: E402
from app.services.rag_index_service import rag_index_service  # noqa: E402

CHILDREN = (
    (SourceModel, SourceModel.id),
    (TimelineEventModel, TimelineEventModel.id),
    (AgentRunModel, AgentRunModel.id),
    (ReportModel, ReportModel.id),
    (SourceChunkModel, SourceChunkModel.id),
    (IncidentAliasModel, IncidentAliasModel.alias_id),
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="merge, rather than only report")
    parser.add_argument("--backup", type=Path, help="where to write the affected rows first (required with --apply)")
    args = parser.parse_args()
    if args.apply and not args.backup:
        parser.error("--apply requires --backup")
    if args.apply:
        IncidentAliasModel.__table__.create(engine, checkfirst=True)

    with SessionLocal() as db:
        if not args.apply and is_postgres():
            db.execute(text("SET TRANSACTION READ ONLY"))

        groups = entity_resolver.plan_merges(db)
        incidents = {incident.id: incident for incident in db.scalars(select(IncidentModel))}
        merging = sum(len(group.members) for group in groups)
        print(
            f"{len(incidents)} incidents; {len(groups)} groups; {merging} would merge into another, "
            f"leaving {len(incidents) - merging}."
        )
        for group in groups:
            survivor = incidents[group.survivor_id]
            print(f"\n[{survivor.category}] keep {survivor.id}  {survivor.title[:90]}")
            for incident_id, reason in group.members:
                print(f"    + {incident_id}  {incidents[incident_id].title[:80]}\n        ({reason})")

        if not args.apply:
            print("\nDry run: nothing changed. Re-run with --apply --backup FILE to merge.")
            return

        args.backup.write_text(json.dumps(_backup(db, groups), indent=2, default=str), encoding="utf-8")
        print(f"\nBackup of the affected rows written to {args.backup}")

    with SessionLocal() as db:
        entity_resolver.apply_merges(db, groups)
        result = rag_index_service.sync_all(db)
        print(f"Merged {merging} incidents into {len(groups)}. Re-embedded {result.embedded} chunks.")


def _row(model, instance) -> dict:
    return {column.key: getattr(instance, column.key) for column in model.__table__.columns}


def _backup(db: Session, groups: list[MergeGroup]) -> list[dict]:
    """Everything needed to undo the merge by hand: each merged incident's row,
    the ids of the rows that move off it, and the survivor as it was."""
    entries = []
    for group in groups:
        merged = []
        for incident_id, reason in group.members:
            merged.append(
                {
                    "incident": _row(IncidentModel, db.get(IncidentModel, incident_id)),
                    "reason": reason,
                    "moved": {
                        model.__tablename__: list(db.scalars(select(key).where(model.incident_id == incident_id)))
                        for model, key in CHILDREN
                    },
                }
            )
        entries.append({"survivor": _row(IncidentModel, db.get(IncidentModel, group.survivor_id)), "merged": merged})
    return entries


if __name__ == "__main__":
    main()
