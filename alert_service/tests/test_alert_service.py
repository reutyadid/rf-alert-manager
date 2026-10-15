import itertools
from datetime import timedelta

import pytest

from alert_service.domain.errors import AlertNotFound, InvalidTransition
from alert_service.domain.models import (
    Alert,
    AlertFilter,
    AlertState,
    MatchOutcome,
    RuleMatch,
    RuleParams,
)
from alert_service.domain.service import AlertService
from alert_service.domain.signal import compute_signal_key
from alert_service.domain.watermark import Watermark
from alert_service.tests.fakes import T0, FakeClock, InMemoryAlertRepository

pytestmark = pytest.mark.anyio

PARAMS = RuleParams(min_duration_s=10, max_gap_s=3, resolve_after_s=60, severity="HIGH")
_ids = itertools.count()


def at(seconds: float):
    return T0 + timedelta(seconds=seconds)


def match(
    t: float,
    *,
    rule_id: str = "rule-ism",
    freq: float = 2437.0,
    power: float = -40.0,
    sensor: str = "sensor-01",
    params: RuleParams = PARAMS,
    obs_id: str | None = None,
) -> RuleMatch:
    return RuleMatch(
        observation_id=obs_id or f"obs-{next(_ids)}",
        rule_id=rule_id,
        sensor_id=sensor,
        timestamp=at(t),
        frequency_mhz=freq,
        power_dbm=power,
        rule=params,
    )


def build(repo: InMemoryAlertRepository | None = None, max_clock_skew_s: float = 3600):
    # The wall clock stays at T0 in most tests while event time moves on, so the default
    # skew is large; the clock-skew tests pass a small one.
    clock = FakeClock(T0)
    repo = repo or InMemoryAlertRepository()
    watermark = Watermark(clock, allowed_lateness_s=30, idle_timeout_s=30)
    service = AlertService(
        repo, clock, watermark, freq_tolerance_mhz=0.1, max_clock_skew_s=max_clock_skew_s
    )
    return service, repo, clock


async def feed(service: AlertService, times, **kwargs) -> list[MatchOutcome]:
    return [await service.handle_match(match(t, **kwargs)) for t in times]


async def alerts(service: AlertService) -> list[Alert]:
    return await service.list_alerts(AlertFilter())


# --- opening -------------------------------------------------------------------------


async def test_persistent_emitter_opens_one_alert():
    service, _, _ = build()
    outcomes = await feed(service, range(0, 10))
    assert await alerts(service) == []
    assert set(outcomes) == {MatchOutcome.PENDING}

    assert await service.handle_match(match(10, power=-30.0, sensor="sensor-02")) == (
        MatchOutcome.OPENED
    )
    [alert] = await alerts(service)
    assert alert.state is AlertState.OPEN
    assert alert.rule_id == "rule-ism"
    assert alert.frequency_mhz == 2437.0
    assert alert.severity == "HIGH"
    assert alert.first_seen == at(0)
    assert alert.last_seen == at(10)
    # The alert is built from the whole pending run, not only the opening event.
    assert alert.occurrences == 11
    assert alert.peak_power_dbm == -30.0
    assert alert.sensor_ids == ["sensor-01", "sensor-02"]
    _, contributions = await service.get_alert(alert.alert_id)
    assert len(contributions) == 11


async def test_short_burst_does_not_open():
    service, _, _ = build()
    await feed(service, range(0, 6))
    assert await alerts(service) == []


async def test_two_bursts_with_long_gap_do_not_open():
    service, _, _ = build()
    await feed(service, range(0, 6))  # 0..5
    await feed(service, range(12, 18))  # gap of 7 s > max_gap 3 s; total span 17 s
    assert await alerts(service) == []


async def test_one_missing_observation_does_not_break_the_run():
    service, _, _ = build()
    await feed(service, [t for t in range(0, 11) if t != 4])  # gap of 2 s <= max_gap
    assert len(await alerts(service)) == 1


async def test_out_of_order_event_extends_run_backwards_and_opens():
    service, _, _ = build()
    await feed(service, range(3, 11))  # span 7 s
    await feed(service, [1])  # 2 s before first_ts: extends to span 9 s
    assert await alerts(service) == []
    assert await service.handle_match(match(0)) is MatchOutcome.OPENED
    [alert] = await alerts(service)
    assert alert.first_seen == at(0)
    assert alert.last_seen == at(10)


async def test_event_far_before_the_run_is_ignored():
    service, _, _ = build()
    await feed(service, range(10, 16))
    assert await service.handle_match(match(5)) is MatchOutcome.IGNORED
    await feed(service, range(16, 20))  # 10..19 is 9 s: the ignored event did not count
    assert await alerts(service) == []


async def test_pending_run_uses_params_of_most_recent_event():
    service, _, _ = build()
    await feed(service, range(0, 5))
    fast = RuleParams(min_duration_s=5, max_gap_s=3, resolve_after_s=120, severity="LOW")
    assert await service.handle_match(match(5, params=fast)) is MatchOutcome.OPENED
    [alert] = await alerts(service)
    assert alert.severity == "LOW"
    assert alert.resolve_after_s == 120


async def test_different_frequencies_open_separate_alerts():
    service, _, _ = build()
    await feed(service, range(0, 11), freq=2437.0)
    await feed(service, range(0, 11), freq=2462.0)
    assert sorted(a.frequency_mhz for a in await alerts(service)) == [2437.0, 2462.0]


# --- dedup and idempotency -----------------------------------------------------------


async def test_matches_update_one_alert_from_several_sensors():
    service, _, _ = build()
    for t in range(0, 21):
        await service.handle_match(
            match(t, sensor=f"sensor-0{t % 3}", power=-45.0 + (5 if t == 15 else 0))
        )
    [alert] = await alerts(service)
    assert alert.occurrences == 21
    assert alert.last_seen == at(20)
    assert alert.peak_power_dbm == -40.0
    assert alert.sensor_ids == ["sensor-00", "sensor-01", "sensor-02"]
    assert alert.signal_key == compute_signal_key("rule-ism", 2437.0, 0.1)


async def test_late_out_of_order_match_updates_first_seen():
    service, _, _ = build()
    await feed(service, range(5, 16))
    await service.handle_match(match(1))  # older than first_seen, newer than the watermark
    [alert] = await alerts(service)
    assert alert.first_seen == at(1)
    assert alert.last_seen == at(15)


async def test_duplicate_observation_id_is_skipped_on_open_alert():
    service, _, _ = build()
    await feed(service, range(0, 11))
    repeat = match(11, obs_id="obs-repeat")
    assert await service.handle_match(repeat) is MatchOutcome.UPDATED
    assert await service.handle_match(repeat) is MatchOutcome.DUPLICATE
    [alert] = await alerts(service)
    assert alert.occurrences == 12


async def test_duplicate_observation_id_is_skipped_in_pending_run():
    service, _, _ = build()
    await service.handle_match(match(0, obs_id="obs-a"))
    assert await service.handle_match(match(0, obs_id="obs-a")) is MatchOutcome.DUPLICATE
    await feed(service, range(1, 11))
    [alert] = await alerts(service)
    assert alert.occurrences == 11


# --- event time and auto-resolve -----------------------------------------------------


async def test_auto_resolve_after_resolve_after_s_of_event_time():
    service, _, _ = build()
    await feed(service, range(0, 11))  # last_seen = 10
    # Another signal pushes event time forward: watermark = 100 - 30 = 70; 70 - 10 = 60.
    await service.handle_match(match(100, freq=100.0))
    assert await service.sweep() == []
    await service.handle_match(match(101, freq=100.0))  # watermark 71: 61 s > 60 s
    [resolved] = await service.sweep()
    assert resolved.state is AlertState.RESOLVED
    assert resolved.frequency_mhz == 2437.0


async def test_late_event_beyond_watermark_is_ignored():
    service, _, _ = build()
    await service.handle_match(match(100, freq=100.0))  # watermark = 70
    outcomes = await feed(service, range(40, 51))  # would open an alert if not late
    assert set(outcomes) == {MatchOutcome.LATE}
    assert await alerts(service) == []
    assert service.pending_run_count == 1  # only the run of the freq=100.0 signal


async def test_match_after_auto_resolve_starts_a_new_alert():
    service, _, _ = build()
    await feed(service, range(0, 11))
    await service.handle_match(match(101, freq=100.0))
    await service.sweep()
    await feed(service, range(110, 121))
    states = sorted(a.state for a in await alerts(service))
    assert states == [AlertState.OPEN, AlertState.RESOLVED]


async def test_idle_fallback_resolves_when_all_sensors_go_silent():
    service, _, clock = build()
    await feed(service, range(0, 11))  # max event time 10, last arrival at wall time 0
    clock.advance(90)  # watermark = 10 + 90 - 30 = 70: 60 s, not more
    assert await service.sweep() == []
    clock.advance(10)  # watermark = 80
    assert len(await service.sweep()) == 1


async def test_open_alert_from_before_restart_resolves_without_any_event():
    repo = InMemoryAlertRepository()
    service, _, _ = build(repo)
    await feed(service, range(0, 11))
    [alert] = await alerts(service)

    # Restart: same database, fresh in-memory state, emitter already gone.
    restarted, _, clock = build(repo)
    clock.advance(29)
    assert await restarted.sweep() == []  # no event and not idle yet: no watermark
    clock.advance(42)  # idle: watermark = start + 71 - 30 = 41; 41 - 10 = 31 s, not yet
    assert await restarted.sweep() == []
    clock.advance(30)  # watermark = 71: 61 s > 60 s
    [resolved] = await restarted.sweep()
    assert resolved.alert_id == alert.alert_id


async def test_sweep_drops_stale_pending_runs():
    service, _, _ = build()
    await feed(service, range(0, 5))
    assert service.pending_run_count == 1
    await service.handle_match(match(100, freq=100.0))  # watermark 70: run ended at 4
    await service.sweep()
    assert service.pending_run_count == 1  # only the new freq=100.0 run


# --- ack and resolve -----------------------------------------------------------------


async def test_ack_does_not_stop_updates():
    service, _, _ = build()
    await feed(service, range(0, 11))
    [alert] = await alerts(service)
    acked = await service.acknowledge(alert.alert_id)
    assert acked.state is AlertState.ACKNOWLEDGED
    assert await service.handle_match(match(11)) is MatchOutcome.UPDATED
    [alert] = await alerts(service)
    assert alert.state is AlertState.ACKNOWLEDGED
    assert alert.occurrences == 12


async def test_ack_is_idempotent_but_not_allowed_after_resolve():
    service, _, _ = build()
    await feed(service, range(0, 11))
    [alert] = await alerts(service)
    await service.acknowledge(alert.alert_id)
    again = await service.acknowledge(alert.alert_id)
    assert again.state is AlertState.ACKNOWLEDGED
    await service.resolve(alert.alert_id)
    with pytest.raises(InvalidTransition):
        await service.acknowledge(alert.alert_id)


async def test_manual_resolve_from_open_and_new_match_starts_new_run():
    service, _, _ = build()
    await feed(service, range(0, 11))
    [alert] = await alerts(service)
    resolved = await service.resolve(alert.alert_id)
    assert resolved.state is AlertState.RESOLVED
    with pytest.raises(InvalidTransition):
        await service.resolve(alert.alert_id)

    assert await service.handle_match(match(11)) is MatchOutcome.PENDING
    await feed(service, range(12, 22))
    states = sorted(a.state for a in await alerts(service))
    assert states == [AlertState.OPEN, AlertState.RESOLVED]


async def test_unknown_alert_raises_not_found():
    service, _, _ = build()
    with pytest.raises(AlertNotFound):
        await service.acknowledge("alrt-missing")
    with pytest.raises(AlertNotFound):
        await service.resolve("alrt-missing")
    with pytest.raises(AlertNotFound):
        await service.get_alert("alrt-missing")


async def test_sweep_resolves_acknowledged_alert():
    service, _, _ = build()
    await feed(service, range(0, 11))
    [alert] = await alerts(service)
    await service.acknowledge(alert.alert_id)
    await service.handle_match(match(101, freq=100.0))  # watermark 71: 61 s > 60 s
    [resolved] = await service.sweep()
    assert resolved.state is AlertState.RESOLVED


async def test_replayed_pending_observation_after_open_is_a_duplicate():
    # Crash after the alert was written but before XACK: Redis redelivers the opening batch.
    service, _, _ = build()
    for t in range(0, 11):
        await service.handle_match(match(t, obs_id=f"replay-{t}"))
    for t in range(0, 11):
        assert await service.handle_match(match(t, obs_id=f"replay-{t}")) is (
            MatchOutcome.DUPLICATE
        )
    [alert] = await alerts(service)
    assert alert.occurrences == 11


# --- clock skew ----------------------------------------------------------------------


async def test_event_too_far_in_the_future_is_rejected_and_does_not_move_the_watermark():
    service, _, _ = build(max_clock_skew_s=60)  # wall clock = T0
    assert await service.handle_match(match(61, freq=100.0)) is MatchOutcome.FUTURE
    assert service.pending_run_count == 0
    # Had the watermark moved to 61 - 30 = 31, this would be LATE. It is processed normally.
    assert await service.handle_match(match(0)) is MatchOutcome.PENDING


async def test_event_within_clock_skew_is_accepted():
    service, _, _ = build(max_clock_skew_s=60)
    assert await service.handle_match(match(60)) is MatchOutcome.PENDING


async def test_future_event_cannot_resolve_open_alerts():
    service, _, _ = build(max_clock_skew_s=60)
    await feed(service, range(0, 11))
    # A sensor with a clock a year ahead.
    await service.handle_match(match(365 * 24 * 3600))
    assert await service.sweep() == []
    [alert] = await alerts(service)
    assert alert.state is AlertState.OPEN
