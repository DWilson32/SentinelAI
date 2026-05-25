# SentinelAI

Autonomous crisis intelligence platform MVP.

SentinelAI monitors crisis incidents, runs multi-agent investigation workflows, scores risk, and presents live intelligence in a full-stack dashboard. The app runs locally with seeded data, optional live news ingest, **semantic RAG chat**, and a **LangGraph** investigation pipeline.

## Stack

- **Frontend:** Next.js, TypeScript, Tailwind CSS, Recharts
- **Backend:** FastAPI, Pydantic, SQLAlchemy (SQLite by default)
- **RAG:** Qdrant, FastEmbed (`BAAI/bge-small-en-v1.5`)
- **Agents:** LangGraph (research → verification → prediction → strategy → report)
- **Optional:** OpenAI for chat answers and agent steps; GNews / NewsAPI for ingest
- **Planned:** PostgreSQL, Celery, Redis, scikit-learn risk model

## Project Structure

```txt
can-you-import-chat-from-chatgpt/
  backend/
    app/
      agents/          # LangGraph investigation workflow
      api/
      core/
      db/
      schemas/
      services/        # RAG, ingestion, incidents, analytics
  frontend/
    app/
    components/
    lib/
  docker-compose.yml   # backend, frontend, qdrant
```

## Run Locally

### VS Code

Open the repository root in VS Code. The shared workspace config in `.vscode/` adds recommended extensions, Python import paths, TypeScript SDK selection, debug launchers, and common tasks.

First-time setup:

```bash
# VS Code task: Backend: create venv
# VS Code task: Backend: install deps
# VS Code task: Frontend: install deps
```

Run the app from **Run and Debug** with **Full stack: FastAPI + Next.js**, or run the **Full stack: dev** task.

- Frontend: http://127.0.0.1:3000
- Backend: http://127.0.0.1:8000
- Health check task: **Backend: health check**

### Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
copy .env.example .env          # optional: API keys
uvicorn app.main:app --reload --port 8000
```

Defaults:

- SQLite at `backend/sentinel.db`
- Qdrant vectors at `backend/qdrant_data/`
- Auto-seed + vector index sync on startup

PostgreSQL (later):

```bash
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/sentinel_ai
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open **http://localhost:3000** (API: **http://127.0.0.1:8000**).

### Docker

```bash
docker compose up
```

Starts Qdrant, backend, and frontend.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/api/incidents` | List incidents |
| GET | `/api/incidents/{id}` | Incident detail |
| POST | `/api/incidents/ingest` | Manual ingest |
| POST | `/api/incidents/ingest/mock` | Demo ingest |
| POST | `/api/incidents/ingest/real` | Public disaster and conflict feed ingest |
| POST | `/api/incidents/ingest/external` | GNews / NewsAPI |
| POST | `/api/chat` | Semantic RAG Q&A |
| POST | `/api/rag/reindex` | Rebuild vector index |
| POST | `/api/ml/risk/predict` | Predict severity, confidence, and drivers |
| POST | `/api/agents/investigate/{id}` | LangGraph investigation |
| GET | `/api/agents/runs/{id}` | Agent run history |
| GET | `/api/reports/{id}` | List generated incident reports |
| POST | `/api/reports/{id}` | Generate executive Markdown report |
| GET | `/api/analytics/overview` | Dashboard metrics |

## Ingestion

Real public feeds (no API key):

```bash
curl -X POST http://127.0.0.1:8000/api/incidents/ingest/real
```

This pulls recent earthquake data from the USGS GeoJSON feed, global disaster alerts from GDACS, current conflict-related coverage from GDELT with Google News RSS fallback, and crisis reports from ReliefWeb when reachable, then indexes the new sources for RAG chat. No API key is required; unavailable or throttled individual feeds do not block other public sources.

Manual:

```bash
curl -X POST http://127.0.0.1:8000/api/incidents/ingest \
  -H "Content-Type: application/json" \
  -d "{\"sources\":[{\"title\":\"Emergency flood warning\",\"url\":\"https://example.com/flood\",\"publisher\":\"Analyst Desk\",\"raw_text\":\"Emergency flood warning issued after heavy rainfall affected roads and hospitals.\",\"category\":\"Flood\",\"location\":\"India\"}]}"
```

Mock (no API keys):

```bash
curl -X POST http://127.0.0.1:8000/api/incidents/ingest/mock
```

External news (set `GNEWS_API_KEY` or `NEWS_API_KEY` in `backend/.env`):

```bash
curl -X POST http://127.0.0.1:8000/api/incidents/ingest/external \
  -H "Content-Type: application/json" \
  -d "{\"provider\":\"gnews\",\"query\":\"flood warning outbreak wildfire\",\"max_results\":5}"
```

## RAG (semantic chat)

- **Qdrant** — local `./qdrant_data` or `QDRANT_URL` (Docker)
- **FastEmbed** — local embeddings (no key required)
- Chunk-level source indexing with configurable overlap
- Qdrant index metadata fingerprinting for stale-index detection
- **OpenAI** — optional richer answers when `OPENAI_API_KEY` is set

Reindex:

```bash
curl -X POST http://127.0.0.1:8000/api/rag/reindex
```

Useful `.env` knobs:

```bash
RAG_CHUNK_CHARS=900
RAG_CHUNK_OVERLAP_CHARS=150
USE_OPENAI_EMBEDDINGS=false
OPENAI_API_KEY=
```

## LangGraph agents

Click **Investigate** on an incident, or:

```bash
curl -X POST http://127.0.0.1:8000/api/agents/investigate/inc-001
```

Pipeline: **Research → Verification → Prediction → Strategy → Report**

- Uses incident sources + vector context when available
- With `OPENAI_API_KEY`: LLM-generated step outputs and executive brief
- Without key: rule-based fallbacks grounded in incident data
- Persists agent runs and an executive report per investigation

## ML risk model

The ingestion pipeline uses `sentinel-logistic-risk-v1`, a calibrated local risk model that extracts crisis features and returns:

- `risk_score`
- `severity`
- `confidence`
- `drivers`
- `feature_importance`

Try it directly:

```bash
curl -X POST http://127.0.0.1:8000/api/ml/risk/predict \
  -H "Content-Type: application/json" \
  -d "{\"title\":\"Hospital ransomware outage\",\"text\":\"Multiple hospitals reported emergency outages after a ransomware campaign affected lab and scheduling systems.\",\"category\":\"Cybersecurity\",\"source_credibility\":0.82,\"source_count\":3}"
```

## Frontend views

- `/` — dashboard, map, charts, RAG chat, active incidents, and investigation trigger
- `/incidents/[id]` — incident detail, sources, timeline, risk explanation, agent outputs, and report generation

## Configuration

Copy `backend/.env.example` to `backend/.env`:

- `OPENAI_API_KEY` — chat + agents
- `GNEWS_API_KEY` / `NEWS_API_KEY` — external ingest
- `QDRANT_URL` — remote Qdrant (optional; local path used by default)

## Backend capabilities

- SQLAlchemy models: incidents, sources, timeline, agent runs, reports
- Startup DB create + seed when empty
- Vector index sync on startup and after ingest
- ML-style risk scoring with explainability
- LangGraph multi-agent investigations
- Manual, mock, GNews, and NewsAPI ingestion
- Incident-level executive report generation
