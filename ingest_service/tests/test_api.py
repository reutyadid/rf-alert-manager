import pytest
from fastapi.testclient import TestClient

from ingest_service.api.app import Services, create_app
from ingest_service.domain.errors import DependencyUnavailable
from ingest_service.domain.ingest import IngestService
from ingest_service.domain.rules import RuleService
from ingest_service.tests.builders import a_rule, observation_json, rule_json
from ingest_service.tests.fakes import (
    FakeObservationReader,
    FakePublisher,
    FakeRuleRepository,
    FakeSensorReader,
    FakeStore,
)


class Env:
    def __init__(self) -> None:
        self.store = FakeStore()
        self.publisher = FakePublisher()
        self.rules = FakeRuleRepository([a_rule()])
        self.healthy = {"database": True, "redis": True}

        def check(name):
            async def _check() -> None:
                if not self.healthy[name]:
                    raise DependencyUnavailable(f"{name} down")

            return _check

        services = Services(
            ingest=IngestService(self.rules, self.store.uow, self.publisher, default_max_gap_s=3),
            rules=RuleService(self.rules),
            observations=FakeObservationReader(self.store),
            sensors=FakeSensorReader(self.store),
            health_checks={"database": check("database"), "redis": check("redis")},
        )
        self.client = TestClient(create_app(services), raise_server_exceptions=False)


@pytest.fixture
def env():
    return Env()


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    body = response.json()
    assert body["error"] == code
    assert set(body) == {"error", "message", "details"}


# --- observations ---------------------------------------------------------


def test_post_single_observation(env):
    r = env.client.post("/api/v1/observations", json=observation_json())
    assert r.status_code == 200
    assert r.json() == {"accepted": 1, "duplicates": 0}
    assert len(env.publisher.events) == 1


def test_post_batch_and_duplicates(env):
    batch = [observation_json(), observation_json(sensor_id="sensor-04")]
    assert env.client.post("/api/v1/observations", json=batch).json() == {
        "accepted": 2,
        "duplicates": 0,
    }
    assert env.client.post("/api/v1/observations", json=batch).json() == {
        "accepted": 0,
        "duplicates": 2,
    }
    assert len(env.publisher.events) == 2


def test_invalid_batch_stores_nothing(env):
    batch = [observation_json(), observation_json(power_dbm=99)]
    r = env.client.post("/api/v1/observations", json=batch)
    assert_error(r, 422, "validation_error")
    assert r.json()["details"][0]["index"] == 1
    assert env.store.observations == {}
    assert env.publisher.events == []


def test_malformed_json_is_uniform_error(env):
    r = env.client.post(
        "/api/v1/observations", content="{not json", headers={"content-type": "application/json"}
    )
    assert_error(r, 422, "validation_error")


def test_broker_down_returns_503_and_stores_nothing(env):
    env.publisher.fail = True
    r = env.client.post("/api/v1/observations", json=observation_json())
    assert_error(r, 503, "unavailable")
    assert env.store.observations == {}


def test_query_observations(env):
    env.client.post(
        "/api/v1/observations",
        json=[
            observation_json(),
            observation_json(sensor_id="sensor-04", frequency_mhz=900.0),
            observation_json(timestamp="2026-10-06T11:00:00Z"),
        ],
    )
    q = env.client.get(
        "/api/v1/observations",
        params={
            "sensor_id": "sensor-03",
            "min_frequency_mhz": 2400,
            "max_frequency_mhz": 2500,
            "from": "2026-10-06T10:00:00Z",
            "to": "2026-10-06T10:30:00Z",
        },
    )
    assert q.status_code == 200
    rows = q.json()
    assert len(rows) == 1
    assert rows[0]["timestamp"] == "2026-10-06T10:15:30Z"
    assert rows[0]["location"] == {"lat": 32.08, "lon": 34.78}
    assert "observation_id" in rows[0]


def test_query_rejects_naive_time_and_bad_limit(env):
    r = env.client.get("/api/v1/observations", params={"from": "2026-10-06T10:00:00"})
    assert_error(r, 422, "validation_error")
    assert_error(env.client.get("/api/v1/observations?limit=0"), 422, "validation_error")


def test_sensors_last_seen(env):
    env.client.post(
        "/api/v1/observations",
        json=[observation_json(), observation_json(timestamp="2026-10-06T10:20:00Z")],
    )
    assert env.client.get("/api/v1/sensors").json() == [
        {"sensor_id": "sensor-03", "last_seen": "2026-10-06T10:20:00Z"}
    ]


# --- rules ----------------------------------------------------------------


def test_list_rules(env):
    rules = env.client.get("/api/v1/rules").json()
    assert [r["rule_id"] for r in rules] == ["rule-ism-high-power"]
    assert rules[0]["band"] == {"min_mhz": 2400.0, "max_mhz": 2483.5}


def test_create_rule(env):
    r = env.client.post("/api/v1/rules", json=rule_json(rule_id="new", max_gap_s=5))
    assert r.status_code == 201
    assert r.json()["max_gap_s"] == 5
    assert "new" in env.rules.rules


def test_create_duplicate_rule_conflicts(env):
    assert_error(env.client.post("/api/v1/rules", json=rule_json()), 409, "conflict")


@pytest.mark.parametrize(
    "change",
    [
        {"type": "magic"},
        {"band": {"min_mhz": 2500, "max_mhz": 2400}},
        {"severity": "PANIC"},
        {"resolve_after_s": 0},
        {"min_duration_s": -1},
        {"max_gap_s": 0},
        {"rule_id": ""},
    ],
)
def test_create_invalid_rule(env, change):
    body = rule_json(rule_id="x")
    body.update(change)
    r = env.client.post("/api/v1/rules", json=body)
    assert_error(r, 422, "validation_error")


def test_update_rule_disable(env):
    body = rule_json(enabled=False)
    del body["rule_id"]
    r = env.client.put("/api/v1/rules/rule-ism-high-power", json=body)
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    assert env.rules.rules["rule-ism-high-power"].enabled is False


def test_update_missing_rule(env):
    r = env.client.put("/api/v1/rules/nope", json=rule_json(rule_id="nope"))
    assert_error(r, 404, "not_found")


def test_update_rule_id_mismatch(env):
    r = env.client.put("/api/v1/rules/rule-ism-high-power", json=rule_json(rule_id="other"))
    assert_error(r, 422, "validation_error")


def test_delete_rule(env):
    assert env.client.delete("/api/v1/rules/rule-ism-high-power").status_code == 204
    assert_error(env.client.delete("/api/v1/rules/rule-ism-high-power"), 404, "not_found")


def test_disabled_rule_stops_publishing(env):
    body = rule_json(enabled=False)
    env.client.put("/api/v1/rules/rule-ism-high-power", json=body)
    env.client.post("/api/v1/observations", json=observation_json())
    assert env.publisher.events == []


# --- health and metrics ---------------------------------------------------


def test_health_ok(env):
    r = env.client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}


def test_health_503_when_dependency_down(env):
    env.healthy["redis"] = False
    r = env.client.get("/health")
    assert r.status_code == 503
    assert r.json()["checks"] == {"database": "ok", "redis": "unavailable"}


def test_metrics(env):
    env.client.post("/api/v1/observations", json=observation_json())
    text = env.client.get("/metrics").text
    assert "ingest_observations_received_total" in text
    assert "ingest_rule_matches_published_total" in text
    assert "http_request_duration_seconds" in text
