import logging
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import joinedload
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import IncidentModel
from app.schemas.chat import ChatRequest, ChatResponse, Citation
from app.services.embedding_service import embedding_service
from app.services.incident_service import incident_service
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
        chunks = self._retrieve_vector_chunks(request)
        if not chunks:
            chunks = self._retrieve_database_chunks(db, request)

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

    def _retrieve_vector_chunks(self, request: ChatRequest) -> list[RetrievedChunk]:
        if not settings.vector_rag_enabled and not settings.qdrant_url:
            return []
        try:
            if vector_store.count() == 0:
                return []
            query_vector = embedding_service.embed([request.query])[0]
            hits = vector_store.search(
                query_vector,
                limit=settings.rag_top_k,
                category=request.category,
                severity=request.severity,
            )
            return self._chunks_from_hits(hits)
        except Exception as exc:
            logger.warning("Vector RAG retrieval failed; using database fallback: %s", exc)
            return []

    def _retrieve_database_chunks(self, db: Session, request: ChatRequest) -> list[RetrievedChunk]:
        incidents = (
            db.scalars(
                select(IncidentModel)
                .options(joinedload(IncidentModel.sources))
                .order_by(IncidentModel.risk_score.desc())
            )
            .unique()
            .all()
        )
        terms = self._query_terms(request.query)
        scored: list[tuple[float, RetrievedChunk]] = []

        for incident in incidents:
            if request.category and incident.category != request.category:
                continue
            if request.severity and incident.severity != request.severity:
                continue

            for source in incident.sources:
                haystack = " ".join(
                    [
                        incident.title,
                        incident.category,
                        incident.location,
                        incident.summary,
                        source.title,
                        source.publisher,
                        source.raw_text,
                    ]
                ).lower()
                title_haystack = f"{incident.title} {source.title}".lower()
                term_hits = sum(1 for term in terms if term in haystack)
                title_hits = sum(1 for term in terms if term in title_haystack)
                if terms and term_hits == 0:
                    continue
                score = min(
                    0.95,
                    0.42
                    + (0.08 * term_hits)
                    + (0.05 * title_hits)
                    + min(0.2, incident.risk_score / 500),
                )
                scored.append(
                    (
                        score,
                        RetrievedChunk(
                            incident_id=incident.id,
                            score=score,
                            title=source.title,
                            publisher=source.publisher,
                            url=source.url,
                            snippet=self._snippet(source.raw_text or incident.summary, terms),
                            incident_title=incident.title,
                            location=incident.location,
                            severity=incident.severity,
                            risk_score=incident.risk_score,
                        ),
                    )
                )

        scored.sort(key=lambda item: item[0], reverse=True)
        return [chunk for _, chunk in scored[: settings.rag_top_k]]

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

    def _query_terms(self, query: str) -> list[str]:
        stop_words = {
            "a",
            "an",
            "and",
            "are",
            "for",
            "from",
            "how",
            "in",
            "is",
            "of",
            "on",
            "or",
            "the",
            "to",
            "what",
            "which",
            "with",
        }
        return [
            term
            for term in re.findall(r"[a-z0-9]+", query.lower())
            if len(term) > 2 and term not in stop_words
        ]

    def _snippet(self, text: str, terms: list[str]) -> str:
        compact = " ".join(text.split())
        if not compact:
            return ""
        lowered = compact.lower()
        first_match = min((lowered.find(term) for term in terms if term in lowered), default=-1)
        if first_match < 0:
            return compact[:700]
        start = max(0, first_match - 160)
        end = min(len(compact), first_match + 540)
        prefix = "..." if start > 0 else ""
        suffix = "..." if end < len(compact) else ""
        return f"{prefix}{compact[start:end]}{suffix}"

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
            f"Grounded in {len(chunks)} source excerpt(s) from the retrieval layer."
        )


rag_service = RagService()
