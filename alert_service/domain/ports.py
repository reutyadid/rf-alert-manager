from datetime import datetime
from typing import Any, Protocol

from alert_service.domain.models import (
    Alert,
    AlertEvent,
    AlertFilter,
    AlertState,
    Contribution,
    OutboxRow,
    SendOutcome,
)


class Clock(Protocol):
    def now(self) -> datetime: ...


class AlertRepository(Protocol):
    async def create(
        self, alert: Alert, contributions: list[Contribution], event: AlertEvent | None = None
    ) -> None:
        """Store the alert, its contributions and the event (if any) atomically."""
        ...

    async def get(self, alert_id: str) -> Alert | None: ...

    async def get_active_by_signal_key(self, signal_key: str) -> Alert | None: ...

    async def list_active(self) -> list[Alert]: ...

    async def has_contribution(self, alert_id: str, observation_id: str) -> bool: ...

    async def add_contribution(self, alert: Alert, contribution: Contribution) -> None:
        """Store the contribution and write the alert's aggregate fields. Never the state."""
        ...

    async def transition(
        self,
        alert_id: str,
        from_states: set[AlertState],
        to_state: AlertState,
        at: datetime,
        event: AlertEvent | None = None,
    ) -> Alert | None:
        """Change state only if the current state is in from_states. None if it was not.
        The event is stored atomically with the change, and only if the change happened."""
        ...

    async def query(self, flt: AlertFilter) -> list[Alert]: ...

    async def list_contributions(self, alert_id: str) -> list[Contribution]: ...


class OutboxRepo(Protocol):
    """Webhook notifications written by AlertRepository together with the alert change."""

    async def fetch_due(self, now: datetime, limit: int) -> list[OutboxRow]:
        """Pending rows ordered by seq. A row is due only if next_attempt_at <= now AND no
        lower-seq row for the same alert_id is still pending (per-alert ordering: resolved is
        never sent before opened). A dead row no longer blocks the rows after it."""
        ...

    async def mark_sent(self, row_id: str, attempts: int, at: datetime, status: int) -> None: ...

    async def schedule_retry(
        self, row_id: str, attempts: int, next_attempt_at: datetime, status: int | None, error: str
    ) -> None: ...

    async def mark_dead(
        self, row_id: str, attempts: int, status: int | None, error: str
    ) -> None: ...

    async def oldest_pending_occurred_at(self) -> datetime | None: ...


class WebhookSender(Protocol):
    async def send(self, notification: dict[str, Any]) -> SendOutcome:
        """POST one notification. Never raises for HTTP or network errors."""
        ...
