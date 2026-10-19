"""The rule_match event. Shape is the contract with alert-service (CLAUDE.md); keep it exact."""

from __future__ import annotations

from ingest_service.domain.identity import canonical_timestamp
from ingest_service.domain.models import Observation, Rule


def build_rule_match(observation: Observation, rule: Rule, default_max_gap_s: float) -> dict:
    max_gap_s = rule.max_gap_s if rule.max_gap_s is not None else default_max_gap_s
    return {
        "observation_id": observation.observation_id,
        "rule_id": rule.rule_id,
        "sensor_id": observation.sensor_id,
        "timestamp": canonical_timestamp(observation.timestamp),
        "frequency_mhz": observation.frequency_mhz,
        "power_dbm": observation.power_dbm,
        "rule": {
            "min_duration_s": rule.min_duration_s,
            "max_gap_s": max_gap_s,
            "resolve_after_s": rule.resolve_after_s,
            "severity": rule.severity,
        },
    }
