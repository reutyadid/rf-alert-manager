"""Integration test for the Redis consumer. Runs only when ALERT_TEST_REDIS_URL is set,
e.g. redis://localhost:56379/15 (the stream used here is deleted first)."""

import json
import os
from datetime import timedelta

import pytest

from alert_service.domain.models import AlertFilter
from alert_service.domain.service import AlertService
from alert_service.domain.watermark import Watermark
from alert_service.tests.fakes import T0, FakeClock, InMemoryAlertRepository

REDIS_URL = os.environ.get("ALERT_TEST_REDIS_URL")
STREAM = "test_rule_matches"

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(not REDIS_URL, reason="ALERT_TEST_REDIS_URL not set"),
]


def payload(t: int, obs_id: str | None = None) -> str:
    return json.dumps(
        {
            "observation_id": obs_id or f"obs-{t}",
            "rule_id": "rule-ism",
            "sensor_id": "sensor-01",
            "timestamp": (T0 + timedelta(seconds=t)).isoformat().replace("+00:00", "Z"),
            "frequency_mhz": 2437.0,
            "power_dbm": -40.0,
            "rule": {
                "min_duration_s": 10,
                "max_gap_s": 3,
                "resolve_after_s": 60,
                "severity": "HIGH",
            },
        }
    )


@pytest.fixture
async def setup():
    import redis.asyncio as redis

    from alert_service.adapters.redis_consumer import RedisMatchConsumer

    client = redis.from_url(REDIS_URL, decode_responses=True)
    await client.delete(STREAM)
    clock = FakeClock(T0)
    repo = InMemoryAlertRepository()
    service = AlertService(repo, clock, Watermark(clock, 30, 30), 0.1, max_clock_skew_s=60)
    consumer = RedisMatchConsumer(client, service, stream=STREAM, block_ms=100)
    await consumer.ensure_group()
    await consumer.ensure_group()  # idempotent
    yield client, service, consumer
    await client.delete(STREAM)
    await client.aclose()


async def test_consumes_opens_alert_and_acks(setup):
    client, service, consumer = setup
    for t in range(0, 11):
        await client.xadd(STREAM, {"data": payload(t)})
    await client.xadd(STREAM, {"data": "garbage"})
    await client.xadd(STREAM, {"data": payload(10)})  # redelivered observation

    assert await consumer.poll_once() == 13
    [alert] = await service.list_alerts(AlertFilter())
    assert alert.occurrences == 11
    pending = await client.xpending(STREAM, "alert-service")
    assert pending["pending"] == 0  # everything acked, including the invalid event


async def test_unacked_entries_are_redelivered_after_restart(setup):
    client, service, consumer = setup
    await client.xadd(STREAM, {"data": payload(0)})
    # Simulate a crash after reading but before acking: read without processing.
    await client.xreadgroup("alert-service", "alert-service", {STREAM: ">"}, count=10)
    assert (await client.xpending(STREAM, "alert-service"))["pending"] == 1

    assert await consumer.poll_once("0") == 1  # startup reads own pending entries first
    assert (await client.xpending(STREAM, "alert-service"))["pending"] == 0
    assert service.pending_run_count == 1
