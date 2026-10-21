"""Integration test for the webhook outbox in Postgres. Runs only when ALERT_TEST_DATABASE_URL
is set (see test_postgres_repo.py)."""

import os
from datetime import timedelta

import pytest

from alert_service.domain.models import AlertEvent, AlertEventType, AlertState
from alert_service.tests.fakes import T0
from alert_service.tests.test_postgres_repo import alert, contribution

DATABASE_URL = os.environ.get("ALERT_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.integration,
    pytest.mark.skipif(not DATABASE_URL, reason="ALERT_TEST_DATABASE_URL not set"),
]

OPENED = AlertEvent(AlertEventType.OPENED, T0)
RESOLVED = AlertEvent(AlertEventType.RESOLVED, T0 + timedelta(seconds=5), "manual")
LATER = T0 + timedelta(minutes=1)


@pytest.fixture
async def db():
    import asyncpg

    from alert_service.adapters.postgres_repo import (
        PostgresAlertRepository,
        PostgresOutboxRepo,
        apply_schema,
    )

    pool = await asyncpg.create_pool(DATABASE_URL)
    await pool.execute("DROP TABLE IF EXISTS alert_notifications, alert_observations, alerts")
    await apply_schema(pool)
    yield PostgresAlertRepository(pool), PostgresOutboxRepo(pool), pool
    await pool.close()


async def block_outbox_inserts(pool) -> None:
    """Make every outbox INSERT fail, to prove the alert change rolls back with it."""
    await pool.execute(
        "ALTER TABLE alert_notifications ADD CONSTRAINT block_inserts CHECK (false) NOT VALID"
    )


async def test_create_writes_alert_and_outbox_row(db):
    repo, outbox, _ = db
    await repo.create(alert(), [contribution(0)], OPENED)
    [row] = await outbox.fetch_due(LATER, limit=10)
    assert row.id.startswith("ntf-")
    assert (row.alert_id, row.type, row.occurred_at, row.resolution) == (
        "alrt-1",
        AlertEventType.OPENED,
        T0,
        None,
    )
    assert row.attempts == 0
    assert row.alert["alert_id"] == "alrt-1"
    assert row.alert["state"] == "OPEN"
    assert row.alert["first_seen"] == "2026-10-06T10:00:00Z"


async def test_create_without_event_writes_no_outbox_row(db):
    repo, outbox, _ = db
    await repo.create(alert(), [])
    assert await outbox.fetch_due(LATER, limit=10) == []


async def test_create_is_atomic_with_the_outbox_insert(db):
    repo, _, pool = db
    await block_outbox_inserts(pool)
    with pytest.raises(Exception, match="block_inserts"):
        await repo.create(alert(), [contribution(0)], OPENED)
    assert await repo.get("alrt-1") is None
    assert await repo.list_contributions("alrt-1") == []


async def test_transition_writes_snapshot_of_the_new_state(db):
    repo, outbox, _ = db
    await repo.create(alert(), [])
    await repo.transition(
        "alrt-1", AlertState.active(), AlertState.RESOLVED, RESOLVED.occurred_at, RESOLVED
    )
    [row] = await outbox.fetch_due(LATER, limit=10)
    assert (row.type, row.resolution) == (AlertEventType.RESOLVED, "manual")
    assert row.alert["state"] == "RESOLVED"
    assert row.alert["resolved_at"] == "2026-10-06T10:00:05Z"


async def test_transition_is_atomic_with_the_outbox_insert(db):
    repo, _, pool = db
    await repo.create(alert(), [])
    await block_outbox_inserts(pool)
    with pytest.raises(Exception, match="block_inserts"):
        await repo.transition("alrt-1", AlertState.active(), AlertState.RESOLVED, T0, RESOLVED)
    assert (await repo.get("alrt-1")).state is AlertState.OPEN


async def test_transition_that_loses_the_race_writes_no_outbox_row(db):
    repo, outbox, _ = db
    await repo.create(alert(), [])
    await repo.transition("alrt-1", AlertState.active(), AlertState.RESOLVED, T0)
    assert (
        await repo.transition("alrt-1", {AlertState.OPEN}, AlertState.RESOLVED, T0, RESOLVED)
        is None
    )
    assert await outbox.fetch_due(LATER, limit=10) == []


async def test_row_is_not_due_before_next_attempt_at(db):
    repo, outbox, _ = db
    await repo.create(alert(), [], OPENED)
    [row] = await outbox.fetch_due(LATER, limit=10)
    await outbox.schedule_retry(row.id, 1, LATER, 503, "HTTP 503")
    assert await outbox.fetch_due(LATER - timedelta(seconds=1), limit=10) == []
    [again] = await outbox.fetch_due(LATER, limit=10)
    assert again.attempts == 1


async def test_resolved_waits_for_opened_of_the_same_alert(db):
    repo, outbox, _ = db
    await repo.create(alert(), [], OPENED)
    await repo.transition("alrt-1", AlertState.active(), AlertState.RESOLVED, T0, RESOLVED)
    [opened] = await outbox.fetch_due(LATER, limit=10)
    assert opened.type is AlertEventType.OPENED

    # opened is waiting for a retry: resolved is still blocked behind it
    await outbox.schedule_retry(opened.id, 1, LATER + timedelta(hours=1), 503, "HTTP 503")
    assert await outbox.fetch_due(LATER, limit=10) == []

    await outbox.mark_sent(opened.id, 1, LATER, 200)
    [resolved] = await outbox.fetch_due(LATER, limit=10)
    assert resolved.type is AlertEventType.RESOLVED
    assert resolved.seq > opened.seq


async def test_dead_row_releases_the_next_one(db):
    repo, outbox, _ = db
    await repo.create(alert(), [], OPENED)
    await repo.transition("alrt-1", AlertState.active(), AlertState.RESOLVED, T0, RESOLVED)
    [opened] = await outbox.fetch_due(LATER, limit=10)
    await outbox.mark_dead(opened.id, 10, 404, "HTTP 404")
    [resolved] = await outbox.fetch_due(LATER, limit=10)
    assert resolved.type is AlertEventType.RESOLVED


async def test_other_alerts_are_not_blocked(db):
    repo, outbox, _ = db
    await repo.create(alert("alrt-1"), [], OPENED)
    await repo.create(alert("alrt-2", signal_key="rule-ism@100.0"), [], OPENED)
    rows = await outbox.fetch_due(LATER, limit=10)
    assert [r.alert_id for r in rows] == ["alrt-1", "alrt-2"]  # ordered by seq


async def test_oldest_pending(db):
    repo, outbox, _ = db
    assert await outbox.oldest_pending_occurred_at() is None
    await repo.create(alert(), [], OPENED)
    assert await outbox.oldest_pending_occurred_at() == T0
    [row] = await outbox.fetch_due(LATER, limit=10)
    await outbox.mark_sent(row.id, 1, LATER, 200)
    assert await outbox.oldest_pending_occurred_at() is None
