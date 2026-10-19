"""Pydantic request/response models. Validation of the wire format lives here only."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictStr,
    model_validator,
)

from ingest_service.domain.identity import make_observation
from ingest_service.domain.models import Band, Observation, Rule, Sensor


def _require_string(value: Any) -> Any:
    if not isinstance(value, str):
        raise ValueError("must be an ISO-8601 string")
    return value


def _number(**constraints: float):
    return Annotated[float, Field(strict=True, allow_inf_nan=False, **constraints)]


IsoTimestamp = Annotated[AwareDatetime, BeforeValidator(_require_string)]


# --- observations ---------------------------------------------------------


class LocationIn(BaseModel):
    lat: _number(ge=-90, le=90)
    lon: _number(ge=-180, le=180)


class ObservationIn(BaseModel):
    sensor_id: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    timestamp: IsoTimestamp
    frequency_mhz: _number(ge=1, le=6000)
    bandwidth_khz: _number(gt=0)
    power_dbm: _number(ge=-150, le=30)
    location: LocationIn

    def to_domain(self) -> Observation:
        return make_observation(
            sensor_id=self.sensor_id,
            timestamp=self.timestamp,
            frequency_mhz=self.frequency_mhz,
            bandwidth_khz=self.bandwidth_khz,
            power_dbm=self.power_dbm,
            lat=self.location.lat,
            lon=self.location.lon,
        )


class LocationOut(BaseModel):
    lat: float
    lon: float


class ObservationOut(BaseModel):
    observation_id: str
    sensor_id: str
    timestamp: datetime
    frequency_mhz: float
    bandwidth_khz: float
    power_dbm: float
    location: LocationOut

    @classmethod
    def from_domain(cls, o: Observation) -> ObservationOut:
        return cls(
            observation_id=o.observation_id,
            sensor_id=o.sensor_id,
            timestamp=o.timestamp,
            frequency_mhz=o.frequency_mhz,
            bandwidth_khz=o.bandwidth_khz,
            power_dbm=o.power_dbm,
            location=LocationOut(lat=o.location.lat, lon=o.location.lon),
        )


class IngestResponse(BaseModel):
    accepted: int
    duplicates: int


class SensorOut(BaseModel):
    sensor_id: str
    last_seen: datetime

    @classmethod
    def from_domain(cls, s: Sensor) -> SensorOut:
        return cls(sensor_id=s.sensor_id, last_seen=s.last_seen)


# --- rules ----------------------------------------------------------------

RuleId = Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9._-]{1,100}$")]
Severity = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]


class BandModel(BaseModel):
    min_mhz: _number(ge=0)
    max_mhz: _number(ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> BandModel:
        if self.min_mhz > self.max_mhz:
            raise ValueError("min_mhz must be <= max_mhz")
        return self


class RuleFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    type: Annotated[StrictStr, Field(min_length=1)]
    band: BandModel
    threshold_dbm: _number()
    min_duration_s: _number(ge=0)
    resolve_after_s: _number(gt=0)
    severity: Severity
    enabled: bool = True
    max_gap_s: _number(gt=0) | None = None

    def to_domain(self, rule_id: str) -> Rule:
        return Rule(
            rule_id=rule_id,
            name=self.name,
            type=self.type,
            band=Band(min_mhz=self.band.min_mhz, max_mhz=self.band.max_mhz),
            threshold_dbm=self.threshold_dbm,
            min_duration_s=self.min_duration_s,
            resolve_after_s=self.resolve_after_s,
            severity=self.severity,
            enabled=self.enabled,
            max_gap_s=self.max_gap_s,
        )


class RuleIn(RuleFields):
    rule_id: RuleId


class RuleUpdate(RuleFields):
    """Full replacement. rule_id comes from the path; if sent in the body it must match."""

    rule_id: RuleId | None = None


class RuleOut(BaseModel):
    rule_id: str
    name: str
    type: str
    band: BandModel
    threshold_dbm: float
    min_duration_s: float
    resolve_after_s: float
    severity: str
    enabled: bool
    max_gap_s: float | None

    @classmethod
    def from_domain(cls, r: Rule) -> RuleOut:
        return cls(
            rule_id=r.rule_id,
            name=r.name,
            type=r.type,
            band=BandModel(min_mhz=r.band.min_mhz, max_mhz=r.band.max_mhz),
            threshold_dbm=r.threshold_dbm,
            min_duration_s=r.min_duration_s,
            resolve_after_s=r.resolve_after_s,
            severity=r.severity,
            enabled=r.enabled,
            max_gap_s=r.max_gap_s,
        )
