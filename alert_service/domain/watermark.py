from datetime import datetime, timedelta

from alert_service.domain.ports import Clock


class Watermark:
    """Global event-time watermark: max event time seen minus the allowed lateness.

    Idle fallback: when no event has arrived for idle_timeout_s of wall-clock time, the max
    event time advances with the wall clock. Before the first event the startup time is the
    base, so alerts left OPEN by a previous run still resolve after a restart.
    """

    def __init__(self, clock: Clock, allowed_lateness_s: float, idle_timeout_s: float) -> None:
        self._clock = clock
        self._lateness = timedelta(seconds=allowed_lateness_s)
        self._idle_timeout = timedelta(seconds=idle_timeout_s)
        self._max_event_time: datetime | None = None
        now = clock.now()
        self._last_arrival = now
        self._last_advance = now

    def observe(self, event_time: datetime) -> None:
        self._advance_if_idle()
        if self._max_event_time is None or event_time > self._max_event_time:
            self._max_event_time = event_time
        now = self._clock.now()
        self._last_arrival = now
        self._last_advance = now

    def current(self) -> datetime | None:
        self._advance_if_idle()
        if self._max_event_time is None:
            return None
        return self._max_event_time - self._lateness

    def is_late(self, event_time: datetime) -> bool:
        watermark = self.current()
        return watermark is not None and event_time < watermark

    def _advance_if_idle(self) -> None:
        now = self._clock.now()
        if now - self._last_arrival < self._idle_timeout:
            return
        base = self._max_event_time if self._max_event_time is not None else self._last_advance
        self._max_event_time = base + (now - self._last_advance)
        self._last_advance = now
