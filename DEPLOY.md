# Deploy SentinelAI (Vercel + Render)

## 1. Backend on Render (~10 min)

1. Open https://dashboard.render.com/select-repo?type=blueprint
2. Connect **DWilson32/SentinelAI** (branch `main`)
3. Render reads `render.yaml` and creates:
   - **sentinel-ai-api** (Python web service)
   - **sentinel-db** (PostgreSQL)
4. The blueprint sets:

| Key | Value |
|-----|--------|
| `DATABASE_URL` | Render Postgres connection string |
| `ALLOWED_ORIGINS` | `https://frontend-rho-five-98.vercel.app` |

5. Optional: after create, set extra **Environment** values on `sentinel-ai-api`:

| Key | Value |
|-----|--------|
| `OPENAI_API_KEY` | Optional, for LLM chat/agents |

6. Wait for deploy; copy API URL e.g. `https://sentinel-ai-api.onrender.com`
7. Verify: `https://sentinel-ai-api.onrender.com/health`

## 2. Frontend on Vercel

**Production URL:** https://frontend-rho-five-98.vercel.app

Set environment variable in [Vercel Project Settings](https://vercel.com/shre4esh-6075s-projects/frontend/settings/environment-variables):

| Name | Value |
|------|--------|
| `NEXT_PUBLIC_API_BASE_URL` | `https://sentinel-ai-api.onrender.com/api` |

Redeploy after saving (Deployments → Redeploy).

**CLI:**

```bash
cd frontend
npx vercel env add NEXT_PUBLIC_API_BASE_URL production
# paste: https://sentinel-ai-api.onrender.com/api
npx vercel deploy --prod
```

## 3. Post-deploy

```bash
curl -X POST https://sentinel-ai-api.onrender.com/api/rag/reindex
```

## Repo

https://github.com/DWilson32/SentinelAI

## 4. Semantic search (pgvector)

Vectors live in the same Postgres database as everything else. On first request
the app creates the `vector` extension, the `source_chunks` table and its HNSW
index. New incidents are embedded after each ingest; to embed what is already
there, run once:

```bash
curl -X POST -H "X-API-Key: $SENTINEL_ADMIN_KEY" \
  https://sentinel-ai-api.onrender.com/api/rag/reindex
```

The build command must include `python scripts/prefetch_model.py`. Without it the
embedding model downloads at runtime, and on the free tier that repeats after
every spin-down — adding ~40 s to the first chat request each time.

## 5. Public feeds

| Feed | Needs |
|------|-------|
| USGS, GDACS, Google News | Nothing |
| GDELT | Nothing, but it rate-limits cloud IPs intermittently. After a failure the ingester skips it for 6 hours and uses Google News, saving ~12 s per sync; then it probes GDELT again. |
| ReliefWeb | An approved app name: request one at https://apidoc.reliefweb.int/parameters#appname, then set `RELIEFWEB_APPNAME` on the Render service. Until then it shows as **disabled**, not failing. |

`GET /api/feeds/status` reports the state of each.

## 6. Health checks and monitoring

| Endpoint | Purpose | Behaviour when the database is gone |
|----------|---------|--------------------------------------|
| `/health` | Liveness. Render polls this (`healthCheckPath`). | Still **200**, with `"database": "down"` in the body. Failing here would make Render restart the instance in a loop, which cannot revive an expired database. |
| `/health/deep` | Readiness. Point uptime monitoring here. | **503**, with the underlying error. |

Set up a free uptime monitor (UptimeRobot, Better Stack, Cron-job.org) against:

```
https://sentinel-ai-api.onrender.com/health/deep
```

Without this, an expired database is invisible: the liveness probe keeps
returning 200 while every data route 500s. That failure has gone unnoticed for
weeks at a time.
