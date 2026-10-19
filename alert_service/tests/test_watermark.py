from datetime import timedelta

from alert_service.domain.watermark import Watermark
from alert_service.tests.fakes import T0, FakeClock


def s(seconds: float) -> timedelta:
    return timedelta(seconds=seconds)


def make(clock: FakeClock) -> Watermark:
    return Watermark(clock, allowed_lateness_s=30, idle_timeout_s=30)


def test_no_watermark_before_events_and_before_idle_timeout():
    clock = FakeClock(T0)
    wm = make(clock)
    clock.advance(29)
    assert wm.current() is None
    assert not wm.is_late(T0 - s(1000))


def test_watermark_is_max_event_time_minus_lateness():
    wm = make(FakeClock(T0))
    wm.observe(T0 + s(100))
    wm.observe(T0 + s(50))  # out of order: never moves the watermark backwards
    assert wm.current() == T0 + s(70)
    assert wm.is_late(T0 + s(69))
    assert not wm.is_late(T0 + s(70))


def test_idle_fallback_advances_by_wall_clock():
    clock = FakeClock(T0)
    wm = make(clock)
    wm.observe(T0 + s(10))
    clock.advance(29)
    assert wm.current() == T0 - s(20)  # not idle yet
    clock.advance(1)
    assert wm.current() == T0 + s(10)  # 30 s idle: advanced by 30 s
    clock.advance(5)
    assert wm.current() == T0 + s(15)  # keeps following the wall clock


def test_idle_fallback_applies_before_any_event():
    clock = FakeClock(T0)
    wm = make(clock)
    clock.advance(31)
    assert wm.current() == T0 + s(1)  # startup time + 31 s elapsed - 30 s lateness


def test_new_event_after_idle_does_not_move_watermark_back():
    clock = FakeClock(T0)
    wm = make(clock)
    wm.observe(T0 + s(10))
    clock.advance(100)
    assert wm.current() == T0 + s(80)
    wm.observe(T0 + s(20))  # an event older than the advanced max
    assert wm.current() == T0 + s(80)
    clock.advance(10)  # an event just arrived, so not idle any more
    assert wm.current() == T0 + s(80)
