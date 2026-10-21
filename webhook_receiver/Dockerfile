# Demo webhook receiver (compose profile "demo" only; never deployed by the pipeline).
# Standard library only, so a single stage is enough.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app

COPY receiver.py /app/receiver.py
WORKDIR /app

USER app

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2)" || exit 1

CMD ["python", "receiver.py"]
