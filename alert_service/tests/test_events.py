import json
from datetime import UTC, datetime

import pytest

from alert_service.adapters.events import InvalidEvent, parse_rule_match
from alert_service.domain.models import RuleParams


def event(**overrides) -> dict:
    base = {
        "observation_id": "6f1c1d3e-0000-5000-8000-000000000001",
        "rule_id": "rule-ism-high-power",
        "sensor_id": "sensor-03",
        "timestamp": "2026-10-06T10:15:30Z",
        "frequency_mhz": 2437.0,
        "power_dbm": -42.5,
        "rule": {"min_duration_s": 10, "max_gap_s": 5, "resolve_after_s": 60, "severity": "HIGH"},
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "rule",
    [
        {"min_duration_s": 10, "max_gap_s": 5, "resolve_after_s": 60, "severity": "HIGH"},
        # ingest sends floats
        {"min_duration_s": 10.0, "max_gap_s": 5.0, "resolve_after_s": 60.0, "severity": "HIGH"},
    ],
    ids=["int", "float"],
)
def test_parses_the_agreed_contract(rule):
    data = event(rule=rule)
    assert "schema_version" not in data  # the agreed contract omits it
    match = parse_rule_match(json.dumps(data))
    assert match.observation_id == "6f1c1d3e-0000-5000-8000-000000000001"
    assert match.timestamp == datetime(2026, 10, 6, 10, 15, 30, tzinfo=UTC)
    assert match.frequency_mhz == 2437.0
    assert match.rule == RuleParams(10, 5, 60, "HIGH")


def test_schema_version_1_is_accepted_and_others_rejected():
    assert parse_rule_match(json.dumps(event(schema_version=1)))
    with pytest.raises(InvalidEvent, match="schema_version"):
        parse_rule_match(json.dumps(event(schema_version=2)))


def test_offset_timestamps_are_converted_to_utc():
    match = parse_rule_match(json.dumps(event(timestamp="2026-10-06T13:15:30+03:00")))
    assert match.timestamp == datetime(2026, 10, 6, 10, 15, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    "data",
    [
        "not json",
        "[]",
        json.dumps({k: v for k, v in event().items() if k != "sensor_id"}),
        json.dumps(event(rule={"min_duration_s": 10, "resolve_after_s": 60, "severity": "HIGH"})),
        json.dumps(event(timestamp="2026-10-06T10:15:30")),  # naive
        json.dumps(event(timestamp="yesterday")),
        json.dumps(event(power_dbm="-42")),
        json.dumps(event(frequency_mhz=True)),
        json.dumps(event(rule="HIGH")),
        "",
        # Values that would crash the domain and block the stream (review finding 1).
        '{"frequency_mhz": NaN}',
        json.dumps(event()).replace("2437.0", "NaN"),
        json.dumps(event()).replace("-42.5", "Infinity"),
        json.dumps(event(frequency_mhz=1e308)),
        json.dumps(event(frequency_mhz=0.5)),
        json.dumps(event(power_dbm=-500)),
        json.dumps(event(rule={**event()["rule"], "max_gap_s": -1})),
        json.dumps(event(rule={**event()["rule"], "min_duration_s": 1e300})),
        json.dumps(event(rule={**event()["rule"], "resolve_after_s": float("inf")})),
    ],
)
def test_invalid_events_are_rejected(data):
    with pytest.raises(InvalidEvent):
        parse_rule_match(data)
