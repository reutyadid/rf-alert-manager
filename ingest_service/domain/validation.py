"""Validation rules that need more than the wire format (the API layer checks types/ranges)."""

from __future__ import annotations

from datetime import datetime, timedelta

DEFAULT_MAX_CLOCK_SKEW_S = 60.0


def is_too_far_in_future(timestamp: datetime, now: datetime, max_clock_skew_s: float) -> bool:
    """A sensor clock ahead by more than the allowed skew (DECISIONS §6).

    Such an event could never affect alerts (alert-service drops it to protect its
    watermark), so it is rejected at ingest and the sensor learns its clock is wrong.
    """
    return timestamp > now + timedelta(seconds=max_clock_skew_s)
