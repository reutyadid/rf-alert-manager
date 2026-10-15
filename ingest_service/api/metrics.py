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
    response = await call_next(request)
    route = request.scope.get("route")
    # Use the route template, not the raw path, to keep label cardinality bounded.
    path = getattr(route, "path", "unmatched")
    REQUEST_DURATION.labels(request.method, path, str(response.status_code)).observe(
        time.perf_counter() - start
    )
    return response


def metrics_response() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
