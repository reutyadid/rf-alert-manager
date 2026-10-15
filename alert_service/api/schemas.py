from datetime import datetime

from pydantic import BaseModel

from alert_service.domain.models import Alert, AlertState, Contribution


class AlertOut(BaseModel):
    alert_id: str
    rule_id: str
    state: AlertState
    severity: str
    frequency_mhz: float
    sensor_ids: list[str]
    first_seen: datetime
    last_seen: datetime
    occurrences: int
    peak_power_dbm: float
    acknowledged_at: datetime | None
    resolved_at: datetime | None

    @classmethod
    def from_domain(cls, alert: Alert) -> "AlertOut":
        return cls(
            alert_id=alert.alert_id,
            rule_id=alert.rule_id,
            state=alert.state,
            severity=alert.severity,
            frequency_mhz=alert.frequency_mhz,
            sensor_ids=alert.sensor_ids,
            first_seen=alert.first_seen,
            last_seen=alert.last_seen,
            occurrences=alert.occurrences,
            peak_power_dbm=alert.peak_power_dbm,
            acknowledged_at=alert.acknowledged_at,
            resolved_at=alert.resolved_at,
        )


class ObservationOut(BaseModel):
    observation_id: str
    sensor_id: str
    timestamp: datetime
    frequency_mhz: float
    power_dbm: float

    @classmethod
    def from_domain(cls, c: Contribution) -> "ObservationOut":
        return cls(
            observation_id=c.observation_id,
            sensor_id=c.sensor_id,
            timestamp=c.timestamp,
            frequency_mhz=c.frequency_mhz,
            power_dbm=c.power_dbm,
        )


class AlertDetailOut(AlertOut):
    observations: list[ObservationOut]


class ErrorOut(BaseModel):
    error: str
    message: str
    details: list = []
