# SentinelAI

Autonomous crisis intelligence platform MVP.

SentinelAI monitors crisis incidents, runs multi-agent investigation workflows, scores risk, and presents live intelligence in a full-stack dashboard. The app runs locally with seeded data, optional live news ingest, **semantic RAG chat**, and a **LangGraph** investigation pipeline.

## Stack

- **Frontend:** Next.js, TypeScript, Tailwind CSS, Recharts
- **Backend:** FastAPI, Pydantic, SQLAlchemy (SQLite by default)
- **RAG:** pgvector in Postgres, FastEmbed (`BAAI/bge-small-en-v1.5`, 384-dim)
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
  docker-compose.yml   # backend, frontend, postgres+pgvector
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

- SQLite at `backend/sentinel.db` — chat uses **keyword** retrieval here, since SQLite has no pgvector
- Tables are created and seeded on first request

For semantic search, point at Postgres with the pgvector extension (production uses Neon):

```bash
DATABASE_URL=postgresql://user:password@host/dbname?sslmode=require
```

The `vector` extension, the `source_chunks` table and its HNSW index are created automatically.

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

Starts Postgres with pgvector, the backend, and the frontend.

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
| POST | `/api/chat` | RAG Q&A — reports whether `semantic` or `keyword` retrieval answered |
| POST | `/api/rag/reindex` | Sync the vector index (incremental; `?force=true` re-embeds all) |
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

### Entity resolution

One event is one incident. A report about something already tracked becomes another source of that incident instead of a new one (`backend/app/services/entity_resolution.py`):

- **Earthquakes** match on physical identity: epicentres within 100 km, magnitudes within 0.5, origin times within 5 minutes. This catches USGS and GDACS reporting the same quake while keeping real aftershocks apart.
- **News** matches a report in the same category from the last 48 hours on an identical headline, or on embedding similarity of at least 0.92. Text that is mostly not in Latin script needs an identical headline, because the embedding model is English-only.
- **GDACS cyclones and floods** are never matched on text, because their alerts come from templates and different storms read alike. Each has a unique event id in its URL.

The thresholds were set against the live data; the module docstring records the cases. Incidents ingested before this existed can be merged with `python scripts/merge_duplicates.py` (a dry run; add `--apply --backup FILE` to merge). Merged ids stay valid as aliases of the incident they joined.

### Geocoding

News feeds file stories under "Global". When a report has no coordinates, `backend/app/services/geocoder.py` reads the place from its headline. The rules favour precision, so an incident is left unlocated rather than put in the wrong place:

- The most specific place named wins: city, then region or sea, then country. A place after "in", "near" and similar words wins a tie.
- Demonyms ("Russian shelling") and the US, which is a party to most international stories, never count as the location on their own.
- Names that double as common words or first names (Nice, Victoria) are ignored.

The map draws region- and country-level positions as hollow rings, because they are approximate.

Place names come from [GeoNames](https://www.geonames.org/), licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The index covers countries, first-level regions, and cities that are capitals, regional seats or have 100,000+ people. `scripts/build_gazetteer.py` compiles it into `app/data/gazetteer.json.gz`. To locate incidents stored before geocoding existed, run `python scripts/geocode_incidents.py` (a dry run; add `--apply --backup FILE` to save).

### Tests

The entity resolution and geocoding rules are covered by tests:

```bash
pip install -r requirements-dev.txt
python -m pytest tests
```

## RAG (semantic chat)

- **pgvector** — embeddings live in Postgres beside the incidents, in a `source_chunks` table with an HNSW cosine index. One datastore, no separate vector service.
- **Live joins** — search joins back to incidents, so category, severity and risk are always current rather than copied in at index time
- **FastEmbed** — local embeddings, no API key. The model is downloaded at build time (`scripts/prefetch_model.py`) so cold starts load it from disk in well under a second
- **Incremental indexing** — each chunk stores a content hash; a reindex embeds only what changed
- **Similarity floor** — chunks below `RAG_MIN_SIMILARITY` (0.60, calibrated on live queries) are not cited, so off-topic questions get "nothing found" instead of irrelevant sources
- **Keyword fallback** — used only when semantic search is unavailable (SQLite, empty index, query error); the response says which path answered
- **OpenAI** — optional richer answers when `OPENAI_API_KEY` is set

Reindex:

```bash
curl -X POST http://127.0.0.1:8000/api/rag/reindex
```

Useful `.env` knobs:

```bash
RAG_CHUNK_CHARS=900
RAG_CHUNK_OVERLAP_CHARS=150
RAG_MIN_SIMILARITY=0.60
USE_OPENAI_EMBEDDINGS=false
OPENAI_API_KEY=
```

## LangGraph agents

Click **Investigate** on an incident, or:

```bash
curl -X POST http://127.0.0.1:8000/api/agents/investigate/inc-001
```

Pipeline: **Research → Verification → Prediction → Strategy → Report**

- Uses the incident's own sources, plus semantically similar *other* incidents as context
- With `OPENAI_API_KEY`: LLM-generated step outputs and executive brief
- Without key: rule-based fallbacks grounded in incident data
- Persists agent runs and an executive report per investigation

## Risk scoring (heuristic)

The ingestion pipeline scores each incident with `sentinel-heuristic-risk-v1`. It applies a logistic function to keyword densities (urgency, infrastructure, exposure), a category prior and source credibility — but the weights are **hand-tuned, not learned**, so it is a heuristic rather than a trained model, and its `confidence` output is not a calibrated probability. The dashboard labels it "signal strength" for that reason. Training it needs a labelled evaluation set; see the roadmap.

It returns:

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
- `DATABASE_URL` — Postgres with pgvector enables semantic search; SQLite falls back to keywords

## Backend capabilities

- SQLAlchemy models: incidents, sources, timeline, agent runs, reports
- Tables created and seeded on first request
- New incidents embedded automatically after each ingest
- Heuristic risk scoring with per-feature explanations
- LangGraph multi-agent investigations
- Manual, mock, GNews, and NewsAPI ingestion
- Incident-level executive report generation
