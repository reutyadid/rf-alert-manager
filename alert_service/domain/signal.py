"""The only place that decides whether two matches are "the same signal"."""

import math


def round_frequency(frequency_mhz: float, tolerance_mhz: float) -> float:
    # floor(x + 0.5) instead of round(): Python's round() is banker's rounding.
    buckets = math.floor(frequency_mhz / tolerance_mhz + 0.5)
    return round(buckets * tolerance_mhz, 6)


def compute_signal_key(rule_id: str, frequency_mhz: float, tolerance_mhz: float) -> str:
    """rule_id + frequency rounded to the tolerance. The sensor is deliberately not part of
    the key, so several sensors hearing one emitter contribute to one alert."""
    return f"{rule_id}@{round_frequency(frequency_mhz, tolerance_mhz)}"
