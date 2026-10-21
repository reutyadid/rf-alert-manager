from datetime import timedelta

import pytest

from alert_service.domain.models import AlertEventType, Delivered, Permanent, Retryable
from alert_service.domain.notifications import (
    BACKOFF_S,
    MAX_ATTEMPTS,
    Dispatcher,
    next_attempt_at,
)
from alert_service.tests.fakes import T0, FakeClock, FakeSender, InMemoryOutboxRepo

pytestmark = pytest.mark.anyio


# --- retry policy --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("attempts", "delay_s"),
    [(1, 2), (2, 4), (3, 8), (4, 16), (5, 32), (6, 60), (7, 120), (8, 300), (9, 300)],
)
def test_backoff_after_each_failed_attempt(attempts, delay_s):
    assert next_attempt_at(attempts, T0) == T0 + timedelta(seconds=delay_s)


def test_gives_up_after_max_attempts():
    assert MAX_ATTEMPTS == 10
    assert next_attempt_at(MAX_ATTEMPTS, T0) is None
    assert next_attempt_at(MAX_ATTEMPTS + 1, T0) is None


def test_backoff_is_capped_at_300_s():
    assert max(BACKOFF_S) == 300
    assert len(BACKOFF_S) == MAX_ATTEMPTS - 1  # one wait between each pair of attempts


# --- dispatcher ----------------------------------------------------------------------


def dispatcher(*outcomes):
    outbox, sender, clock = InMemoryOutboxRepo(), FakeSender(*outcomes), FakeClock(T0)
    return Dispatcher(outbox, sender, clock), outbox, sender, clock


async def test_due_row_is_sent_with_the_envelope_and_marked_sent():
    d, outbox, sender, _ = dispatcher()
    row_id = outbox.add("alrt-1", AlertEventType.RESOLVED, T0, "auto")
    result = await d.dispatch_due()
    assert sender.sent == [
        {
            "version": 1,
            "id": row_id,
            "type": "alert.resolved",
            "sequence": 1,
            "occurred_at": "2026-10-06T10:00:00Z",
            "resolution": "auto",
            "alert": {"alert_id": "alrt-1"},
        }
    ]
    assert outbox.row(row_id)["status"] == "sent"
    assert outbox.row(row_id)["attempts"] == 1
    assert [r.id for r in result.delivered] == [row_id]


async def test_row_that_is_not_due_yet_is_not_sent():
    d, outbox, sender, _ = dispatcher()
    outbox.add("alrt-1", AlertEventType.OPENED, T0 + timedelta(seconds=1))
    await d.dispatch_due()
    assert sender.sent == []


async def test_retryable_failure_schedules_the_next_attempt():
    d, outbox, sender, clock = dispatcher(Retryable("HTTP 503", 503))
    row_id = outbox.add("alrt-1", AlertEventType.OPENED, T0)
    result = await d.dispatch_due()
    row = outbox.row(row_id)
    assert (row["status"], row["attempts"], row["last_status_code"]) == ("pending", 1, 503)
    assert row["next_attempt_at"] == T0 + timedelta(seconds=2)
    assert result.retried == 1

    await d.dispatch_due()  # not due yet
    assert len(sender.sent) == 1
    clock.advance(2)
    await d.dispatch_due()
    assert outbox.row(row_id)["status"] == "sent"
    assert outbox.row(row_id)["attempts"] == 2


async def test_permanent_failure_marks_the_row_dead():
    d, outbox, _, _ = dispatcher(Permanent("HTTP 404", 404))
    row_id = outbox.add("alrt-1", AlertEventType.OPENED, T0)
    result = await d.dispatch_due()
    assert outbox.row(row_id)["status"] == "dead"
    assert [(r.id, reason) for r, reason in result.dead] == [(row_id, "HTTP 404")]


async def test_gives_up_after_max_attempts_of_retryable_failures():
    d, outbox, _, clock = dispatcher(*[Retryable("timeout")] * MAX_ATTEMPTS)
    row_id = outbox.add("alrt-1", AlertEventType.OPENED, T0)
    for _ in range(MAX_ATTEMPTS):
        await d.dispatch_due()
        clock.advance(300)
    row = outbox.row(row_id)
    assert (row["status"], row["attempts"], row["last_error"]) == ("dead", MAX_ATTEMPTS, "timeout")


async def test_sender_exception_is_retried_and_does_not_stop_the_pass():
    d, outbox, _, _ = dispatcher(RuntimeError("bug"), Delivered(200))
    first = outbox.add("alrt-1", AlertEventType.OPENED, T0)
    second = outbox.add("alrt-2", AlertEventType.OPENED, T0)
    await d.dispatch_due()
    assert outbox.row(first)["status"] == "pending"
    assert outbox.row(first)["last_error"] == "RuntimeError: bug"
    assert outbox.row(second)["status"] == "sent"


async def test_resolved_is_not_sent_while_opened_of_the_same_alert_is_pending():
    d, outbox, sender, clock = dispatcher(Retryable("HTTP 503", 503))
    opened = outbox.add("alrt-1", AlertEventType.OPENED, T0)
    resolved = outbox.add("alrt-1", AlertEventType.RESOLVED, T0, "manual")
    await d.dispatch_due()  # opened fails
    await d.dispatch_due()
    assert [n["id"] for n in sender.sent] == [opened]
    assert outbox.row(resolved)["status"] == "pending"

    clock.advance(2)
    await d.dispatch_due()  # opened delivered; resolved was not due in this fetch
    await d.dispatch_due()
    assert [n["id"] for n in sender.sent] == [opened, opened, resolved]


async def test_oldest_pending_age():
    d, outbox, _, clock = dispatcher(Retryable("HTTP 503", 503))
    assert await d.oldest_pending_age_s() == 0
    outbox.add("alrt-1", AlertEventType.OPENED, T0)
    await d.dispatch_due()
    clock.advance(30)
    assert await d.oldest_pending_age_s() == 30
