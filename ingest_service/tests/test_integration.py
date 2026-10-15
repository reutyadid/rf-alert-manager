"""Integration tests against real PostgreSQL and Redis.

Skipped unless both are configured, e.g.:
  INGEST_IT_DATABASE_URL=postgresql://postgres:test@localhost:5432/postgres
  INGEST_IT_REDIS_URL=redis://localhost:6379/15
The tests drop and recreate the ingest tables and the rule_matches stream: use a throwaway DB.
"""

import asyncio
import json
import os

import asyncpg
import pytest
from fastapi.testclient import TestClient
from redis.asyncio import Redis

from ingest_service.adapters import postgres, redis_publisher
from ingest_service.main import Settings, build_app
from ingest_service.tests.builders import a_rule, an_observation, observation_json, rule_json

DB_URL = os.environ.get("INGEST_IT_DATABASE_URL")
REDIS_URL = os.environ.get("INGEST_IT_REDIS_URL")

pytestmark = pytest.mark.skipif(
    not (DB_URL and REDIS_URL), reason="INGEST_IT_DATABASE_URL / INGEST_IT_REDIS_URL not set"
)


async def _reset() -> None:
    conn = await asyncpg.connect(DB_URL)
    await conn.execute("DROP TABLE IF EXISTS observations, sensors, rules")
    await conn.close()
    redis = Redis.from_url(REDIS_URL)
    await redis.delete(redis_publisher.STREAM)
    await redis.aclose()


async def _stream() -> list[dict]:
    redis = Redis.from_url(REDIS_URL)
    entries = await redis.xrange(redis_publisher.STREAM)
    await redis.aclose()
    return [json.loads(fields[b"data"]) for _, fields in entries]


async def _count(sql: str) -> int:
    conn = await asyncpg.connect(DB_URL)
    try:
        return await conn.fetchval(sql)
    finally:
        await conn.close()


@pytest.fixture
def client():
    asyncio.run(_reset())
    settings = Settings(database_url=DB_URL, redis_url=REDIS_URL, startup_retries=1)
    with TestClient(build_app(settings)) as c:  # runs the real lifespan
        yield c


def test_seeded_rules_and_health(client):
    assert client.get("/health").status_code == 200
    ids = {r["rule_id"] for r in client.get("/api/v1/rules").json()}
    assert "rule-ism-high-power" in ids


def test_ingest_stores_publishes_and_dedupes(client):
    batch = [
        observation_json(),
        observation_json(timestamp="2026-10-06T13:15:31+03:00"),
        observation_json(power_dbm=-90.0, timestamp="2026-10-06T10:15:32Z"),  # weak
    ]
    assert client.post("/api/v1/observations", json=batch).json() == {
        "accepted": 3,
        "duplicates": 0,
    }
    events = asyncio.run(_stream())
    assert [e["timestamp"] for e in events] == ["2026-10-06T10:15:30Z", "2026-10-06T10:15:31Z"]
    assert events[0]["rule"] == {
        "min_duration_s": 10.0,
        "max_gap_s": 3.0,
        "resolve_after_s": 60.0,
        "severity": "HIGH",
    }

    # Same batch again: nothing new stored or published.
    assert client.post("/api/v1/observations", json=batch).json() == {
        "accepted": 0,
        "duplicates": 3,
    }
    assert len(asyncio.run(_stream())) == 2
    assert asyncio.run(_count("SELECT count(*) FROM observations")) == 3

    rows = client.get("/api/v1/observations", params={"sensor_id": "sensor-03"}).json()
    assert len(rows) == 3
    assert client.get("/api/v1/sensors").json() == [
        {"sensor_id": "sensor-03", "last_seen": "2026-10-06T10:15:32Z"}
    ]


def test_rules_crud_persists(client):
    assert client.post("/api/v1/rules", json=rule_json(rule_id="it-rule")).status_code == 201
    body = rule_json(rule_id="it-rule", enabled=False, max_gap_s=7)
    assert client.put("/api/v1/rules/it-rule", json=body).json()["max_gap_s"] == 7
    assert client.delete("/api/v1/rules/it-rule").status_code == 204
    assert client.delete("/api/v1/rules/it-rule").status_code == 404


def test_seed_skipped_when_table_not_empty():
    async def scenario() -> int:
        await _reset()
        pool = await asyncpg.create_pool(DB_URL)
        await postgres.init_schema(pool)
        repo = postgres.PostgresRuleRepository(pool)

        await repo.create(a_rule(rule_id="operator-made"))
        seeded = await repo.seed_if_empty([a_rule()])
        await pool.close()
        return seeded

    assert asyncio.run(scenario()) == 0


def test_redis_down_rolls_back(client, monkeypatch):
    async def broken(self, events):
        raise redis_publisher.DependencyUnavailable("redis: simulated")

    monkeypatch.setattr(redis_publisher.RedisStreamPublisher, "publish", broken)
    r = client.post("/api/v1/observations", json=observation_json())
    assert r.status_code == 503
    assert asyncio.run(_count("SELECT count(*) FROM observations")) == 0
    assert asyncio.run(_count("SELECT count(*) FROM sensors")) == 0


class _RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def publish(self, events: list[dict]) -> None:
        self.events.extend(events)


class _CommitFailsOnNetwork:
    """Wraps an asyncpg transaction so COMMIT fails like a dropped connection."""

    def __init__(self, tx) -> None:
        self._tx = tx

    async def commit(self) -> None:
        raise ConnectionResetError("simulated network error during COMMIT")

    async def rollback(self) -> None:
        await self._tx.rollback()


def test_postgres_uow_state_flags():
    async def scenario() -> None:
        await _reset()
        pool = await asyncpg.create_pool(DB_URL)
        await postgres.init_schema(pool)
        publisher = _RecordingPublisher()
        obs = an_observation()

        # COMMIT fails after a successful publish: published, not committed, nothing stored.
        uow = postgres.PostgresUnitOfWork(pool, publisher)
        with pytest.raises(redis_publisher.DependencyUnavailable):
            async with uow:
                await uow.insert_observations([obs])
                uow.add_events([{"observation_id": obs.observation_id}])
                uow._tx = _CommitFailsOnNetwork(uow._tx)
                await uow.commit()
        assert (uow.published, uow.committed, uow.stage) == (True, False, "commit")
        assert await pool.fetchval("SELECT count(*) FROM observations") == 0

        # Happy path: both flags set, row stored.
        uow = postgres.PostgresUnitOfWork(pool, publisher)
        async with uow:
            await uow.insert_observations([obs])
            uow.add_events([{"observation_id": obs.observation_id}])
            await uow.commit()
        assert (uow.published, uow.committed, uow.stage) == (True, True, None)
        assert await pool.fetchval("SELECT count(*) FROM observations") == 1
        assert len(publisher.events) == 2
        await pool.close()

    asyncio.run(scenario())
