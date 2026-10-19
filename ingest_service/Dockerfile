# ingest-service image. Build context is this folder:
#   docker build -t ingest-service ingest_service/
# Configuration comes only from the environment (DATABASE_URL, REDIS_URL, ...); no secrets here.

# --- builder: install dependencies into a virtualenv ---------------------------------------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
COPY requirements.txt /tmp/requirements.txt
RUN /opt/venv/bin/pip install -r /tmp/requirements.txt

# --- runtime: only the virtualenv, the code and the seed rules ------------------------------
FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    RULES_FILE=/app/config/rules.yaml \
    PORT=8000

RUN useradd --system --uid 10001 --no-create-home app

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY config/rules.yaml /app/config/rules.yaml
COPY . /app/ingest_service/

# Code stays root-owned and read-only for the runtime user.
USER 10001

EXPOSE 8000

# Standard library only (no curl). /health returns 503 when DB or Redis is down -> exit 1.
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import os, sys, urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('PORT', '8000')}/health\", timeout=4); sys.exit(0)"]

# Exec form: the app is PID 1 and receives SIGTERM directly. Logs go to stdout.
CMD ["python", "-m", "ingest_service.main"]
