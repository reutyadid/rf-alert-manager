import json
from datetime import timedelta, timezone

from ingest_service.domain.events import build_rule_match
from ingest_service.tests.builders import T0, a_rule, an_observation


def test_event_matches_contract_exactly():
    obs = an_observation()
    event = build_rule_match(obs, a_rule(max_gap_s=5.0), default_max_gap_s=3.0)
    assert event == {
        "observation_id": obs.observation_id,
        "rule_id": "rule-ism-high-power",
        "sensor_id": "sensor-03",
        "timestamp": "2026-10-06T10:15:30Z",
        "frequency_mhz": 2437.0,
        "power_dbm": -42.5,
        "rule": {
            "min_duration_s": 10.0,
            "max_gap_s": 5.0,
            "resolve_after_s": 60.0,
            "severity": "HIGH",
        },
    }
    json.dumps(event)  # serializable as-is


def test_max_gap_defaulted_when_rule_omits_it():
    event = build_rule_match(an_observation(), a_rule(max_gap_s=None), default_max_gap_s=3.0)
    assert event["rule"]["max_gap_s"] == 3.0


def test_timestamp_is_utc_z_even_for_offset_input():
    obs = an_observation(timestamp=T0.astimezone(timezone(timedelta(hours=3))))
    event = build_rule_match(obs, a_rule(), default_max_gap_s=3.0)
    assert event["timestamp"] == "2026-10-06T10:15:30Z"
