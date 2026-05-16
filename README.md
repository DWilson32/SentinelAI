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
| POST | `/api/incidents/ingest/external` | GNews / NewsAPI |
| POST | `/api/chat` | Semantic RAG Q&A |
| POST | `/api/rag/reindex` | Rebuild vector index |
| POST | `/api/agents/investigate/{id}` | LangGraph investigation |
| GET | `/api/agents/runs/{id}` | Agent run history |
| GET | `/api/analytics/overview` | Dashboard metrics |

## Ingestion

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
- **OpenAI** — optional richer answers when `OPENAI_API_KEY` is set

Reindex:

```bash
curl -X POST http://127.0.0.1:8000/api/rag/reindex
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

## Configuration

Copy `backend/.env.example` to `backend/.env`:

- `OPENAI_API_KEY` — chat + agents
- `GNEWS_API_KEY` / `NEWS_API_KEY` — external ingest
- `QDRANT_URL` — remote Qdrant (optional; local path used by default)

## Backend capabilities

- SQLAlchemy models: incidents, sources, timeline, agent runs, reports
- Startup DB create + seed when empty
- Vector index sync on startup and after ingest
- LangGraph multi-agent investigations
- Manual, mock, GNews, and NewsAPI ingestion
