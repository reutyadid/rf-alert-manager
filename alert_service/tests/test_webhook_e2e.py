"""End to end without Docker for the receiver: AlertService -> Postgres outbox -> Dispatcher ->
httpx sender -> a MockTransport receiver that fails once and then accepts. Runs only when
ALERT_TEST_DATABASE_URL is set (see test_postgres_repo.py)."""

import json
import os

import httpx
import pytest

from alert_service.domain.models import AlertFilter, AlertState
from alert_service.domain.notifications import Dispatcher
from alert_service.domain.service import AlertService
from alert_service.domain.watermark import Watermark
from alert_service.tests.fakes import T0, FakeClock
from alert_service.tests.test_alert_service import match
from alert_service.tests.test_webhook_sender import SECRET, URL, verify

DATABASE_URL = os.environ.get("ALERT_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.integration,
    pytest.mark.skipif(not DATABASE_URL, reason="ALERT_TEST_DATABASE_URL not set"),
]


@pytest.fixture
async def pool():
    import asyncpg

    from alert_service.adapters.postgres_repo import apply_schema

    pool = await asyncpg.create_pool(DATABASE_URL)
    await pool.execute("DROP TABLE IF EXISTS alert_notifications, alert_observations, alerts")
    await apply_schema(pool)
    yield pool
    await pool.close()


async def test_opened_is_retried_then_delivered_and_resolved_follows_in_order(pool):
    from alert_service.adapters.postgres_repo import PostgresAlertRepository, PostgresOutboxRepo
    from alert_service.adapters.webhook_sender import HttpWebhookSender

    received: list[httpx.Request] = []
    answers = [503]  # first request fails, the rest succeed

    def receiver(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(answers.pop(0) if answers else 200)

    clock = FakeClock(T0)
    service = AlertService(
        PostgresAlertRepository(pool),
        clock,
        Watermark(clock, allowed_lateness_s=30, idle_timeout_s=30),
        freq_tolerance_mhz=0.1,
        max_clock_skew_s=3600,
        notify=True,
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(receiver), follow_redirects=False)
    dispatcher = Dispatcher(
        PostgresOutboxRepo(pool), HttpWebhookSender(client, URL, SECRET, clock), clock
    )

    for t in range(0, 11):
        await service.handle_match(match(t))
    [alert] = await service.list_alerts(AlertFilter(state=AlertState.OPEN))
    await service.resolve(alert.alert_id)

    await dispatcher.dispatch_due()  # opened -> 503, resolved waits behind it
    assert len(received) == 1
    await dispatcher.dispatch_due()  # opened not due yet (backoff 2 s)
    assert len(received) == 1

    clock.advance(2)
    await dispatcher.dispatch_due()  # opened -> 200
    await dispatcher.dispatch_due()  # resolved -> 200
    bodies = [json.loads(r.content) for r in received]
    assert [(b["type"], b["resolution"]) for b in bodies] == [
        ("alert.opened", None),
        ("alert.opened", None),
        ("alert.resolved", "manual"),
    ]
    assert bodies[0]["id"] == bodies[1]["id"]  # same notification, retried
    assert bodies[0] == bodies[1]  # same body on retry
    assert bodies[2]["sequence"] > bodies[1]["sequence"]
    assert bodies[1]["alert"]["state"] == "OPEN"  # snapshot at open time
    assert bodies[2]["alert"]["state"] == "RESOLVED"
    assert all(verify(SECRET, r) for r in received)

    statuses = await pool.fetch("SELECT status, attempts FROM alert_notifications ORDER BY seq")
    assert [(r["status"], r["attempts"]) for r in statuses] == [("sent", 2), ("sent", 1)]
    assert await dispatcher.oldest_pending_age_s() == 0
    await client.aclose()
