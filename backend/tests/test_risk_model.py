"""The trained risk model (sentinel-logistic-risk-v3)."""

import pytest

from app.ml.risk_model import risk_model
from app.schemas.risk import RiskPredictionRequest


def rate(title, text, category):
    return risk_model.predict(RiskPredictionRequest(title=title, text=text, category=category))


def usgs(alert):
    return rate(
        "M 6.5 - 20 km N of Example City",
        f"M 6.5 - 20 km N of Example City. USGS reported a magnitude 6.5 earthquake. Alert level: {alert}. Tsunami flag: 0.",
        "Earthquake",
    )


@pytest.mark.parametrize(
    "alert, severity",
    [("not assigned", "low"), ("green", "low"), ("yellow", "medium"), ("orange", "high"), ("red", "critical")],
)
def test_agency_alerts_decide_hazards(alert, severity):
    assert usgs(alert).severity == severity


def test_news_without_casualties_is_low():
    assert rate("Trump rejects Iran's latest ceasefire proposal", "The proposal was rejected on Thursday.", "Conflict").severity == "low"


def test_reported_deaths_make_news_medium():
    prediction = rate("Airstrike near a market in Rakhine state kills 33 people", "An airstrike killed 33 people.", "Conflict")
    assert prediction.severity == "medium"
    assert prediction.drivers[0] == "Reported deaths: 33"


@pytest.mark.parametrize("toll, severity", [(120, "high"), (1500, "critical")])
def test_large_tolls_meet_the_rubric_floor(toll, severity):
    # No training example reports 100+ deaths, so the rubric's thresholds apply.
    prediction = rate(f"Missile strike kills {toll:,} people", f"A missile strike killed {toll} people.", "Conflict")
    assert prediction.severity == severity


def test_the_risk_score_band_gives_back_the_severity():
    for alert in ("not assigned", "yellow", "orange", "red"):
        prediction = usgs(alert)
        assert risk_model.severity_for(prediction.risk_score) == prediction.severity


def test_cross_validated_scores_ship_with_the_model():
    # A guard against retraining into something worse without noticing.
    assert risk_model.cross_validated["accuracy"] >= 0.9
    assert risk_model.cross_validated["by_kind"]["news"]["accuracy"] >= 0.85
