from __future__ import annotations

from datetime import datetime, timedelta

from alert_service.domain.models import Contribution, RuleMatch


class PendingRun:
    """Matches for one signal key that have not yet lasted min_duration_s.

    Bounds follow the event timestamps, so out-of-order events within max_gap_s extend the
    run instead of breaking it. Contributions are kept so the alert opens with the whole run.
    Rule params are taken from the most recent event of the run.
    """

    def __init__(self, match: RuleMatch) -> None:
        self._start(match)

    def _start(self, match: RuleMatch) -> None:
        self.first_ts = match.timestamp
        self.last_ts = match.timestamp
        self.params = match.rule
        self.contributions = [Contribution.from_match(match)]

    def contains(self, observation_id: str) -> bool:
        return any(c.observation_id == observation_id for c in self.contributions)

    def add(self, match: RuleMatch) -> bool:
        """Apply a match. Returns False if the match is too early to belong to this run."""
        ts = match.timestamp
        max_gap = timedelta(seconds=match.rule.max_gap_s)
        if ts > self.last_ts + max_gap:
            self._start(match)  # gap too long: this event starts a new run
            return True
        if ts < self.first_ts - max_gap:
            return False
        self.first_ts = min(self.first_ts, ts)
        self.last_ts = max(self.last_ts, ts)
        self.params = match.rule
        self.contributions.append(Contribution.from_match(match))
        return True

    def is_complete(self) -> bool:
        return self.last_ts - self.first_ts >= timedelta(seconds=self.params.min_duration_s)

    def is_stale(self, watermark: datetime) -> bool:
        """No future (non-late) event can extend this run any more."""
        return watermark - self.last_ts > timedelta(seconds=self.params.max_gap_s)
