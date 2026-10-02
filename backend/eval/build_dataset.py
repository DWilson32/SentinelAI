"""Build the evaluation set: incidents with a severity label (see RUBRIC.md).

    cd backend && python -m eval.build_dataset

Three parts, because the live data alone only has green alerts:

1. The live incidents, read-only from DATABASE_URL. Hazards take the agencies'
   alert; news takes reported casualties, as a suggestion until reviewed.
2. Historical USGS earthquakes with yellow, orange and red PAGER alerts.
3. Historical GDACS floods and cyclones with orange and red alerts.

Writes eval/incidents.jsonl. Reviewed news labels live in eval/news_labels.json
and are kept when the set is rebuilt.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from app.db.database import SessionLocal, is_postgres
from app.db.models import IncidentModel
from app.services.credibility import is_structured, publisher_credibility
from app.services.ingestion_service import ingestion_service
from app.services import casualties

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "incidents.jsonl"
REVIEWED = HERE / "news_labels.json"

PAGER = {"green": "low", "yellow": "medium", "orange": "high", "red": "critical", "not assigned": "low"}
GDACS = {"green": "low", "orange": "high", "red": "critical"}
PER_LEVEL = 20


def main() -> None:
    reviewed = json.loads(REVIEWED.read_text(encoding="utf-8")) if REVIEWED.exists() else {}
    rows = live_rows(reviewed) + usgs_history() + gdacs_history()
    with OUTPUT.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    summary: dict[tuple, int] = {}
    for row in rows:
        key = (row["origin"], row["kind"], row["label"])
        summary[key] = summary.get(key, 0) + 1
    print(f"{len(rows)} incidents -> {OUTPUT}")
    for key in sorted(summary):
        print(f"  {key[0]:6s} {key[1]:6s} {key[2]:8s} {summary[key]}")
    unreviewed = sum(1 for row in rows if row["kind"] == "news" and not row["reviewed"])
    print(f"News labels awaiting review: {unreviewed}")


def hazard_label(source_url: str, title: str, raw_text: str) -> tuple[str, str]:
    if "usgs.gov" in source_url:
        found = re.search(r"Alert level: ([a-z ]+)\.", raw_text)
        alert = found.group(1) if found else "not assigned"
        return PAGER[alert], f"USGS PAGER alert: {alert}"
    colour = title.split()[0].lower()
    return GDACS.get(colour, "low"), f"GDACS alert: {colour}"


def live_rows(reviewed: dict) -> list[dict]:
    with SessionLocal() as db:
        if is_postgres():
            db.execute(text("SET TRANSACTION READ ONLY"))
        incidents = db.scalars(select(IncidentModel).options(selectinload(IncidentModel.sources))).all()
        rows = []
        for incident in incidents:
            sources = sorted(incident.sources, key=lambda s: s.published_at)
            structured = [s for s in sources if is_structured(s.url)]
            row = {
                "id": incident.id,
                "origin": "live",
                "kind": "hazard" if structured else "news",
                "category": incident.category,
                "title": incident.title,
                "location": incident.location,
                "summary": incident.summary,
                "sources": [
                    {
                        "title": s.title,
                        "publisher": s.publisher,
                        "url": s.url,
                        "credibility": s.credibility_score,
                        "published_at": s.published_at.isoformat(),
                        "text": s.raw_text,
                    }
                    for s in sources
                ],
            }
            if structured:
                # The worst alert among the incident's agency reports.
                labels = [hazard_label(s.url, s.title, s.raw_text) for s in structured]
                order = ["low", "medium", "high", "critical"]
                row["label"], row["label_source"] = max(labels, key=lambda item: order.index(item[0]))
                row["reviewed"] = True
                row["reviewed_by"] = "agency alert"
            else:
                found = casualties.from_headlines([s.title for s in sources])
                suggestion = casualties.severity(found)
                reason = (
                    f"reported deaths: {found.deaths} ({found.evidence})"
                    if found.deaths
                    else "injuries reported, no deaths" if found.injuries else "no casualties reported"
                )
                decision = reviewed.get(incident.id)
                row["suggested"] = suggestion
                row["label"] = decision["label"] if decision else suggestion
                row["label_source"] = decision["reason"] if decision else reason
                row["reviewed"] = bool(decision)
                row["reviewed_by"] = decision.get("reviewed_by") if decision else None
            rows.append(row)
        db.rollback()
    return rows


def _spread(items: list, count: int) -> list:
    """An even sample across the period, so no single year dominates."""
    if len(items) <= count:
        return items
    step = len(items) / count
    return [items[int(index * step)] for index in range(count)]


def usgs_history() -> list[dict]:
    rows = []
    with httpx.Client(timeout=60) as client:
        for alert in ("yellow", "orange", "red"):
            response = client.get(
                "https://earthquake.usgs.gov/fdsnws/event/1/query",
                params={"format": "geojson", "starttime": "2016-01-01", "endtime": "2026-09-30", "alertlevel": alert, "orderby": "time", "limit": 400},
            )
            response.raise_for_status()
            for feature in _spread(response.json()["features"], PER_LEVEL):
                source = ingestion_service.usgs_source(feature)
                if source is None:
                    continue
                rows.append(
                    {
                        "id": f"usgs-{feature['id']}",
                        "origin": "usgs",
                        "kind": "hazard",
                        "category": "Earthquake",
                        "title": source.title,
                        "location": source.location,
                        "summary": ingestion_service._summarize(source.raw_text),
                        "sources": [_history_source(source)],
                        "label": PAGER[alert],
                        "label_source": f"USGS PAGER alert: {alert}",
                        "reviewed": True,
                        "reviewed_by": "agency alert",
                    }
                )
    return rows


def gdacs_history() -> list[dict]:
    with httpx.Client(timeout=90) as client:
        response = client.get(
            "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH",
            params={"eventlist": "FL;TC", "alertlevel": "Orange;Red", "fromdate": "2020-01-01", "todate": "2026-09-30", "pagesize": 100},
        )
        response.raise_for_status()
    features = response.json()["features"]
    rows = []
    for colour in ("Orange", "Red"):
        chosen = _spread([f for f in features if f["properties"]["alertlevel"] == colour], PER_LEVEL)
        for feature in chosen:
            p = feature["properties"]
            if p["eventtype"] == "TC":
                severity_text = (p.get("severitydata") or {}).get("severitytext", "").strip()
                title = f"{colour} notification for tropical cyclone {p['name'].split()[-1]}. {severity_text}".strip()
            else:
                title = f"{colour} flood alert in {p.get('country') or 'unknown country'}"
            raw_text = f"{title}. {p.get('htmldescription', '').strip()}"
            url = f"https://www.gdacs.org/report.aspx?eventtype={p['eventtype']}&eventid={p['eventid']}"
            longitude, latitude = feature["geometry"]["coordinates"][:2]
            source = {
                "title": title,
                "publisher": "GDACS",
                "url": url,
                "credibility": publisher_credibility("GDACS", url),
                "published_at": p.get("fromdate"),
                "text": raw_text,
                "latitude": latitude,
                "longitude": longitude,
            }
            rows.append(
                {
                    "id": f"gdacs-{p['eventtype']}-{p['eventid']}",
                    "origin": "gdacs",
                    "kind": "hazard",
                    # As the live ingester files it: by keyword, from the title and text.
                    "category": ingestion_service._infer_category(title, raw_text),
                    "title": title,
                    "location": p.get("country") or "Global",
                    "summary": ingestion_service._summarize(raw_text),
                    "sources": [source],
                    "label": GDACS[colour.lower()],
                    "label_source": f"GDACS alert: {colour.lower()}",
                    "reviewed": True,
                    "reviewed_by": "agency alert",
                }
            )
    return rows


def _history_source(source) -> dict:
    return {
        "title": source.title,
        "publisher": source.publisher,
        "url": str(source.url),
        "credibility": publisher_credibility(source.publisher, str(source.url)),
        "published_at": (source.published_at or datetime.now(timezone.utc)).isoformat(),
        "text": source.raw_text,
        "latitude": source.latitude,
        "longitude": source.longitude,
    }


if __name__ == "__main__":
    main()
