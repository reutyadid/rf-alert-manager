"""Canonical form and deterministic id of an observation.

The id is uuid5 over canonical inputs, so a retried observation always gets the same id
and alert-service can skip it (DECISIONS §7.1).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from ingest_service.domain.models import Location, Observation

# Fixed forever: changing it changes every observation id.
OBSERVATION_NAMESPACE = uuid.UUID("6f1c2a52-0d3e-4b8e-9a51-6a0c3d7f2e11")

FREQUENCY_DECIMALS = 6


def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return dt.astimezone(UTC)


def canonical_timestamp(dt: datetime) -> str:
    """UTC, 'Z' suffix, microseconds only when non-zero: 2026-10-06T10:15:30Z."""
    dt = to_utc(dt)
    text = dt.strftime("%Y-%m-%dT%H:%M:%S")
    if dt.microsecond:
        text += f".{dt.microsecond:06d}"
    return text + "Z"


def normalize_frequency(frequency_mhz: float) -> float:
    return round(float(frequency_mhz), FREQUENCY_DECIMALS)


def canonical_frequency(frequency_mhz: float) -> str:
    return f"{normalize_frequency(frequency_mhz):.{FREQUENCY_DECIMALS}f}"


def observation_id(sensor_id: str, timestamp: datetime, frequency_mhz: float) -> str:
    key = f"{sensor_id}|{canonical_timestamp(timestamp)}|{canonical_frequency(frequency_mhz)}"
    return str(uuid.uuid5(OBSERVATION_NAMESPACE, key))


def make_observation(
    *,
    sensor_id: str,
    timestamp: datetime,
    frequency_mhz: float,
    bandwidth_khz: float,
    power_dbm: float,
    lat: float,
    lon: float,
) -> Observation:
    """Build a normalized observation (UTC timestamp, rounded frequency) with its id."""
    ts = to_utc(timestamp)
    frequency = normalize_frequency(frequency_mhz)
    return Observation(
        observation_id=observation_id(sensor_id, ts, frequency),
        sensor_id=sensor_id,
        timestamp=ts,
        frequency_mhz=frequency,
        bandwidth_khz=float(bandwidth_khz),
        power_dbm=float(power_dbm),
        location=Location(lat=float(lat), lon=float(lon)),
    )
