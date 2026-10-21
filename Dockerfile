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

# Pango and fonts for WeasyPrint (invoice PDFs).
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --system --uid 10001 --home /app tahgul \
 && mkdir -p /data && chown tahgul /data
WORKDIR /app

COPY api/pyproject.toml ./
COPY api/tahgul ./tahgul
RUN pip install .

COPY api/alembic.ini ./
COPY api/migrations ./migrations
COPY deploy/entrypoint.sh /entrypoint.sh
COPY --from=web /web/dist ./static

ENV TAHGUL_STATIC_DIR=/app/static \
    XDG_CACHE_HOME=/tmp/.cache \
    TAHGUL_DATA_DIR=/data
VOLUME /data
USER tahgul
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health').status==200 else 1)"
ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "tahgul.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
