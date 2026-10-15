from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


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


@dataclass(frozen=True)
class AlertFilter:
    state: AlertState | None = None
    severity: str | None = None
    sensor_id: str | None = None
    min_frequency_mhz: float | None = None
    max_frequency_mhz: float | None = None
    limit: int = field(default=100)
