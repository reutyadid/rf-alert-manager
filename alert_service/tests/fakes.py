"""In-memory fakes for domain tests: no DB, no Redis, no real time."""

import dataclasses
from datetime import UTC, datetime, timedelta

from alert_service.domain.models import (
    Alert,
    AlertEvent,
    AlertFilter,
    AlertState,
    Contribution,
    Delivered,
    OutboxRow,
    SendOutcome,
)


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


class InMemoryAlertRepository:
    def __init__(self) -> None:
        self.alerts: dict[str, Alert] = {}
        self.contributions: dict[str, list[Contribution]] = {}
        self.events: list[tuple[str, AlertEvent]] = []  # (alert_id, event): the outbox

    async def create(
        self, alert: Alert, contributions: list[Contribution], event: AlertEvent | None = None
    ) -> None:
        self.alerts[alert.alert_id] = dataclasses.replace(alert)
        self.contributions[alert.alert_id] = list(contributions)
        if event is not None:
            self.events.append((alert.alert_id, event))

    async def get(self, alert_id: str) -> Alert | None:
        alert = self.alerts.get(alert_id)
        return dataclasses.replace(alert) if alert else None

    async def get_active_by_signal_key(self, signal_key: str) -> Alert | None:
        for alert in self.alerts.values():
            if alert.signal_key == signal_key and alert.state in AlertState.active():
                return dataclasses.replace(alert)
        return None

    async def list_active(self) -> list[Alert]:
        return [
            dataclasses.replace(a) for a in self.alerts.values() if a.state in AlertState.active()
        ]

    async def has_contribution(self, alert_id: str, observation_id: str) -> bool:
        return any(c.observation_id == observation_id for c in self.contributions[alert_id])

    async def add_contribution(self, alert: Alert, contribution: Contribution) -> None:
        # Mirrors the SQL adapter: only aggregate fields are written, never state.
        stored = self.alerts[alert.alert_id]
        stored.first_seen = alert.first_seen
        stored.last_seen = alert.last_seen
        stored.occurrences = alert.occurrences
        stored.peak_power_dbm = alert.peak_power_dbm
        stored.sensor_ids = list(alert.sensor_ids)
        self.contributions[alert.alert_id].append(contribution)

    async def transition(
        self,
        alert_id: str,
        from_states: set[AlertState],
        to_state: AlertState,
        at: datetime,
        event: AlertEvent | None = None,
    ) -> Alert | None:
        stored = self.alerts.get(alert_id)
        if stored is None or stored.state not in from_states:
            return None
        stored.state = to_state
        if to_state is AlertState.ACKNOWLEDGED:
            stored.acknowledged_at = at
        if to_state is AlertState.RESOLVED:
            stored.resolved_at = at
        if event is not None:
            self.events.append((alert_id, event))
        return dataclasses.replace(stored)

    async def query(self, flt: AlertFilter) -> list[Alert]:
        result = []
        for a in self.alerts.values():
            if flt.state and a.state is not flt.state:
                continue
            if flt.severity and a.severity != flt.severity:
                continue
            if flt.sensor_id and flt.sensor_id not in a.sensor_ids:
                continue
            if flt.min_frequency_mhz is not None and a.frequency_mhz < flt.min_frequency_mhz:
                continue
            if flt.max_frequency_mhz is not None and a.frequency_mhz > flt.max_frequency_mhz:
                continue
            result.append(dataclasses.replace(a))
        return result

    async def list_contributions(self, alert_id: str) -> list[Contribution]:
        return list(self.contributions.get(alert_id, []))


class InMemoryOutboxRepo:
    """Same contract as PostgresOutboxRepo, including per-alert ordering."""

    def __init__(self) -> None:
        self.rows: list[dict] = []  # OutboxRow fields + status, next_attempt_at, last_*

    def add(self, alert_id: str, type_, occurred_at: datetime, resolution=None) -> str:
        row_id = f"ntf-{len(self.rows) + 1}"
        self.rows.append(
            {
                "id": row_id,
                "seq": len(self.rows) + 1,
                "alert_id": alert_id,
                "type": type_,
                "occurred_at": occurred_at,
                "resolution": resolution,
                "alert": {"alert_id": alert_id},
                "attempts": 0,
                "status": "pending",
                "next_attempt_at": occurred_at,
                "last_status_code": None,
                "last_error": None,
                "sent_at": None,
            }
        )
        return row_id

    def row(self, row_id: str) -> dict:
        return next(r for r in self.rows if r["id"] == row_id)

    async def fetch_due(self, now: datetime, limit: int) -> list[OutboxRow]:
        pending = [r for r in self.rows if r["status"] == "pending"]
        due = [
            r
            for r in pending
            if r["next_attempt_at"] <= now
            and not any(e["alert_id"] == r["alert_id"] and e["seq"] < r["seq"] for e in pending)
        ]
        fields = {f.name for f in dataclasses.fields(OutboxRow)}
        due.sort(key=lambda r: r["seq"])
        return [OutboxRow(**{k: r[k] for k in fields}) for r in due[:limit]]

    async def mark_sent(self, row_id: str, attempts: int, at: datetime, status: int) -> None:
        self.row(row_id).update(
            status="sent", attempts=attempts, sent_at=at, last_status_code=status, last_error=None
        )

    async def schedule_retry(
        self, row_id: str, attempts: int, next_attempt_at: datetime, status: int | None, error: str
    ) -> None:
        self.row(row_id).update(
            attempts=attempts,
            next_attempt_at=next_attempt_at,
            last_status_code=status,
            last_error=error,
        )

    async def mark_dead(self, row_id: str, attempts: int, status: int | None, error: str) -> None:
        self.row(row_id).update(
            status="dead", attempts=attempts, last_status_code=status, last_error=error
        )

    async def oldest_pending_occurred_at(self) -> datetime | None:
        return min((r["occurred_at"] for r in self.rows if r["status"] == "pending"), default=None)


class FakeSender:
    """Returns the queued outcomes in order (Delivered(200) once empty). An Exception in the
    queue is raised instead of returned."""

    def __init__(self, *outcomes) -> None:
        self.outcomes = list(outcomes)
        self.sent: list[dict] = []

    async def send(self, notification: dict) -> SendOutcome:
        self.sent.append(notification)
        outcome = self.outcomes.pop(0) if self.outcomes else Delivered(200)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


T0 = datetime(2026, 10, 6, 10, 0, 0, tzinfo=UTC)
