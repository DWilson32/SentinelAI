# Four stages, so each image contains only what it needs:
#
#   deps     the dependency set, installed once into a virtualenv
#   test     + dev dependencies and the suite; the build fails if a test fails
#   build    + the embedding model, baked in
#   runtime  the final image: no pip, no tests, no build context, not root
#
# The model is baked in rather than fetched on first use because a free-tier host
# discards anything written at runtime on every spin-down, so a lazy download
# would repeat on each cold start (~40 s, and rate-limited without a token).

FROM python:3.12.8-slim AS deps

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app
RUN python -m venv /opt/venv

# Dependencies on their own layer, so editing application code does not reinstall them.
COPY requirements.txt ./
RUN pip install -r requirements.txt


# docker build --target test . runs the suite against this exact dependency set.
# The tests mock the embedding model, so this stage never downloads it.
FROM deps AS test

COPY requirements-dev.txt ./
RUN pip install -r requirements-dev.txt
COPY app ./app
COPY tests ./tests
# conftest.py pins the tests to in-memory SQLite; this only keeps settings importable.
ENV DATABASE_URL=sqlite://
RUN python -m pytest tests -q


FROM deps AS build

COPY app ./app
COPY scripts ./scripts
RUN python scripts/prefetch_model.py


FROM python:3.12.8-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8000

WORKDIR /app

COPY --from=build /opt/venv /opt/venv
COPY --from=build /app/.model_cache ./.model_cache
COPY app ./app
# The operational scripts (merges, geocoding, re-rating) run against a deployed
# database, so they ship with the image.
COPY scripts ./scripts

# The app only ever reads its own files, so it does not run as root.
RUN useradd --no-create-home --uid 10001 sentinel
USER sentinel

EXPOSE 8000

# The container reports its own health; a host may also poll /health itself.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ['PORT'] + '/health', timeout=4)"

# PORT is injected by the host; the default keeps `docker run -p 8000:8000` working.
CMD ["sh", "-c", "exec python -m uvicorn app.main:app --host 0.0.0.0 --port \"$PORT\""]
