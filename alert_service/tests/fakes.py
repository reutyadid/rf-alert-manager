"""In-memory fakes for domain tests: no DB, no Redis, no real time."""

import dataclasses
from datetime import UTC, datetime, timedelta

from alert_service.domain.models import Alert, AlertFilter, AlertState, Contribution


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

    async def create(self, alert: Alert, contributions: list[Contribution]) -> None:
        self.alerts[alert.alert_id] = dataclasses.replace(alert)
        self.contributions[alert.alert_id] = list(contributions)

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
        self, alert_id: str, from_states: set[AlertState], to_state: AlertState, at: datetime
    ) -> Alert | None:
        stored = self.alerts.get(alert_id)
        if stored is None or stored.state not in from_states:
            return None
        stored.state = to_state
        if to_state is AlertState.ACKNOWLEDGED:
            stored.acknowledged_at = at
        if to_state is AlertState.RESOLVED:
            stored.resolved_at = at
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


T0 = datetime(2026, 10, 6, 10, 0, 0, tzinfo=UTC)
