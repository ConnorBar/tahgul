# syntax=docker/dockerfile:1

# --- Web app -----------------------------------------------------------------
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# --- API (serves the web app too) ---------------------------------------------
FROM python:3.12-slim AS api
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd --system --uid 10001 --home /app ledger
WORKDIR /app

COPY api/pyproject.toml ./
COPY api/ledger ./ledger
RUN pip install .

COPY api/alembic.ini ./
COPY api/migrations ./migrations
COPY deploy/entrypoint.sh /entrypoint.sh
COPY --from=web /web/dist ./static

ENV LEDGER_STATIC_DIR=/app/static
USER ledger
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health').status==200 else 1)"
ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "ledger.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
