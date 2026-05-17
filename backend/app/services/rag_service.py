import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import settings
from app.schemas.chat import ChatRequest, ChatResponse, Citation
from app.services.embedding_service import embedding_service
from app.services.incident_service import incident_service
from app.services.rag_index_service import rag_index_service
from app.services.vector_store import vector_store

logger = logging.getLogger(__name__)


@dataclass
class RetrievedChunk:
    incident_id: str
    score: float
    title: str
    publisher: str
    url: str
    snippet: str
    incident_title: str
    location: str
    severity: str
    risk_score: float


class RagService:
    def answer(self, db: Session, request: ChatRequest) -> ChatResponse:
        if vector_store.count() == 0:
            rag_index_service.sync_all(db)

        query_vector = embedding_service.embed([request.query])[0]
        hits = vector_store.search(
            query_vector,
            limit=settings.rag_top_k,
            category=request.category,
            severity=request.severity,
        )
        chunks = self._chunks_from_hits(hits)

        if not chunks:
            return ChatResponse(
                answer=(
                    "No relevant crisis intelligence was found for that query. "
                    "Try broadening the question or ingest additional sources."
                ),
                confidence=0.35,
                citations=[],
                retrieved_incident_ids=[],
            )

        citations = [
            Citation(title=chunk.title, publisher=chunk.publisher, url=chunk.url)
            for chunk in chunks
        ]
        incident_ids = list(dict.fromkeys(chunk.incident_id for chunk in chunks))
        confidence = min(0.95, max(chunk.score for chunk in chunks))
        top_incident = incident_service.get_incident(db, chunks[0].incident_id) if chunks else None
        answer = self._generate_answer(request.query, chunks, top_incident)

        return ChatResponse(
            answer=answer,
            confidence=round(confidence, 2),
            citations=citations,
            retrieved_incident_ids=incident_ids,
        )

    def _chunks_from_hits(self, hits) -> list[RetrievedChunk]:
        chunks: list[RetrievedChunk] = []
        for hit in hits:
            payload = hit.payload or {}
            chunks.append(
                RetrievedChunk(
                    incident_id=str(payload.get("incident_id", "")),
                    score=float(hit.score or 0.0),
                    title=str(payload.get("source_title", "")),
                    publisher=str(payload.get("publisher", "")),
                    url=str(payload.get("url", "")),
                    snippet=str(payload.get("snippet", "")),
                    incident_title=str(payload.get("incident_title", "")),
                    location=str(payload.get("location", "")),
                    severity=str(payload.get("severity", "")),
                    risk_score=float(payload.get("risk_score", 0.0)),
                )
            )
        return chunks

    def _generate_answer(self, query: str, chunks: list[RetrievedChunk], top_incident) -> str:
        if settings.openai_api_key:
            try:
                return self._generate_openai_answer(query, chunks)
            except Exception as exc:
                logger.warning("OpenAI RAG answer generation failed; using deterministic fallback: %s", exc)
        return self._compose_answer(query, chunks, top_incident)

    def _generate_openai_answer(self, query: str, chunks: list[RetrievedChunk]) -> str:
        from openai import OpenAI

        context_blocks = []
        for index, chunk in enumerate(chunks, start=1):
            context_blocks.append(
                f"[{index}] Incident: {chunk.incident_title} ({chunk.severity}, risk {chunk.risk_score:.0f})\n"
                f"Location: {chunk.location}\n"
                f"Source: {chunk.title} - {chunk.publisher}\n"
                f"Excerpt: {chunk.snippet}"
            )
        context = "\n\n".join(context_blocks)
        client = OpenAI(api_key=settings.openai_api_key)
        response = client.chat.completions.create(
            model=settings.openai_chat_model,
            temperature=0.2,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are SentinelAI, a crisis intelligence analyst. "
                        "Answer only using the provided source excerpts. "
                        "If the context is insufficient, say what is missing. "
                        "Be concise and actionable."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Sources:\n{context}\n\nQuestion: {query}",
                },
            ],
        )
        content = response.choices[0].message.content
        return content.strip() if content else self._compose_answer(query, chunks, None)

    def _compose_answer(self, query: str, chunks: list[RetrievedChunk], top_incident) -> str:
        top = chunks[0]
        actions = ""
        if top_incident and top_incident.recommended_actions:
            actions = " Recommended actions: " + "; ".join(top_incident.recommended_actions[:2]) + "."

        supporting = " ".join(
            f"[{chunk.incident_title}: {chunk.snippet[:180].rstrip()}...]"
            for chunk in chunks[:2]
        )
        return (
            f"Semantic retrieval matched your question to '{top.incident_title}' in {top.location} "
            f"({top.severity}, risk {top.risk_score:.0f}/100). "
            f"{supporting}"
            f"{actions} "
            f"Grounded in {len(chunks)} source excerpt(s) from the vector index."
        )


rag_service = RagService()
