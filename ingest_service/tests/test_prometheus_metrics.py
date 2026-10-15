from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from ingest_service.adapters.prometheus_metrics import PrometheusMetrics
from ingest_service.api.app import create_app


def _phantoms(rule_id: str) -> float:
    return REGISTRY.get_sample_value("ingest_phantom_events_total", {"rule_id": rule_id}) or 0.0


def test_phantom_counter_per_rule_and_exposed_on_metrics_endpoint():
    before_a, before_b = _phantoms("rule-a"), _phantoms("rule-b")

    PrometheusMetrics().phantom_events(["rule-a", "rule-a", "rule-b"])

    assert _phantoms("rule-a") - before_a == 2
    assert _phantoms("rule-b") - before_b == 1
    text = TestClient(create_app()).get("/metrics").text
    assert 'ingest_phantom_events_total{rule_id="rule-a"}' in text
