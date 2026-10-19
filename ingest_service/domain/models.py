"""Domain entities. Plain dataclasses; all datetimes are timezone-aware UTC."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Location:
    lat: float
    lon: float


@dataclass(frozen=True)
class Observation:
    observation_id: str
    sensor_id: str
    timestamp: datetime
    frequency_mhz: float
    bandwidth_khz: float
    power_dbm: float
    location: Location


@dataclass(frozen=True)
class Band:
    min_mhz: float
    max_mhz: float

    def contains(self, frequency_mhz: float) -> bool:
        return self.min_mhz <= frequency_mhz <= self.max_mhz


@dataclass(frozen=True)
class Rule:
    rule_id: str
    name: str
    type: str
    band: Band
    threshold_dbm: float
    min_duration_s: float
    resolve_after_s: float
    severity: str
    enabled: bool
    max_gap_s: float | None = None


@dataclass(frozen=True)
class Sensor:
    sensor_id: str
    last_seen: datetime


@dataclass(frozen=True)
class ObservationQuery:
    sensor_id: str | None = None
    min_frequency_mhz: float | None = None
    max_frequency_mhz: float | None = None
    start: datetime | None = None
    end: datetime | None = None
    limit: int = 100


@dataclass(frozen=True)
class IngestResult:
    accepted: int
    duplicates: int
    published: int
