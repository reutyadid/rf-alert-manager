from datetime import UTC, datetime, timedelta, timezone

import pytest

from ingest_service.domain.identity import (
    canonical_frequency,
    canonical_timestamp,
    observation_id,
)
from ingest_service.tests.builders import T0, an_observation

PLUS_3 = timezone(timedelta(hours=3))


def test_same_observation_twice_gets_same_id():
    assert an_observation().observation_id == an_observation().observation_id


@pytest.mark.parametrize(
    "timestamp",
    [
        datetime(2026, 10, 6, 10, 15, 30, tzinfo=UTC),
        datetime(2026, 10, 6, 10, 15, 30, tzinfo=timezone(timedelta(0))),
        datetime(2026, 10, 6, 13, 15, 30, tzinfo=PLUS_3),
    ],
)
@pytest.mark.parametrize("frequency", [2437, 2437.0, 2437.0000001])
def test_equivalent_inputs_get_same_id(timestamp, frequency):
    reference = observation_id("sensor-03", T0, 2437.0)
    assert observation_id("sensor-03", timestamp, frequency) == reference


@pytest.mark.parametrize(
    "change",
    [
        {"sensor_id": "sensor-04"},
        {"timestamp": T0 + timedelta(seconds=1)},
        {"timestamp": T0 + timedelta(microseconds=1)},
        {"frequency_mhz": 2437.000001},
    ],
)
def test_different_observation_gets_different_id(change):
    assert an_observation(**change).observation_id != an_observation().observation_id


def test_id_does_not_depend_on_power_or_location():
    a = an_observation(power_dbm=-40.0, lat=10.0)
    assert a.observation_id == an_observation().observation_id


def test_canonical_timestamp_is_utc_with_z():
    assert canonical_timestamp(datetime(2026, 10, 6, 13, 15, 30, tzinfo=PLUS_3)) == (
        "2026-10-06T10:15:30Z"
    )
    assert canonical_timestamp(T0.replace(microsecond=250000)) == "2026-10-06T10:15:30.250000Z"


def test_canonical_timestamp_rejects_naive():
    with pytest.raises(ValueError):
        canonical_timestamp(datetime(2026, 10, 6, 10, 15, 30))


def test_canonical_frequency_fixed_format():
    assert canonical_frequency(2437) == "2437.000000"
    assert canonical_frequency(1.5) == "1.500000"


def test_observation_is_normalized():
    obs = an_observation(timestamp=datetime(2026, 10, 6, 13, 15, 30, tzinfo=PLUS_3))
    assert obs.timestamp == T0
    assert obs.timestamp.utcoffset() == timedelta(0)
    assert an_observation(frequency_mhz=2437.0000001).frequency_mhz == 2437.0
