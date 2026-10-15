from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from alert_service.api.app import create_app
from alert_service.domain.models import RuleMatch, RuleParams
from alert_service.domain.service import AlertService
from alert_service.domain.watermark import Watermark
from alert_service.tests.fakes import T0, FakeClock, InMemoryAlertRepository

PARAMS = RuleParams(min_duration_s=10, max_gap_s=3, resolve_after_s=60, severity="HIGH")


async def ok():
    return None


async def down():
    raise ConnectionError("db down")


@pytest.fixture
def client():
    clock = FakeClock(T0)
    service = AlertService(InMemoryAlertRepository(), clock, Watermark(clock, 30, 30), 0.1)
    app = create_app()
    app.state.service = service
    app.state.health_checks = {"database": ok, "redis": ok}
    with TestClient(app) as client:
        for t in range(0, 11):
            client.portal.call(
                service.handle_match,
                RuleMatch(
                    f"obs-{t}",
                    "rule-ism",
                    f"sensor-0{t % 2}",
                    T0 + timedelta(seconds=t),
                    2437.0,
                    -40.0,
                    PARAMS,
                ),
            )
        yield client


def only_alert_id(client: TestClient) -> str:
    [alert] = client.get("/api/v1/alerts").json()
    return alert["alert_id"]


def test_list_and_filter_alerts(client):
    [alert] = client.get("/api/v1/alerts").json()
    assert alert["state"] == "OPEN"
    assert alert["first_seen"] == "2026-10-06T10:00:00Z"
    assert alert["sensor_ids"] == ["sensor-00", "sensor-01"]
    assert alert["occurrences"] == 11
    assert len(client.get("/api/v1/alerts?state=OPEN&severity=HIGH").json()) == 1
    assert len(client.get("/api/v1/alerts?sensor_id=sensor-01").json()) == 1
    assert client.get("/api/v1/alerts?state=RESOLVED").json() == []
    assert client.get("/api/v1/alerts?min_frequency_mhz=2440").json() == []
    assert len(client.get("/api/v1/alerts?min_frequency_mhz=2400&max_frequency_mhz=2483.5").json())


def test_alert_details_include_contributing_observations(client):
    alert_id = only_alert_id(client)
    body = client.get(f"/api/v1/alerts/{alert_id}").json()
    assert len(body["observations"]) == 11
    assert body["observations"][0]["observation_id"] == "obs-0"


def test_ack_and_resolve_flow(client):
    alert_id = only_alert_id(client)
    assert client.post(f"/api/v1/alerts/{alert_id}/ack").json()["state"] == "ACKNOWLEDGED"
    again = client.post(f"/api/v1/alerts/{alert_id}/ack")
    assert again.status_code == 200  # idempotent
    resolved = client.post(f"/api/v1/alerts/{alert_id}/resolve").json()
    assert resolved["state"] == "RESOLVED"
    assert resolved["resolved_at"] is not None

    conflict = client.post(f"/api/v1/alerts/{alert_id}/ack")
    assert conflict.status_code == 409
    assert conflict.json()["error"] == "invalid_transition"
    assert client.post(f"/api/v1/alerts/{alert_id}/resolve").status_code == 409


def test_errors_use_the_uniform_body(client):
    missing = client.get("/api/v1/alerts/alrt-missing")
    assert missing.status_code == 404
    assert missing.json() == {
        "error": "not_found",
        "message": "alert alrt-missing not found",
        "details": [],
    }
    invalid = client.get("/api/v1/alerts?state=BROKEN")
    assert invalid.status_code == 422
    assert invalid.json()["error"] == "validation_error"
    assert invalid.json()["details"][0]["loc"] == ["query", "state"]


def test_health_reports_503_when_a_dependency_is_down(client):
    assert client.get("/health").json() == {
        "status": "ok",
        "checks": {"database": "ok", "redis": "ok"},
    }
    client.app.state.health_checks = {"database": down, "redis": ok}
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "error: ConnectionError"


def test_metrics_endpoint(client):
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "late_observations_dropped_total" in response.text


def test_inverted_frequency_range_is_rejected(client):
    response = client.get("/api/v1/alerts?min_frequency_mhz=2500&max_frequency_mhz=2400")
    assert response.status_code == 422
    assert response.json()["error"] == "validation_error"
