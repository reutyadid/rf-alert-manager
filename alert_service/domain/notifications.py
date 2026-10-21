from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from alert_service.domain.models import Delivered, OutboxRow, Permanent, Retryable, SendOutcome
from alert_service.domain.ports import Clock, OutboxRepo, WebhookSender

# Wait after the n-th failed attempt (index n - 1). Deterministic, no jitter: one dispatcher
# and one receiver, so there is no thundering herd to spread out.
BACKOFF_S = (2, 4, 8, 16, 32, 60, 120, 300, 300)
MAX_ATTEMPTS = len(BACKOFF_S) + 1
ENVELOPE_VERSION = 1


def next_attempt_at(attempts: int, now: datetime) -> datetime | None:
    """When to retry after `attempts` failed attempts, or None to give up."""
    if attempts >= MAX_ATTEMPTS:
        return None
    return now + timedelta(seconds=BACKOFF_S[attempts - 1])


def envelope(row: OutboxRow) -> dict[str, Any]:
    """The JSON body sent to the receiver. Receivers dedupe on id and order on sequence."""
    return {
        "version": ENVELOPE_VERSION,
        "id": row.id,
        "type": row.type.value,
        "sequence": row.seq,
        "occurred_at": row.occurred_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "resolution": row.resolution,
        "alert": row.alert,
    }


@dataclass
class DispatchResult:
    delivered: list[OutboxRow] = field(default_factory=list)
    dead: list[tuple[OutboxRow, str]] = field(default_factory=list)  # (row, last error)
    retried: int = 0


class Dispatcher:
    """Sends due outbox rows one at a time, in seq order. Never raises for a single row:
    a sender bug is treated as a retryable failure of that row."""

    def __init__(
        self, outbox: OutboxRepo, sender: WebhookSender, clock: Clock, batch_size: int = 50
    ) -> None:
        self._outbox = outbox
        self._sender = sender
        self._clock = clock
        self._batch_size = batch_size

    async def dispatch_due(self) -> DispatchResult:
        result = DispatchResult()
        for row in await self._outbox.fetch_due(self._clock.now(), self._batch_size):
            try:
                outcome: SendOutcome = await self._sender.send(envelope(row))
            except Exception as exc:
                outcome = Retryable(f"{type(exc).__name__}: {exc}")
            await self._record(row, outcome, result)
        return result

    async def oldest_pending_age_s(self) -> float:
        oldest = await self._outbox.oldest_pending_occurred_at()
        if oldest is None:
            return 0.0
        return max(0.0, (self._clock.now() - oldest).total_seconds())

    async def _record(self, row: OutboxRow, outcome: SendOutcome, result: DispatchResult) -> None:
        attempts = row.attempts + 1
        now = self._clock.now()
        match outcome:
            case Delivered(status=status):
                await self._outbox.mark_sent(row.id, attempts, now, status)
                result.delivered.append(row)
                return
            case Permanent(reason=reason, status=status):
                retry_at = None
            case Retryable(reason=reason, status=status):
                retry_at = next_attempt_at(attempts, now)
        if retry_at is None:
            await self._outbox.mark_dead(row.id, attempts, status, reason)
            result.dead.append((row, reason))
        else:
            await self._outbox.schedule_retry(row.id, attempts, retry_at, status, reason)
            result.retried += 1
