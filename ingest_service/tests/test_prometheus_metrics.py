from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from ingest_service.adapters.prometheus_metrics import PrometheusMetrics
from ingest_service.api.app import create_app


def _candidates(rule_id: str) -> float:
    return REGISTRY.get_sample_value("ingest_phantom_candidates_total", {"rule_id": rule_id}) or 0.0


def test_candidate_counter_per_rule_and_exposed_on_metrics_endpoint():
    before_a, before_b = _candidates("rule-a"), _candidates("rule-b")

    PrometheusMetrics().phantom_candidates(["rule-a", "rule-a", "rule-b"])

    assert _candidates("rule-a") - before_a == 2
    assert _candidates("rule-b") - before_b == 1
    text = TestClient(create_app()).get("/metrics").text
    assert 'ingest_phantom_candidates_total{rule_id="rule-a"}' in text
