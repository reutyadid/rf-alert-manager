"""Prometheus metrics for ingest-service."""

from __future__ import annotations

import time

from fastapi import Request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

OBSERVATIONS_RECEIVED = Counter(
    "ingest_observations_received_total", "Valid observations received (including duplicates)"
)
OBSERVATIONS_DUPLICATE = Counter(
    "ingest_observations_duplicate_total", "Observations skipped as duplicates"
)
BATCHES_REJECTED = Counter(
    "ingest_batches_rejected_total", "Requests rejected because an observation was invalid"
)
RULE_MATCHES_PUBLISHED = Counter(
    "ingest_rule_matches_published_total", "rule_match events published to the broker"
)
REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration",
    ["method", "route", "status"],
)


async def track_requests(request: Request, call_next):
    start = time.perf_counter()
    status = "500"  # an unhandled exception escapes call_next; record it as a 500
    try:
        response = await call_next(request)
        status = str(response.status_code)
        return response
    finally:
        route = request.scope.get("route")
        # Use the route template, not the raw path, to keep label cardinality bounded.
        path = getattr(route, "path", "unmatched")
        REQUEST_DURATION.labels(request.method, path, status).observe(time.perf_counter() - start)


def metrics_response() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
