from pydantic import BaseModel, Field


class RiskPredictionRequest(BaseModel):
    title: str = Field(min_length=3)
    text: str = Field(min_length=10)
    category: str | None = None
    source_credibility: float = Field(default=0.7, ge=0, le=1)
    source_count: int = Field(default=1, ge=1)


class RiskPrediction(BaseModel):
    risk_score: float = Field(ge=0, le=100)
    severity: str
    confidence: float = Field(ge=0, le=1)
    drivers: list[str]
    feature_importance: dict[str, float]
    features: dict[str, float]
    model_name: str

