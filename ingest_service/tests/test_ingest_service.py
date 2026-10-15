import asyncio
import logging
from datetime import timedelta

import pytest

from ingest_service.domain.errors import DependencyUnavailable
from ingest_service.domain.ingest import IngestService
from ingest_service.tests.builders import T0, a_rule, an_observation
from ingest_service.tests.fakes import FakeMetrics, FakePublisher, FakeRuleRepository, FakeStore


def make_service(rules=None):
    store = FakeStore()
    publisher = FakePublisher()
    repo = FakeRuleRepository([a_rule()] if rules is None else rules)
    service = IngestService(repo, store.uow, publisher, default_max_gap_s=3.0)
    return service, store, publisher


def ingest(service, observations):
    return asyncio.run(service.ingest(observations))


def test_stores_and_publishes_matching_observation():
    service, store, publisher = make_service()
    obs = an_observation()

    result = ingest(service, [obs])

    assert (result.accepted, result.duplicates, result.published) == (1, 0, 1)
    assert obs.observation_id in store.observations
    assert [e["observation_id"] for e in publisher.events] == [obs.observation_id]


def test_non_matching_observation_is_stored_but_not_published():
    service, store, publisher = make_service()

    result = ingest(service, [an_observation(power_dbm=-90.0)])

    assert (result.accepted, result.published) == (1, 0)
    assert len(store.observations) == 1
    assert publisher.events == []


def test_duplicates_are_skipped_and_not_republished():
    service, store, publisher = make_service()
    ingest(service, [an_observation()])

    result = ingest(service, [an_observation(), an_observation(sensor_id="sensor-04")])

    assert (result.accepted, result.duplicates, result.published) == (1, 1, 1)
    assert len(publisher.events) == 2
    assert {e["sensor_id"] for e in publisher.events} == {"sensor-03", "sensor-04"}


def test_duplicate_inside_one_batch_is_published_once():
    service, _, publisher = make_service()

    result = ingest(service, [an_observation(), an_observation()])

    assert (result.accepted, result.duplicates) == (1, 1)
    assert len(publisher.events) == 1


def test_one_event_per_matching_rule():
    service, _, publisher = make_service([a_rule(), a_rule(rule_id="second")])

    ingest(service, [an_observation()])

    assert sorted(e["rule_id"] for e in publisher.events) == ["rule-ism-high-power", "second"]


def test_disabled_rule_produces_no_events():
    service, _, publisher = make_service([a_rule(enabled=False)])

    ingest(service, [an_observation()])

    assert publisher.events == []


def test_publish_failure_stores_nothing():
    service, store, publisher = make_service()
    publisher.fail = True

    with pytest.raises(DependencyUnavailable):
        ingest(service, [an_observation()])

    assert store.observations == {}
    assert store.sensors == {}


def test_retry_after_failed_commit_publishes_same_observation_id():
    service, store, publisher = make_service()
    store.fail_on_commit = True
    with pytest.raises(DependencyUnavailable):
        ingest(service, [an_observation()])
    assert store.observations == {}
    assert len(publisher.events) == 1  # event went out before the failed commit

    store.fail_on_commit = False
    ingest(service, [an_observation()])

    ids = [e["observation_id"] for e in publisher.events]
    assert len(ids) == 2 and ids[0] == ids[1]  # alert-service dedupes the repeat


def test_sensor_last_seen_is_max_event_time():
    service, store, _ = make_service()
    later = T0 + timedelta(seconds=10)

    ingest(service, [an_observation(timestamp=later), an_observation(timestamp=T0)])
    ingest(service, [an_observation(timestamp=T0 - timedelta(minutes=5))])  # delayed batch

    assert store.sensors == {"sensor-03": later}


def test_empty_batch_is_a_noop():
    service, store, publisher = make_service()

    result = ingest(service, [])

    assert (result.accepted, result.duplicates, result.published) == (0, 0, 0)
    assert store.commits == 0


# --- phantom events: XADD succeeded, then COMMIT failed (docs/adr/0001) ----


def make_measured_service(rules=None):
    store = FakeStore()
    publisher = FakePublisher()
    metrics = FakeMetrics()
    repo = FakeRuleRepository([a_rule()] if rules is None else rules)
    service = IngestService(repo, store.uow, publisher, default_max_gap_s=3.0, metrics=metrics)
    return service, store, publisher, metrics


def test_commit_failure_after_publish_counts_phantoms_and_reraises(caplog):
    service, store, publisher, metrics = make_measured_service([a_rule(), a_rule(rule_id="second")])
    store.fail_on_commit = True

    with caplog.at_level(logging.ERROR), pytest.raises(DependencyUnavailable):
        ingest(service, [an_observation()])

    assert sorted(metrics.phantom_rule_ids) == ["rule-ism-high-power", "second"]
    assert len(publisher.events) == 2
    record = next(r for r in caplog.records if "phantom" in r.getMessage())
    assert record.published_events == 2
    assert record.observation_ids == [an_observation().observation_id]
    assert record.error_type == "DependencyUnavailable"


def test_publish_failure_is_not_a_phantom():
    service, store, publisher, metrics = make_measured_service()
    publisher.fail = True

    with pytest.raises(DependencyUnavailable):
        ingest(service, [an_observation()])

    assert metrics.phantom_rule_ids == []
    assert store.observations == {}


def test_commit_failure_without_published_events_is_not_a_phantom():
    service, store, _, metrics = make_measured_service()
    store.fail_on_commit = True

    with pytest.raises(DependencyUnavailable):
        ingest(service, [an_observation(power_dbm=-90.0)])  # matches no rule

    assert metrics.phantom_rule_ids == []


def test_happy_path_is_not_a_phantom():
    service, _, publisher, metrics = make_measured_service()

    ingest(service, [an_observation()])

    assert len(publisher.events) == 1
    assert metrics.phantom_rule_ids == []
