"""Prometheus implementation of the domain MetricsPort. Exposed on /metrics (default registry)."""

from __future__ import annotations

from collections import Counter as Tally

from prometheus_client import Counter

# An upper bound, not confirmed phantoms (docs/adr/0001): most candidates heal when the client
# retries with the same uuid5 ids, and a COMMIT that failed on a network error may in fact
# have succeeded on the server.
PHANTOM_CANDIDATES = Counter(
    "ingest_phantom_candidates_total",
    "rule_match events published whose transaction did not commit (upper bound on phantoms; "
    "a COMMIT failing on a network error is ambiguous and may have succeeded)",
    ["rule_id"],
)


class PrometheusMetrics:
    def phantom_candidates(self, rule_ids: list[str]) -> None:
        for rule_id, count in Tally(rule_ids).items():
            PHANTOM_CANDIDATES.labels(rule_id=rule_id).inc(count)
