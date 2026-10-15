"""Consumer error handling with a fake Redis: a message that always fails must not block the
stream, but a dependency outage must never cause an event to be acked unprocessed."""

import json

import pytest

from alert_service.adapters.redis_consumer import RedisMatchConsumer
from alert_service.tests.test_events import event

pytestmark = pytest.mark.anyio


class FakeRedis:
    def __init__(self, entries: list[tuple[str, dict]]) -> None:
        self.entries = entries
        self.acked: list[str] = []

    async def xreadgroup(self, group, consumer, streams, count, block):
        pending = [e for e in self.entries if e[0] not in self.acked]
        return [["rule_matches", pending]] if pending else []

    async def xack(self, stream, group, entry_id):
        self.acked.append(entry_id)


class FailingService:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.calls = 0
        self.pending_run_count = 0

    async def handle_match(self, match):
        self.calls += 1
        raise self.exc


def make(exc: Exception):
    fake = FakeRedis([("1-0", {"data": json.dumps(event())})])
    service = FailingService(exc)
    return fake, service, RedisMatchConsumer(fake, service, max_attempts=3)


async def test_deterministic_failure_is_acked_after_max_attempts():
    fake, service, consumer = make(ValueError("bug"))
    for _ in range(2):
        with pytest.raises(ValueError):
            await consumer.poll_once("0")
    assert fake.acked == []
    await consumer.poll_once("0")  # third failure: give up on this entry
    assert fake.acked == ["1-0"]
    assert service.calls == 3


async def test_dependency_outage_is_never_acked():
    fake, service, consumer = make(ConnectionError("db down"))
    for _ in range(10):
        with pytest.raises(ConnectionError):
            await consumer.poll_once("0")
    assert fake.acked == []
