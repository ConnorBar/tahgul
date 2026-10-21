# Radicale CalDAV server for the stack (SPEC: calendar, "Self-hosted (recommended)").
FROM python:3.12-slim
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONUNBUFFERED=1
RUN pip install "radicale>=3.2,<4" "bcrypt>=4"
RUN useradd --system --uid 10002 --home /data radicale \
    && mkdir -p /data /config && chown radicale /data
COPY config /config/config
COPY entrypoint.sh /entrypoint.sh
USER radicale
VOLUME /data
EXPOSE 5232
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import socket; socket.create_connection(('127.0.0.1', 5232), 2)"
ENTRYPOINT ["/entrypoint.sh"]
