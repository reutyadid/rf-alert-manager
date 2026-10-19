"""Simulator tests. No network: scenarios are pure, HTTP and time are injected.

The simulator runtime is stdlib only; these tests may import ingest_service to check the
generated traffic against the real validation and rule evaluation.
"""

import json
import math
import random
from datetime import UTC, datetime, timedelta

import pytest

from ingest_service.adapters.rules_yaml import read_rules_file
from ingest_service.api.observations import parse_observations
from ingest_service.api.schemas import RuleIn
from ingest_service.domain.evaluators import matching_rules
from ingest_service.main import DEFAULT_RULES_FILE
from simulator import simulate

START = datetime(2026, 10, 18, 12, 0, 0, tzinfo=UTC)
TICKS = range(0, 240)
SEED_RULES = [
    RuleIn.model_validate(r).to_domain(r["rule_id"]) for r in read_rules_file(DEFAULT_RULES_FILE)
]
ISM_RULE = next(r for r in SEED_RULES if r.rule_id == "rule-ism-high-power")


def tick(n, scenarios, rate=5.0, seed=1):
    return simulate.observations_for_tick(n, START, random.Random(seed), set(scenarios), rate)


def parsed(n, scenarios, **kw):
    """Run a tick through ingest's real validation (also normalizes like ingest does)."""
    return parse_observations(tick(n, scenarios, **kw), now=START + timedelta(seconds=n))


def matches(obs, rule):
    return bool(matching_rules(obs, [rule]))


def alert_bucket(frequency_mhz, tolerance_mhz=0.1):
    # Same formula as alert-service compute_signal_key (alert_service/domain/signal.py).
    return round(math.floor(frequency_mhz / tolerance_mhz + 0.5) * tolerance_mhz, 6)


# --- generated traffic is valid and unique ------------------------------------------------


def test_every_tick_passes_ingest_validation():
    for n in TICKS:
        parsed(n, simulate.ALL_SCENARIOS, rate=8)  # raises ApiError if anything is invalid


def test_no_accidental_duplicates_across_a_whole_run():
    rng = random.Random(7)
    keys = []
    for n in TICKS:
        batch = simulate.observations_for_tick(n, START, rng, set(simulate.ALL_SCENARIOS), 8)
        for o in parse_observations(batch, now=START + timedelta(seconds=n)):
            keys.append((o.sensor_id, o.timestamp, o.frequency_mhz))
    assert len(keys) == len(set(keys))


# --- scenarios ----------------------------------------------------------------------------


def test_background_never_matches_any_seeded_rule():
    for seed in range(5):
        for n in TICKS:
            for obs in parsed(n, ["background"], rate=10, seed=seed):
                assert matching_rules(obs, SEED_RULES) == []


def test_background_rate():
    counts = [len(tick(n, ["background"], rate=2.5, seed=n)) for n in range(400)]
    assert all(c in (2, 3) for c in counts)
    assert 2.3 < sum(counts) / len(counts) < 2.7


def test_emitter_matches_every_tick_from_three_sensors():
    # One match per second per sensor: never a gap near max_gap (3 s).
    for n in TICKS:
        matching = [o for o in parsed(n, ["emitter"]) if matches(o, ISM_RULE)]
        assert sorted(o.sensor_id for o in matching) == ["sensor-01", "sensor-02", "sensor-03"]


def test_emitter_weak_sensor_is_below_threshold():
    for n in TICKS:
        weak = [o for o in parsed(n, ["emitter"]) if o.sensor_id == "sensor-04"]
        assert len(weak) == 1 and not matches(weak[0], ISM_RULE)


def test_emitter_jitter_never_crosses_a_signal_key_bucket():
    for seed in range(20):
        for n in TICKS:
            for obs in parsed(n, ["emitter"], seed=seed):
                assert alert_bucket(obs.frequency_mhz) == 2437.0


def test_emitter_jitter_is_applied_and_bounded():
    freqs = [
        o["frequency_mhz"]
        for n in TICKS
        for o in tick(n, ["emitter"], seed=n)
        if o["sensor_id"] != "sensor-04"
    ]
    assert len(set(freqs)) > 100  # really varies
    assert max(abs(f - 2437.0) for f in freqs) <= simulate.EMITTER_JITTER_MHZ + 1e-9
    # Bucket edges are 2436.95 / 2437.05: the jitter bound keeps a safe margin.
    assert simulate.EMITTER_JITTER_MHZ < 0.05


def test_burst_only_between_15_and_19_and_shorter_than_min_duration():
    active = [n for n in TICKS if any(matches(o, ISM_RULE) for o in parsed(n, ["burst"]))]
    assert active == list(range(15, 20))
    assert active[-1] - active[0] < ISM_RULE.min_duration_s


def test_vanishing_runs_from_20_to_59_then_stops():
    active = [n for n in TICKS if any(matches(o, ISM_RULE) for o in parsed(n, ["vanishing"]))]
    assert active == list(range(20, 60))
    assert active[-1] - active[0] >= ISM_RULE.min_duration_s  # long enough to open


def test_scenarios_use_distinct_signal_keys():
    def bucket(scenario, n):
        return {alert_bucket(o.frequency_mhz) for o in parsed(n, [scenario])}

    assert bucket("emitter", 30) == {2437.0}
    assert bucket("burst", 16) == {2412.0}
    assert bucket("vanishing", 30) == {2462.0}


def test_late_observations_are_20_and_40_seconds_behind_and_match():
    assert tick(simulate.LATE_TICK - 1, ["late"]) == []
    obs = parsed(simulate.LATE_TICK, ["late"])
    tick_time = START + timedelta(seconds=simulate.LATE_TICK)
    lag = {o.sensor_id: (tick_time - o.timestamp).total_seconds() for o in obs}
    assert lag == {"sensor-late-20": 20.0, "sensor-late-40": 40.0}
    assert all(matches(o, ISM_RULE) for o in obs)
    assert {alert_bucket(o.frequency_mhz) for o in obs} == {2437.0}  # joins the emitter alert


# --- retries resend the exact same batch --------------------------------------------------


def test_retry_resends_the_exact_same_body():
    bodies, statuses = [], iter([503, None, 200])

    def fake_post(url, body):
        bodies.append(body)
        return next(statuses), {"accepted": 1, "duplicates": 0}

    body = json.dumps(tick(30, simulate.ALL_SCENARIOS)).encode()
    status, _ = simulate.send_batch(
        fake_post, "http://x/api/v1/observations", body, sleep=lambda s: None
    )

    assert status == 200
    assert len(bodies) == 3
    assert all(b is body for b in bodies)  # same object, not regenerated


def test_run_retries_with_the_batch_it_generated():
    bodies, statuses = [], iter([503, 200])

    def fake_post(url, body):
        bodies.append(body)
        return next(statuses), {"accepted": 4, "duplicates": 0}

    simulate.run(
        "http://x",
        duration=1,
        scenarios={"emitter"},
        rate=0,
        seed=3,
        watch=False,
        post=fake_post,
        monotonic=lambda: 0.0,
        sleep=lambda s: None,
        now=lambda: START,
        out=lambda line: None,
    )
    assert len(bodies) == 2 and bodies[0] is bodies[1]


def test_client_errors_are_not_retried():
    calls = []

    def fake_post(url, body):
        calls.append(body)
        return 422, {"error": "validation_error"}

    status, _ = simulate.send_batch(fake_post, "u", b"[]", sleep=lambda s: None)
    assert status == 422 and len(calls) == 1


# --- scheduling ---------------------------------------------------------------------------


def test_ticks_are_scheduled_against_start_without_drift():
    clock = {"t": 100.0}
    sleeps, sent_at = [], []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock["t"] += seconds

    def slow_post(url, body):
        sent_at.append(clock["t"])
        clock["t"] += 0.3  # each request takes 0.3 s
        return 200, {"accepted": 0, "duplicates": 0}

    simulate.run(
        "http://x",
        duration=5,
        scenarios={"emitter"},
        rate=0,
        seed=1,
        watch=False,
        post=slow_post,
        monotonic=lambda: clock["t"],
        sleep=fake_sleep,
        now=lambda: START,
        out=lambda line: None,
    )
    assert sent_at == pytest.approx([100.0, 101.0, 102.0, 103.0, 104.0])
    assert sleeps == pytest.approx([0.7, 0.7, 0.7, 0.7])


def test_event_time_is_start_plus_tick():
    obs = parsed(17, ["emitter"])
    assert {o.timestamp for o in obs} == {START + timedelta(seconds=17)}


# --- CLI ----------------------------------------------------------------------------------


def test_parse_args_defaults_and_validation():
    args = simulate.parse_args([])
    assert (args.url, args.rate, args.duration) == ("http://localhost", 5.0, 240)
    assert args.scenarios == set(simulate.ALL_SCENARIOS)
    assert simulate.parse_args(["--scenarios", "emitter,burst"]).scenarios == {"emitter", "burst"}
    with pytest.raises(SystemExit):
        simulate.parse_args(["--scenarios", "magic"])
