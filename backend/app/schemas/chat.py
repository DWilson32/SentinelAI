from typing import Literal

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
    # Which path produced the answer, so the UI can say so instead of
    # always claiming semantic search.
    retrieval: Literal["semantic", "keyword"] = "keyword"
    # The language model that wrote the answer, or "template" when none did.
    answered_by: str = "template"

