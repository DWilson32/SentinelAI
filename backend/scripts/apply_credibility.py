"""Give stored sources their publisher's credibility, and re-rate incidents.

Sources stored before publisher credibility all carry their feed's 0.82. This
sets each to its publisher's tier, re-scores every incident with the current
risk model (the score itself is unchanged; its explanation no longer credits
credibility), and caps severity where the evidence does not support it.
Runs against DATABASE_URL.

A dry run by default, read-only on Postgres; --apply writes the old values to
a JSON backup first.

    python scripts/apply_credibility.py
    python scripts/apply_credibility.py --apply --backup credibility-backup.json
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
from app.ml.risk_model import risk_model  # noqa: E402
from app.schemas.risk import RiskPredictionRequest  # noqa: E402
from app.services.credibility import apply_evidence, publisher_credibility  # noqa: E402

RATED = ("risk_score", "severity", "severity_note", "status", "recommended_actions", "risk_drivers", "feature_importance")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="save, rather than only report")
    parser.add_argument("--backup", type=Path, help="where to write the old values first (required with --apply)")
    args = parser.parse_args()
    if args.apply and not args.backup:
        parser.error("--apply requires --backup")
    if args.apply:
        migrate_db_tables()  # adds the severity_note column if the app has not yet

    with SessionLocal() as db:
        if not args.apply and is_postgres():
            db.execute(text("SET TRANSACTION READ ONLY"))
        incidents = db.scalars(select(IncidentModel).options(selectinload(IncidentModel.sources))).all()
        backup = [
            {
                "id": incident.id,
                **{column: getattr(incident, column) for column in RATED},
                "sources": {source.id: source.credibility_score for source in incident.sources},
            }
            for incident in incidents
        ]

        tiers, moves, scores_changed, notes = Counter(), Counter(), 0, []
        for incident in incidents:
            before = (incident.severity, incident.risk_score)
            for source in incident.sources:
                source.credibility_score = publisher_credibility(source.publisher, source.url)
                tiers[source.credibility_score] += 1
            best = max(
                (
                    risk_model.predict(
                        RiskPredictionRequest(
                            title=source.title,
                            text=source.raw_text,
                            category=incident.category,
                            source_count=1,
                        )
                    )
                    for source in incident.sources
                ),
                key=lambda prediction: prediction.risk_score,
            )
            incident.risk_score = best.risk_score
            incident.risk_confidence = best.confidence
            incident.risk_drivers = best.drivers
            incident.feature_importance = best.feature_importance
            apply_evidence(incident)
            moves[(before[0], incident.severity)] += 1
            scores_changed += incident.risk_score != before[1]
            if incident.severity_note:
                notes.append((incident.title, incident.severity_note))

        print(f"{len(incidents)} incidents, {sum(tiers.values())} sources.")
        print("Sources by credibility:", dict(sorted(tiers.items(), reverse=True)))
        print("Severity before -> after:", dict(moves))
        print(f"Risk scores changed: {scores_changed}")
        for title, note in notes:
            print(f"  {title[:70]}\n    {note}")

        if not args.apply:
            db.rollback()
            print("\nDry run: nothing changed. Re-run with --apply --backup FILE to save.")
            return

        args.backup.write_text(json.dumps(backup, indent=2, default=str), encoding="utf-8")
        db.commit()
        print(f"\nSaved. Old values backed up to {args.backup}")


if __name__ == "__main__":
    main()
