"""Prometheus implementation of the domain MetricsPort. Exposed on /metrics (default registry)."""

from __future__ import annotations

from collections import Counter as Tally

from prometheus_client import Counter

PHANTOM_EVENTS = Counter(
    "ingest_phantom_events_total",
    "rule_match events published to the broker whose DB commit then failed (docs/adr/0001)",
    ["rule_id"],
)


class PrometheusMetrics:
    def phantom_events(self, rule_ids: list[str]) -> None:
        for rule_id, count in Tally(rule_ids).items():
            PHANTOM_EVENTS.labels(rule_id=rule_id).inc(count)
