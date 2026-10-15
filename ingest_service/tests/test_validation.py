from datetime import timedelta

import pytest

from ingest_service.api.errors import ApiError
from ingest_service.api.observations import parse_observations
from ingest_service.tests.builders import T0, observation_json

# Server clock for these tests: 1 hour after the builders' default observation time.
NOW = T0 + timedelta(hours=1)


def parse(payload, **kwargs):
    return parse_observations(payload, now=NOW, max_clock_skew_s=60, **kwargs)


def iso(dt) -> str:
    return dt.isoformat().replace("+00:00", "Z")


VALID_EDGES = [
    {"frequency_mhz": 1},
    {"frequency_mhz": 6000},
    {"bandwidth_khz": 0.001},
    {"power_dbm": -150},
    {"power_dbm": 30},
    {"location": {"lat": -90, "lon": -180}},
    {"location": {"lat": 90, "lon": 180}},
    {"timestamp": "2026-10-06T13:15:30+03:00"},
    {"timestamp": "2026-10-06T10:15:30.123Z"},
]

INVALID = [
    ({"frequency_mhz": 0.99}, "frequency_mhz"),
    ({"frequency_mhz": 6000.1}, "frequency_mhz"),
    ({"frequency_mhz": "2437"}, "frequency_mhz"),
    ({"bandwidth_khz": 0}, "bandwidth_khz"),
    ({"bandwidth_khz": -5}, "bandwidth_khz"),
    ({"power_dbm": -150.1}, "power_dbm"),
    ({"power_dbm": 30.1}, "power_dbm"),
    ({"timestamp": "2026-10-06T10:15:30"}, "timestamp"),  # naive
    ({"timestamp": "yesterday"}, "timestamp"),
    ({"timestamp": 1791281730}, "timestamp"),  # epoch number, not ISO-8601
    ({"location": {"lat": 90.1, "lon": 0}}, "location.lat"),
    ({"location": {"lat": 0, "lon": -180.1}}, "location.lon"),
    ({"location": {"lat": 0}}, "location.lon"),
    ({"sensor_id": ""}, "sensor_id"),
]


@pytest.mark.parametrize("change", VALID_EDGES)
def test_valid_edges_accepted(change):
    assert len(parse(observation_json(**change))) == 1


@pytest.mark.parametrize(("change", "field"), INVALID)
def test_invalid_field_rejected(change, field):
    with pytest.raises(ApiError) as exc:
        parse(observation_json(**change))
    assert exc.value.status_code == 422
    assert [(d["index"], d["field"]) for d in exc.value.details] == [(0, field)]


@pytest.mark.parametrize(
    "field", ["sensor_id", "timestamp", "frequency_mhz", "bandwidth_khz", "power_dbm", "location"]
)
def test_every_field_required(field):
    body = observation_json()
    del body[field]
    with pytest.raises(ApiError) as exc:
        parse(body)
    assert exc.value.details[0]["field"] == field


def test_batch_lists_every_invalid_item():
    batch = [
        observation_json(),
        observation_json(power_dbm=99),
        observation_json(),
        observation_json(frequency_mhz=0, bandwidth_khz=0),
    ]
    with pytest.raises(ApiError) as exc:
        parse(batch)
    found = sorted((d["index"], d["field"]) for d in exc.value.details)
    assert found == [(1, "power_dbm"), (3, "bandwidth_khz"), (3, "frequency_mhz")]


def test_valid_batch_parsed_and_normalized():
    observations = parse(
        [observation_json(), observation_json(timestamp="2026-10-06T13:15:30+03:00")]
    )
    assert observations[0].timestamp == observations[1].timestamp
    assert observations[0].observation_id == observations[1].observation_id


@pytest.mark.parametrize("payload", ["text", 5, None, [1, 2]])
def test_body_must_be_object_or_array_of_objects(payload):
    with pytest.raises(ApiError) as exc:
        parse(payload)
    assert exc.value.status_code == 422


def test_batch_over_limit_rejected_before_validation():
    with pytest.raises(ApiError) as exc:
        parse([observation_json()] * 3, max_batch_size=2)
    assert exc.value.status_code == 413
    assert exc.value.error == "batch_too_large"


# --- future timestamps (DECISIONS §6) ---------------------------------------


def test_timestamp_up_to_clock_skew_in_future_accepted():
    assert len(parse(observation_json(timestamp=iso(NOW + timedelta(seconds=60))))) == 1


def test_timestamp_beyond_clock_skew_rejected():
    with pytest.raises(ApiError) as exc:
        parse(observation_json(timestamp=iso(NOW + timedelta(seconds=61))))
    assert exc.value.status_code == 422
    [detail] = exc.value.details
    assert (detail["index"], detail["field"]) == (0, "timestamp")
    assert "future" in detail["reason"]


def test_future_check_uses_utc_instant_not_local_offset():
    # 13:15:30+03:00 is 10:15:30Z, i.e. in the past relative to NOW.
    assert len(parse(observation_json(timestamp="2026-10-06T13:15:30+03:00"))) == 1


def test_old_timestamps_accepted():
    assert len(parse(observation_json(timestamp="2020-01-01T00:00:00Z"))) == 1


def test_future_item_rejects_whole_batch_with_other_errors():
    batch = [
        observation_json(),
        observation_json(timestamp=iso(NOW + timedelta(hours=2))),
        observation_json(power_dbm=99),
    ]
    with pytest.raises(ApiError) as exc:
        parse(batch)
    found = sorted((d["index"], d["field"]) for d in exc.value.details)
    assert found == [(1, "timestamp"), (2, "power_dbm")]
