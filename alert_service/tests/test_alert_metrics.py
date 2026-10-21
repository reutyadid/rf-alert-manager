"""Metrics for the Grafana dashboard: alerts opened (counter) and open alerts (gauge)."""

import json

import pytest
from prometheus_client import REGISTRY

from alert_service.adapters import metrics
from alert_service.adapters.redis_consumer import RedisMatchConsumer
from alert_service.domain.models import AlertState, Delivered, MatchOutcome, Retryable, RuleParams
from alert_service.tests.test_alert_service import PARAMS, alerts, build, feed
from alert_service.tests.test_consumer_errors import FakeRedis
from alert_service.tests.test_events import event

pytestmark = pytest.mark.anyio


def sample(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


# --- domain: counts of active alerts ---------------------------------------------------


async def test_active_alert_counts_by_state_and_severity():
    service, _, _ = build()
    low = RuleParams(min_duration_s=10, max_gap_s=3, resolve_after_s=60, severity="LOW")
    await feed(service, range(0, 12), freq=2437.0)
    await feed(service, range(0, 12), freq=2450.0)
    await feed(service, range(0, 12), freq=5800.0, rule_id="rule-5ghz", params=low)
    first = next(a for a in await alerts(service) if a.frequency_mhz == 2437.0)
    await service.acknowledge(first.alert_id)

    assert await service.active_alert_counts() == {
        (AlertState.OPEN, "HIGH"): 1,
        (AlertState.ACKNOWLEDGED, "HIGH"): 1,
        (AlertState.OPEN, "LOW"): 1,
    }


async def test_resolved_alerts_are_not_counted():
    service, _, _ = build()
    await feed(service, range(0, 12))
    alert = (await alerts(service))[0]
    await service.resolve(alert.alert_id)

    assert await service.active_alert_counts() == {}


# --- adapter: gauge goes back to 0 instead of keeping the last value -------------------


def test_open_alerts_gauge_resets_labels_that_disappear():
    metrics.set_open_alerts({(AlertState.OPEN, "CRITICAL"): 2})
    assert sample("open_alerts", state="OPEN", severity="CRITICAL") == 2

    metrics.set_open_alerts({(AlertState.ACKNOWLEDGED, "CRITICAL"): 1})
    assert sample("open_alerts", state="OPEN", severity="CRITICAL") == 0
    assert sample("open_alerts", state="ACKNOWLEDGED", severity="CRITICAL") == 1


# --- consumer: counts alerts opened by severity ----------------------------------------


class OpeningService:
    pending_run_count = 0

    async def handle_match(self, match):
        return MatchOutcome.OPENED


async def test_consumer_counts_opened_alerts_by_severity():
    before = sample("alerts_opened_total", severity=PARAMS.severity)
    fake = FakeRedis([("1-0", {"data": json.dumps(event())})])
    await RedisMatchConsumer(fake, OpeningService()).poll_once("0")

    assert sample("alerts_opened_total", severity="HIGH") == before + 1


# --- webhook dispatcher ----------------------------------------------------------------


async def test_dispatch_pass_updates_webhook_metrics():
    from alert_service.domain.models import AlertEventType, Permanent
    from alert_service.domain.notifications import Dispatcher
    from alert_service.main import run_dispatch
    from alert_service.tests.fakes import T0, FakeClock, FakeSender, InMemoryOutboxRepo

    outbox, clock = InMemoryOutboxRepo(), FakeClock(T0)
    dispatcher = Dispatcher(outbox, FakeSender(Delivered(200), Permanent("HTTP 404", 404)), clock)
    outbox.add("alrt-1", AlertEventType.OPENED, T0)
    outbox.add("alrt-2", AlertEventType.RESOLVED, T0, "auto")
    delivered = sample("webhook_notifications_delivered_total", type="alert.opened")
    dead = sample("webhook_notifications_dead_total", type="alert.resolved")

    await run_dispatch(dispatcher)
    assert sample("webhook_notifications_delivered_total", type="alert.opened") == delivered + 1
    assert sample("webhook_notifications_dead_total", type="alert.resolved") == dead + 1
    assert sample("webhook_outbox_oldest_pending_age_seconds") == 0

    # Receiver down: the row created at T0 stays pending, and its age is what the panel shows.
    failing = Dispatcher(outbox, FakeSender(Retryable("HTTP 503", 503)), clock)
    outbox.add("alrt-3", AlertEventType.OPENED, T0)
    clock.advance(42)
    await run_dispatch(failing)
    assert sample("webhook_outbox_oldest_pending_age_seconds") == 42
