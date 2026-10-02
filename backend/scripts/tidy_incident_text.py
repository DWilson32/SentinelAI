"""Decode HTML entities left in stored feed text, and title each incident by its
report with the highest death toll.

Before ingestion decoded entities, Google News descriptions were stored with a
literal "&nbsp;&nbsp;" before the outlet's name, in source texts and in the
incident summaries made from them. Before an incident was retitled as reports
arrived, a toll that rose kept its first figure in the title.
Runs against DATABASE_URL.

A dry run by default, read-only on Postgres; --apply writes the old values to
a JSON backup first. Re-index afterwards (POST /api/rag/reindex): the index
embeds titles, summaries and source texts.

    python scripts/tidy_incident_text.py
    python scripts/tidy_incident_text.py --apply --backup text-backup.json
"""

import argparse
import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from app.db.database import SessionLocal, is_postgres  # noqa: E402
from app.db.models import IncidentModel  # noqa: E402
from app.services.headlines import retitle  # noqa: E402

ENTITY = re.compile(r"&(?:#\d+|#x[0-9a-fA-F]+|[A-Za-z]+);")


def decoded(value: str) -> str:
    """The text with entities decoded, as ingestion now stores it."""
    return " ".join(html.unescape(value).split()) if ENTITY.search(value) else value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="save, rather than only report")
    parser.add_argument("--backup", type=Path, help="where to write the old values first (required with --apply)")
    args = parser.parse_args()
    if args.apply and not args.backup:
        parser.error("--apply requires --backup")

    with SessionLocal() as db:
        if not args.apply and is_postgres():
            db.execute(text("SET TRANSACTION READ ONLY"))
        incidents = db.scalars(select(IncidentModel).options(selectinload(IncidentModel.sources))).all()

        backup = {"sources": {}, "incidents": {}}
        retitled = []
        for incident in incidents:
            for source in incident.sources:
                old = {"title": source.title, "raw_text": source.raw_text}
                source.title, source.raw_text = decoded(source.title), decoded(source.raw_text)
                if (source.title, source.raw_text) != (old["title"], old["raw_text"]):
                    backup["sources"][source.id] = old
            old = {"title": incident.title, "summary": incident.summary}
            incident.title, incident.summary = decoded(incident.title), decoded(incident.summary)
            before_toll = incident.title
            retitle(incident)
            if incident.title != before_toll:
                retitled.append((old["title"], incident.title))
            if (incident.title, incident.summary) != (old["title"], old["summary"]):
                backup["incidents"][incident.id] = old

        print(f"{len(incidents)} incidents.")
        print(f"Source texts decoded: {len(backup['sources'])}")
        print(f"Incidents changed: {len(backup['incidents'])}, of which retitled by toll: {len(retitled)}")
        for before, after in retitled:
            print(f"  {before[:90]}\n    -> {after[:90]}")
        sample = next(iter(backup["sources"].values()), None)
        if sample:
            print(f"Example source text before: ...{sample['raw_text'][-90:]}")

        if not args.apply:
            db.rollback()
            print("\nDry run: nothing changed. Re-run with --apply --backup FILE to save.")
            return

        args.backup.write_text(json.dumps(backup, indent=2, ensure_ascii=False), encoding="utf-8")
        db.commit()
        print(f"\nSaved. Old values backed up to {args.backup}")


if __name__ == "__main__":
    main()
