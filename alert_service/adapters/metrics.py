from prometheus_client import Counter, Gauge

RULE_MATCHES = Counter(
    "rule_matches_processed_total",
    "rule_match events processed, by outcome",
    ["outcome"],
)
LATE_DROPPED = Counter(
    "late_observations_dropped_total",
    "rule_match events older than the watermark (no effect on alert state)",
)
INVALID_EVENTS = Counter(
    "invalid_events_total",
    "rule_match events that could not be parsed (acknowledged and skipped)",
)
ALERTS_RESOLVED = Counter(
    "alerts_resolved_total",
    "Alerts resolved, by reason",
    ["reason"],
)
PENDING_RUNS = Gauge("pending_runs", "Signals with a pending run (not yet min_duration_s)")
