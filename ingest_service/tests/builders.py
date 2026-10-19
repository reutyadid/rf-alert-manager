"""Test data builders with sensible defaults."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from ingest_service.domain.identity import make_observation
from ingest_service.domain.models import Band, Observation, Rule

T0 = datetime(2026, 10, 6, 10, 15, 30, tzinfo=UTC)


def an_observation(**overrides) -> Observation:
    fields = {
        "sensor_id": "sensor-03",
        "timestamp": T0,
        "frequency_mhz": 2437.0,
        "bandwidth_khz": 20000.0,
        "power_dbm": -42.5,
        "lat": 32.08,
        "lon": 34.78,
    }
    fields.update(overrides)
    return make_observation(**fields)


def a_rule(**overrides) -> Rule:
    base = Rule(
        rule_id="rule-ism-high-power",
        name="High power in protected ISM band",
        type="power_threshold",
        band=Band(min_mhz=2400.0, max_mhz=2483.5),
        threshold_dbm=-50.0,
        min_duration_s=10.0,
        resolve_after_s=60.0,
        severity="HIGH",
        enabled=True,
        max_gap_s=None,
    )
    return replace(base, **overrides)


def observation_json(**overrides) -> dict:
    body = {
        "sensor_id": "sensor-03",
        "timestamp": "2026-10-06T10:15:30Z",
        "frequency_mhz": 2437.0,
        "bandwidth_khz": 20000,
        "power_dbm": -42.5,
        "location": {"lat": 32.08, "lon": 34.78},
    }
    body.update(overrides)
    return body


def rule_json(**overrides) -> dict:
    body = {
        "rule_id": "rule-ism-high-power",
        "name": "High power in protected ISM band",
        "type": "power_threshold",
        "band": {"min_mhz": 2400.0, "max_mhz": 2483.5},
        "threshold_dbm": -50.0,
        "min_duration_s": 10,
        "resolve_after_s": 60,
        "severity": "HIGH",
        "enabled": True,
    }
    body.update(overrides)
    return body
