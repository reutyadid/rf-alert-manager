from prometheus_client import Counter, Gauge

from alert_service.domain.models import AlertState

RULE_MATCHES = Counter(
    "rule_matches_processed_total",
    "rule_match events processed, by outcome",
    ["outcome"],
)
LATE_DROPPED = Counter(
    "late_observations_dropped_total",
    "rule_match events older than the watermark (no effect on alert state)",
)
FUTURE_DROPPED = Counter(
    "future_observations_dropped_total",
    "rule_match events stamped more than MAX_CLOCK_SKEW_S ahead of the server clock",
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
FAILED_EVENTS = Counter(
    "failed_events_total",
    "rule_match events acked after failing max_attempts times with a non-transient error",
)
ALERTS_OPENED = Counter("alerts_opened_total", "Alerts opened, by severity", ["severity"])
OPEN_ALERTS = Gauge(
    "open_alerts",
    "Alerts currently OPEN or ACKNOWLEDGED, by state and severity (refreshed by the sweep)",
    ["state", "severity"],
)
_open_alert_labels: set[tuple[str, str]] = set()


def set_open_alerts(counts: dict[tuple[AlertState, str], int]) -> None:
    """Set the gauge from current counts. A label set that is no longer present goes to 0
    instead of keeping its last value."""
    current = {(str(state), severity): n for (state, severity), n in counts.items()}
    for labels in _open_alert_labels - current.keys():
        OPEN_ALERTS.labels(*labels).set(0)
    for labels, n in current.items():
        OPEN_ALERTS.labels(*labels).set(n)
    _open_alert_labels.update(current)
