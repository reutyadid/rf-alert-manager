import pytest

from ingest_service.api.errors import ApiError
from ingest_service.api.observations import parse_observations
from ingest_service.tests.builders import observation_json

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
    assert len(parse_observations(observation_json(**change))) == 1


@pytest.mark.parametrize(("change", "field"), INVALID)
def test_invalid_field_rejected(change, field):
    with pytest.raises(ApiError) as exc:
        parse_observations(observation_json(**change))
    assert exc.value.status_code == 422
    assert [(d["index"], d["field"]) for d in exc.value.details] == [(0, field)]


@pytest.mark.parametrize(
    "field", ["sensor_id", "timestamp", "frequency_mhz", "bandwidth_khz", "power_dbm", "location"]
)
def test_every_field_required(field):
    body = observation_json()
    del body[field]
    with pytest.raises(ApiError) as exc:
        parse_observations(body)
    assert exc.value.details[0]["field"] == field


def test_batch_lists_every_invalid_item():
    batch = [
        observation_json(),
        observation_json(power_dbm=99),
        observation_json(),
        observation_json(frequency_mhz=0, bandwidth_khz=0),
    ]
    with pytest.raises(ApiError) as exc:
        parse_observations(batch)
    found = sorted((d["index"], d["field"]) for d in exc.value.details)
    assert found == [(1, "power_dbm"), (3, "bandwidth_khz"), (3, "frequency_mhz")]


def test_valid_batch_parsed_and_normalized():
    observations = parse_observations(
        [observation_json(), observation_json(timestamp="2026-10-06T13:15:30+03:00")]
    )
    assert observations[0].timestamp == observations[1].timestamp
    assert observations[0].observation_id == observations[1].observation_id


@pytest.mark.parametrize("payload", ["text", 5, None, [1, 2]])
def test_body_must_be_object_or_array_of_objects(payload):
    with pytest.raises(ApiError) as exc:
        parse_observations(payload)
    assert exc.value.status_code == 422


def test_batch_over_limit_rejected_before_validation():
    with pytest.raises(ApiError) as exc:
        parse_observations([observation_json()] * 3, max_batch_size=2)
    assert exc.value.status_code == 413
    assert exc.value.error == "batch_too_large"
