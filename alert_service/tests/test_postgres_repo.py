"""Integration test for the Postgres adapter. Runs only when ALERT_TEST_DATABASE_URL is set,
e.g. postgresql://postgres:test@localhost:55432/postgres (a throwaway database)."""

import os
from datetime import timedelta

import pytest

from alert_service.domain.models import Alert, AlertFilter, AlertState, Contribution
from alert_service.tests.fakes import T0

DATABASE_URL = os.environ.get("ALERT_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.integration,
    pytest.mark.skipif(not DATABASE_URL, reason="ALERT_TEST_DATABASE_URL not set"),
]


@pytest.fixture
async def repo():
    import asyncpg

    from alert_service.adapters.postgres_repo import PostgresAlertRepository, apply_schema

    pool = await asyncpg.create_pool(DATABASE_URL)
    await pool.execute("DROP TABLE IF EXISTS alert_notifications, alert_observations, alerts")
    await apply_schema(pool)
    await apply_schema(pool)  # idempotent
    yield PostgresAlertRepository(pool)
    await pool.close()


def contribution(i: int, sensor: str = "sensor-01") -> Contribution:
    return Contribution(f"obs-{i}", sensor, T0 + timedelta(seconds=i), 2437.0, -40.0 - i)


def alert(alert_id: str = "alrt-1", signal_key: str = "rule-ism@2437.0") -> Alert:
    return Alert(
        alert_id=alert_id,
        rule_id="rule-ism",
        signal_key=signal_key,
        state=AlertState.OPEN,
        severity="HIGH",
        frequency_mhz=2437.0,
        sensor_ids=["sensor-01"],
        first_seen=T0,
        last_seen=T0 + timedelta(seconds=1),
        occurrences=2,
        peak_power_dbm=-40.0,
        resolve_after_s=60,
    )


async def test_create_get_and_contributions(repo):
    await repo.create(alert(), [contribution(0), contribution(1)])
    stored = await repo.get("alrt-1")
    assert stored == alert()
    assert await repo.get_active_by_signal_key("rule-ism@2437.0") == alert()
    assert await repo.has_contribution("alrt-1", "obs-1")
    assert not await repo.has_contribution("alrt-1", "obs-9")
    assert [c.observation_id for c in await repo.list_contributions("alrt-1")] == [
        "obs-0",
        "obs-1",
    ]


async def test_add_contribution_updates_aggregates_but_not_state(repo):
    await repo.create(alert(), [contribution(0)])
    await repo.transition("alrt-1", {AlertState.OPEN}, AlertState.ACKNOWLEDGED, T0)
    updated = alert()
    updated.add(contribution(5, sensor="sensor-02"))  # the in-memory copy is still OPEN
    await repo.add_contribution(updated, contribution(5, sensor="sensor-02"))
    await repo.add_contribution(updated, contribution(5, sensor="sensor-02"))  # no error
    stored = await repo.get("alrt-1")
    assert stored.state is AlertState.ACKNOWLEDGED
    assert stored.occurrences == 3
    assert stored.sensor_ids == ["sensor-01", "sensor-02"]
    assert stored.last_seen == T0 + timedelta(seconds=5)


async def test_transition_is_conditional(repo):
    await repo.create(alert(), [])
    resolved = await repo.transition(
        "alrt-1", AlertState.active(), AlertState.RESOLVED, T0 + timedelta(seconds=9)
    )
    assert resolved.state is AlertState.RESOLVED
    assert resolved.resolved_at == T0 + timedelta(seconds=9)
    assert await repo.transition("alrt-1", {AlertState.OPEN}, AlertState.ACKNOWLEDGED, T0) is None
    assert await repo.get_active_by_signal_key("rule-ism@2437.0") is None
    assert await repo.list_active() == []


async def test_only_one_active_alert_per_signal(repo):
    import asyncpg

    await repo.create(alert("alrt-1"), [])
    with pytest.raises(asyncpg.UniqueViolationError):
        await repo.create(alert("alrt-2"), [])
    await repo.transition("alrt-1", AlertState.active(), AlertState.RESOLVED, T0)
    await repo.create(alert("alrt-2"), [])  # allowed once the first one is resolved


async def test_query_filters(repo):
    await repo.create(alert("alrt-1"), [])
    other = alert("alrt-2", signal_key="rule-ism@100.0")
    other.frequency_mhz, other.severity, other.sensor_ids = 100.0, "LOW", ["sensor-09"]
    await repo.create(other, [])

    async def ids(**kw):
        return sorted(a.alert_id for a in await repo.query(AlertFilter(**kw)))

    assert await ids() == ["alrt-1", "alrt-2"]
    assert await ids(severity="LOW") == ["alrt-2"]
    assert await ids(sensor_id="sensor-01") == ["alrt-1"]
    assert await ids(min_frequency_mhz=2000, max_frequency_mhz=2500) == ["alrt-1"]
    assert await ids(state=AlertState.RESOLVED) == []
    assert len(await repo.query(AlertFilter(limit=1))) == 1
