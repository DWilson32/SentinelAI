import json
import math
import re
from pathlib import Path

from app.ml.features import FEATURES, extract
from app.schemas.risk import RiskPrediction, RiskPredictionRequest
from app.services import casualties


class HeuristicRiskModel:
    """Hand-tuned heuristic risk scorer.

    The score is a logistic (sigmoid) function of keyword densities and a
    category prior, but the coefficients are chosen by hand, not learned from
    labelled outcomes — so this is a heuristic, not a trained model, and its
    "confidence" is not a calibrated probability. It reflects how decisively
    the matched keywords push the score, and is highest when the score is most
    extreme, whether or not the rating is right.

    v2 drops source credibility from the score. It measured how sure we are of
    a report, not how bad the event is, so a trusted source raised the risk of
    minor events: GDACS's lowest "green" alerts would have scored high. Every
    source then carried 0.82, so that term's constant share (0.75 x 0.82) moved
    into the bias and no score changed. Credibility now decides what severity
    the evidence supports instead; see services/credibility.py.
    """

    model_name = "sentinel-heuristic-risk-v2"

    category_weights = {
        "Flood": 0.58,
        "Wildfire": 0.55,
        "Health": 0.47,
        "Cybersecurity": 0.52,
        "Financial": 0.42,
        "Earthquake": 0.6,
        "Conflict": 0.62,
        "General": 0.25,
    }
    urgency_terms = {"critical", "emergency", "evacuation", "warning", "rapid", "severe", "outage", "ransomware", "airstrike", "missile", "shelling"}
    infrastructure_terms = {"hospital", "shelter", "power", "road", "bridge", "water", "airport", "school", "bank", "civilian"}
    exposure_terms = {"district", "city", "regional", "multiple", "thousands", "population", "residential", "coastal", "displaced"}

    coefficients = {
        "bias": -0.535,
        "urgency_density": 2.4,
        "infrastructure_density": 1.6,
        "exposure_density": 1.25,
        "category_prior": 1.4,
        "source_volume": 0.42,
        "text_length_signal": 0.28,
    }

    def predict(self, request: RiskPredictionRequest) -> RiskPrediction:
        category = request.category or self._infer_category(request.title, request.text)
        # request.source_credibility is accepted for compatibility but no longer scored.
        features = self._extract_features(request.title, request.text, category, request.source_count)
        linear_score = self.coefficients["bias"] + sum(
            self.coefficients[name] * value for name, value in features.items()
        )
        probability = 1 / (1 + math.exp(-linear_score))
        risk_score = round(min(100, max(0, probability * 100)), 1)
        severity = self.severity_for(risk_score)
        confidence = self._confidence(probability, features)
        feature_importance = self._feature_importance(features)
        drivers = self._drivers(category, features, severity)

        return RiskPrediction(
            risk_score=risk_score,
            severity=severity,
            confidence=confidence,
            drivers=drivers,
            feature_importance=feature_importance,
            features={key: round(value, 4) for key, value in features.items()},
            model_name=self.model_name,
        )

    def _extract_features(
        self,
        title: str,
        text: str,
        category: str,
        source_count: int,
    ) -> dict[str, float]:
        content = f"{title} {text}".lower()
        tokens = re.findall(r"[a-z0-9]+", content)
        token_count = max(1, len(tokens))
        token_set = set(tokens)

        return {
            "urgency_density": min(1.0, sum(1 for term in self.urgency_terms if term in token_set) / 4),
            "infrastructure_density": min(1.0, sum(1 for term in self.infrastructure_terms if term in token_set) / 4),
            "exposure_density": min(1.0, sum(1 for term in self.exposure_terms if term in token_set) / 4),
            "category_prior": self.category_weights.get(category, self.category_weights["General"]),
            "source_volume": min(1.0, math.log1p(source_count) / math.log(8)),
            "text_length_signal": min(1.0, token_count / 180),
        }

    def _feature_importance(self, features: dict[str, float]) -> dict[str, float]:
        weighted = {
            feature: abs(self.coefficients[feature] * value)
            for feature, value in features.items()
        }
        total = sum(weighted.values()) or 1
        return {
            feature: round(value / total, 3)
            for feature, value in sorted(weighted.items(), key=lambda item: item[1], reverse=True)
        }

    def _drivers(self, category: str, features: dict[str, float], severity: str) -> list[str]:
        drivers = [f"{category} category prior"]
        if features["urgency_density"] >= 0.25:
            drivers.append("Urgent crisis language")
        if features["infrastructure_density"] >= 0.25:
            drivers.append("Critical infrastructure impact")
        if features["exposure_density"] >= 0.25:
            drivers.append("Population or regional exposure signal")
        if severity in {"high", "critical"}:
            drivers.insert(0, "High model-estimated escalation risk")
        return drivers[:5]

    def _confidence(self, probability: float, features: dict[str, float]) -> float:
        distance_from_boundary = abs(probability - 0.5) * 2
        evidence_strength = min(1.0, features["urgency_density"] + features["infrastructure_density"] + features["exposure_density"])
        return round(min(0.95, 0.48 + 0.32 * distance_from_boundary + 0.15 * evidence_strength), 2)

    def severity_for(self, score: float) -> str:
        if score >= 85:
            return "critical"
        if score >= 70:
            return "high"
        if score >= 50:
            return "medium"
        return "low"

    def _infer_category(self, title: str, text: str) -> str:
        content = f"{title} {text}".lower()
        keyword_map = {
            "Flood": ["flood", "rainfall", "river", "cyclone", "storm surge"],
            "Wildfire": ["wildfire", "fire", "hotspot", "smoke"],
            "Health": ["outbreak", "hospital", "disease", "infection", "respiratory"],
            "Cybersecurity": ["cyber", "ransomware", "malware", "breach", "cve"],
            "Financial": ["market", "bank", "inflation", "liquidity", "default"],
            "Earthquake": ["earthquake", "seismic", "aftershock"],
            "Conflict": ["war", "armed conflict", "airstrike", "missile", "shelling", "ceasefire", "troops"],
        }
        for category, keywords in keyword_map.items():
            if any(keyword in content for keyword in keywords):
                return category
        return "General"


LEVELS = ("low", "medium", "high", "critical")

DRIVER_TEXT = {
    "alert_yellow": "Agency alert: yellow",
    "alert_orange": "Agency alert: orange",
    "alert_red": "Agency alert: red",
    "agency_report": "Agency report",
    "deaths_log10": "Reported deaths",
    "injuries": "Injuries reported",
    "magnitude_above_4_5": "Earthquake magnitude",
    "urgency_terms": "Urgent crisis language",
    "infrastructure_terms": "Infrastructure affected",
    "exposure_terms": "Population exposure",
    "earthquake": "Earthquake category",
    "flood": "Flood category",
    "conflict": "Conflict category",
}


class TrainedRiskModel:
    """Multinomial logistic regression trained on the labelled evaluation set
    by eval/train_risk.py, and loaded from risk_model_v3.json. That file also
    holds its cross-validated scores, on incidents each fold had not seen.

    The risk score is the severity band, 25 points each, plus the model's
    probability within it: low 6-25, medium 31-50, high 56-75, critical 81-100.

    The training set has no news story with 100 or more deaths, so for those
    the labelling rubric's own thresholds (eval/RUBRIC.md) act as a floor:
    100+ reported deaths is at least high, 1,000+ critical.
    """

    def __init__(self, path: Path = Path(__file__).with_name("risk_model_v3.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if tuple(data["features"]) != FEATURES:
            raise ValueError("risk_model_v3.json was trained on different features; retrain it")
        self.model_name = data["model"]
        self.classes = data["classes"]
        self.coef = data["coef"]
        self.intercept = data["intercept"]
        self.cross_validated = data["cross_validated"]
        self._heuristic = HeuristicRiskModel()

    def predict(self, request: RiskPredictionRequest) -> RiskPrediction:
        # request.source_credibility and source_count are accepted for
        # compatibility but not used: credibility gates severity separately.
        category = request.category or self._heuristic._infer_category(request.title, request.text)
        values = extract(request.title, request.text, category)
        x = [values[name] for name in FEATURES]
        logits = [bias + sum(w * v for w, v in zip(weights, x)) for weights, bias in zip(self.coef, self.intercept)]
        peak = max(logits)
        exps = [math.exp(logit - peak) for logit in logits]
        probabilities = [e / sum(exps) for e in exps]
        index = max(range(len(LEVELS)), key=lambda k: probabilities[k])

        deaths, injured = casualties.read(request.title)
        floor = LEVELS.index(casualties.severity(casualties.Casualties(deaths, injured, "")))
        floored = floor > index and floor >= LEVELS.index("high")
        if floored:
            index = floor
        severity = LEVELS[index]
        within = 0.25 if floored else probabilities[index]
        risk_score = round(25 * index + 25 * within, 1)

        contributions = {name: self.coef[index][j] * x[j] for j, name in enumerate(FEATURES) if x[j]}
        total = sum(abs(c) for c in contributions.values()) or 1.0
        importance = {
            name: round(abs(c) / total, 3)
            for name, c in sorted(contributions.items(), key=lambda item: abs(item[1]), reverse=True)
        }
        return RiskPrediction(
            risk_score=risk_score,
            severity=severity,
            confidence=round(probabilities[index] if not floored else max(probabilities[index], 0.25), 2),
            drivers=self._drivers(values, contributions, severity, deaths, floored),
            feature_importance=importance,
            features={name: round(value, 4) for name, value in values.items()},
            model_name=self.model_name,
        )

    def severity_for(self, score: float) -> str:
        """The band a risk score falls in; the inverse of the score encoding."""
        if score > 75:
            return "critical"
        if score > 50:
            return "high"
        if score > 25:
            return "medium"
        return "low"

    def _drivers(self, values: dict, contributions: dict, severity: str, deaths: int, floored: bool) -> list[str]:
        drivers = []
        if floored:
            drivers.append(f"Reported deaths: {deaths} (the rubric's floor for {severity})")
        elif deaths:
            drivers.append(f"Reported deaths: {deaths}")
        if values["agency_report"] and not (values["alert_yellow"] or values["alert_orange"] or values["alert_red"]):
            drivers.append("Agency alert: green or none")
        for name, contribution in sorted(contributions.items(), key=lambda item: item[1], reverse=True):
            text = DRIVER_TEXT[name]
            if contribution > 0 and name not in ("deaths_log10",) and text not in drivers:
                drivers.append(text)
        if not deaths and not values["agency_report"]:
            drivers.append("No casualties reported")
        return drivers[:5]


# The hand-tuned v2 model, kept so the evaluation can compare against it.
heuristic_risk_model = HeuristicRiskModel()
risk_model = TrainedRiskModel()
