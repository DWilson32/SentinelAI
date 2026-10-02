"""Inputs to the trained risk model: only what a report carries when it arrives.

Shared by training (eval/train_risk.py) and prediction, so the two cannot
drift apart.
"""

import math
import re

from app.services import casualties

FEATURES = (
    # The agency's own impact alert, when the report is from USGS or GDACS.
    # Green and "not assigned" are the baseline.
    "alert_yellow",
    "alert_orange",
    "alert_red",
    "agency_report",
    # What a news report says happened.
    "deaths_log10",
    "injuries",
    # How strong an earthquake was, above the feed's 4.5 floor.
    "magnitude_above_4_5",
    # The heuristic model's keyword signals, kept as inputs rather than as the answer.
    "urgency_terms",
    "infrastructure_terms",
    "exposure_terms",
    "earthquake",
    "flood",
    "conflict",
)

_USGS_ALERT = re.compile(r"Alert level: ([a-z ]+)\.")
_GDACS_ALERT = re.compile(r"^(Green|Orange|Red)\b")
_MAGNITUDE = re.compile(r"(?:^M\s*|Magnitude\s+)(\d+(?:\.\d+)?)")

URGENCY = {"critical", "emergency", "evacuation", "warning", "rapid", "severe", "outage", "ransomware", "airstrike", "missile", "shelling"}
INFRASTRUCTURE = {"hospital", "shelter", "power", "road", "bridge", "water", "airport", "school", "bank", "civilian"}
EXPOSURE = {"district", "city", "regional", "multiple", "thousands", "population", "residential", "coastal", "displaced"}


def official_alert(title: str, text: str) -> str | None:
    """The agency alert colour in a USGS or GDACS report, or None for news."""
    found = _USGS_ALERT.search(text)
    if found:
        return found.group(1)
    found = _GDACS_ALERT.search(title)
    return found.group(1).lower() if found else None


def extract(title: str, text: str, category: str) -> dict[str, float]:
    alert = official_alert(title, text)
    deaths, injured = casualties.read(title)
    magnitude = _MAGNITUDE.search(title)
    tokens = set(re.findall(r"[a-z0-9]+", f"{title} {text}".lower()))
    return {
        "alert_yellow": float(alert == "yellow"),
        "alert_orange": float(alert == "orange"),
        "alert_red": float(alert == "red"),
        "agency_report": float(alert is not None),
        "deaths_log10": math.log10(1 + deaths),
        "injuries": float(injured),
        "magnitude_above_4_5": max(0.0, float(magnitude.group(1)) - 4.5) if magnitude else 0.0,
        "urgency_terms": min(1.0, len(tokens & URGENCY) / 4),
        "infrastructure_terms": min(1.0, len(tokens & INFRASTRUCTURE) / 4),
        "exposure_terms": min(1.0, len(tokens & EXPOSURE) / 4),
        "earthquake": float(category == "Earthquake"),
        "flood": float(category == "Flood"),
        "conflict": float(category == "Conflict"),
    }


def vector(title: str, text: str, category: str) -> list[float]:
    values = extract(title, text, category)
    return [values[name] for name in FEATURES]
