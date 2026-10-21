from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class AlertState(StrEnum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"

    @classmethod
    def active(cls) -> set[AlertState]:
        return {cls.OPEN, cls.ACKNOWLEDGED}


class MatchOutcome(StrEnum):
    """What handle_match did with one rule_match event (used for metrics and logs)."""

    PENDING = "pending"  # added to (or started) a pending run
    OPENED = "opened"  # the run reached min_duration_s and opened an alert
    UPDATED = "updated"  # updated an OPEN/ACKNOWLEDGED alert
    DUPLICATE = "duplicate"  # observation_id already contributed
    LATE = "late"  # older than the watermark
    FUTURE = "future"  # ahead of the server clock by more than max_clock_skew_s
    IGNORED = "ignored"  # earlier than the pending run by more than max_gap_s


@dataclass(frozen=True)
class RuleParams:
    min_duration_s: float
    max_gap_s: float
    resolve_after_s: float
    severity: str


@dataclass(frozen=True)
class RuleMatch:
    observation_id: str
    rule_id: str
    sensor_id: str
    timestamp: datetime
    frequency_mhz: float
    power_dbm: float
    rule: RuleParams


@dataclass(frozen=True)
class Contribution:
    observation_id: str
    sensor_id: str
    timestamp: datetime
    frequency_mhz: float
    power_dbm: float

    @classmethod
    def from_match(cls, match: RuleMatch) -> Contribution:
        return cls(
            observation_id=match.observation_id,
            sensor_id=match.sensor_id,
            timestamp=match.timestamp,
            frequency_mhz=match.frequency_mhz,
            power_dbm=match.power_dbm,
        )


@dataclass
class Alert:
    alert_id: str
    rule_id: str
    signal_key: str
    state: AlertState
    severity: str
    frequency_mhz: float
    sensor_ids: list[str]
    first_seen: datetime
    last_seen: datetime
    occurrences: int
    peak_power_dbm: float
    resolve_after_s: float
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None

    def add(self, c: Contribution) -> None:
        """Order-independent aggregation: arrival order never changes the result."""
        self.first_seen = min(self.first_seen, c.timestamp)
        self.last_seen = max(self.last_seen, c.timestamp)
        self.peak_power_dbm = max(self.peak_power_dbm, c.power_dbm)
        self.occurrences += 1
        if c.sensor_id not in self.sensor_ids:
            self.sensor_ids = sorted([*self.sensor_ids, c.sensor_id])


class AlertEventType(StrEnum):
    OPENED = "alert.opened"
    RESOLVED = "alert.resolved"


@dataclass(frozen=True)
class AlertEvent:
    """A change that must be notified. The domain decides that it happens; the adapter
    stores it in the same transaction as the alert change (outbox)."""

    type: AlertEventType
    occurred_at: datetime  # wall clock, same timeline as resolved_at / acknowledged_at
    resolution: str | None = None  # "auto" / "manual" for RESOLVED


@dataclass(frozen=True)
class OutboxRow:
    """A pending webhook notification. `alert` is the alert JSON at the time of the change."""

    id: str
    seq: int
    alert_id: str
    type: AlertEventType
    occurred_at: datetime
    resolution: str | None
    alert: dict[str, Any]
    attempts: int


@dataclass(frozen=True)
class Delivered:
    status: int


@dataclass(frozen=True)
class Retryable:
    reason: str
    status: int | None = None


@dataclass(frozen=True)
class Permanent:
    reason: str
    status: int | None = None


SendOutcome = Delivered | Retryable | Permanent


@dataclass(frozen=True)
class AlertFilter:
    state: AlertState | None = None
    severity: str | None = None
    sensor_id: str | None = None
    min_frequency_mhz: float | None = None
    max_frequency_mhz: float | None = None
    limit: int = field(default=100)
