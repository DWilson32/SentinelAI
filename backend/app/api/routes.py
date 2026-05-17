import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.agent import AgentRun
from app.schemas.analytics import AnalyticsOverview
from app.schemas.chat import ChatRequest, ChatResponse
from app.schemas.ingestion import ExternalIngestRequest, IngestRequest, IngestResponse
from app.schemas.incident import Incident, IncidentDetail
from app.schemas.report import Report, ReportCreateResponse
from app.schemas.risk import RiskPrediction, RiskPredictionRequest
from app.services.agent_service import agent_service
from app.services.analytics_service import analytics_service
from app.services.ingestion_service import ingestion_service
from app.services.incident_service import incident_service
from app.services.rag_index_service import rag_index_service
from app.services.rag_service import rag_service
from app.services.report_service import report_service
from app.ml.risk_model import risk_model

router = APIRouter()


@router.get("/incidents", response_model=list[Incident])
def list_incidents(db: Session = Depends(get_db)) -> list[Incident]:
    return incident_service.list_incidents(db)


@router.get("/incidents/{incident_id}", response_model=IncidentDetail)
def get_incident(incident_id: str, db: Session = Depends(get_db)) -> IncidentDetail:
    incident = incident_service.get_incident(db, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident


@router.post("/incidents/ingest", response_model=IngestResponse)
async def ingest_incidents(request: IngestRequest, db: Session = Depends(get_db)) -> IngestResponse:
    return await ingestion_service.ingest_manual(db, request)


@router.post("/incidents/ingest/mock", response_model=IngestResponse)
async def ingest_mock_incidents(db: Session = Depends(get_db)) -> IngestResponse:
    return await ingestion_service.ingest_mock(db)


@router.post("/incidents/ingest/real", response_model=IngestResponse)
async def ingest_real_incidents(db: Session = Depends(get_db)) -> IngestResponse:
    try:
        return await ingestion_service.ingest_public_feeds(db)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Public crisis feed failed: {exc}") from exc


@router.post("/incidents/ingest/external", response_model=IngestResponse)
async def ingest_external_incidents(request: ExternalIngestRequest, db: Session = Depends(get_db)) -> IngestResponse:
    try:
        return await ingestion_service.ingest_external(db, request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"External ingestion provider failed: {exc}") from exc


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, db: Session = Depends(get_db)) -> ChatResponse:
    return rag_service.answer(db, request)


@router.post("/rag/reindex")
def reindex_rag(db: Session = Depends(get_db)) -> dict[str, int]:
    indexed = rag_index_service.sync_all(db, force=True)
    return {"indexed_chunks": indexed, "indexed_sources": indexed}


@router.post("/ml/risk/predict", response_model=RiskPrediction)
def predict_risk(request: RiskPredictionRequest) -> RiskPrediction:
    return risk_model.predict(request)


@router.post("/agents/investigate/{incident_id}", response_model=list[AgentRun])
def investigate_incident(incident_id: str, db: Session = Depends(get_db)) -> list[AgentRun]:
    if incident_service.get_incident(db, incident_id) is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return agent_service.investigate(db, incident_id)


@router.get("/agents/runs/{incident_id}", response_model=list[AgentRun])
def list_agent_runs(incident_id: str, db: Session = Depends(get_db)) -> list[AgentRun]:
    return agent_service.list_runs(db, incident_id)


@router.get("/reports/{incident_id}", response_model=list[Report])
def list_reports(incident_id: str, db: Session = Depends(get_db)) -> list[Report]:
    if incident_service.get_incident(db, incident_id) is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return report_service.list_reports(db, incident_id)


@router.post("/reports/{incident_id}", response_model=ReportCreateResponse)
def generate_report(incident_id: str, db: Session = Depends(get_db)) -> ReportCreateResponse:
    response = report_service.generate_report(db, incident_id)
    if response is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return response


@router.get("/analytics/overview", response_model=AnalyticsOverview)
def analytics_overview(db: Session = Depends(get_db)) -> AnalyticsOverview:
    return analytics_service.get_overview(db)
