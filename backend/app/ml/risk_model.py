import math
import re

from app.schemas.risk import RiskPrediction, RiskPredictionRequest


class CrisisRiskModel:
    model_name = "sentinel-logistic-risk-v1"

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
        "bias": -1.15,
        "urgency_density": 2.4,
        "infrastructure_density": 1.6,
        "exposure_density": 1.25,
        "category_prior": 1.4,
        "source_credibility": 0.75,
        "source_volume": 0.42,
        "text_length_signal": 0.28,
    }

    def predict(self, request: RiskPredictionRequest) -> RiskPrediction:
        category = request.category or self._infer_category(request.title, request.text)
        features = self._extract_features(request.title, request.text, category, request.source_credibility, request.source_count)
        linear_score = self.coefficients["bias"] + sum(
            self.coefficients[name] * value for name, value in features.items()
        )
        probability = 1 / (1 + math.exp(-linear_score))
        risk_score = round(min(100, max(0, probability * 100)), 1)
        severity = self._severity_from_score(risk_score)
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
        source_credibility: float,
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
            "source_credibility": source_credibility,
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
        if features["source_credibility"] >= 0.75:
            drivers.append("Credible source signal")
        if severity in {"high", "critical"}:
            drivers.insert(0, "High model-estimated escalation risk")
        return drivers[:5]

    def _confidence(self, probability: float, features: dict[str, float]) -> float:
        distance_from_boundary = abs(probability - 0.5) * 2
        evidence_strength = min(1.0, features["urgency_density"] + features["infrastructure_density"] + features["exposure_density"])
        return round(min(0.95, 0.48 + 0.32 * distance_from_boundary + 0.15 * evidence_strength), 2)

    def _severity_from_score(self, score: float) -> str:
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


risk_model = CrisisRiskModel()
