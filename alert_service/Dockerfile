# alert-service image. Build context is this folder:
#   docker build -t alert-service alert_service
# All configuration comes from environment variables at runtime (see .env.example).

# --- builder: install runtime dependencies into a venv ---------------------------------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

# --- final: only the venv and the code -------------------------------------------------
FROM python:3.12-slim

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app

COPY --from=builder /opt/venv /opt/venv
# Owned by root, read-only for the app user.
COPY . /app/alert_service
WORKDIR /app

USER app

EXPOSE 8000

# Standard library only (no curl). /health returns 503 when DB, Redis or a background task
# is down, which makes urlopen raise and the check fail.
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8000\")}/health', timeout=3)" || exit 1

# One process, one uvicorn worker: alert-service is the single stateful consumer
# (pending runs and the watermark are in memory, DECISIONS §4.5). Never add workers.
# Exec form so Python is PID 1 and receives SIGTERM for a clean shutdown.
CMD ["python", "-m", "alert_service.main"]
