from pydantic import BaseModel


class ChatRequest(BaseModel):
    query: str
    category: str | None = None
    severity: str | None = None


class Citation(BaseModel):
    title: str
    publisher: str
    url: str


class ChatResponse(BaseModel):
    answer: str
    confidence: float
    citations: list[Citation]
    retrieved_incident_ids: list[str]

