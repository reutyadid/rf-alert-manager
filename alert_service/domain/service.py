import asyncio
import uuid
from datetime import timedelta

from alert_service.domain.errors import AlertNotFound, InvalidTransition
from alert_service.domain.models import (
    Alert,
    AlertFilter,
    AlertState,
    Contribution,
    MatchOutcome,
    RuleMatch,
)
from alert_service.domain.pending import PendingRun
from alert_service.domain.ports import AlertRepository, Clock
from alert_service.domain.signal import compute_signal_key, round_frequency
from alert_service.domain.watermark import Watermark


class AlertService:
    """Alert lifecycle driven by event time. Single instance per deployment (DECISIONS §4.5).

    A lock serialises the consumer, the sweep and API state changes, so a match never
    updates an alert that the sweep is resolving at the same moment.
    """

    def __init__(
        self,
        repo: AlertRepository,
        clock: Clock,
        watermark: Watermark,
        freq_tolerance_mhz: float,
        max_clock_skew_s: float,
    ) -> None:
        self._repo = repo
        self._clock = clock
        self._watermark = watermark
        self._tolerance = freq_tolerance_mhz
        self._max_skew = timedelta(seconds=max_clock_skew_s)
        self._pending: dict[str, PendingRun] = {}
        self._lock = asyncio.Lock()

    @property
    def pending_run_count(self) -> int:
        return len(self._pending)

    # --- events --------------------------------------------------------------------

    async def handle_match(self, match: RuleMatch) -> MatchOutcome:
        async with self._lock:
            key = compute_signal_key(match.rule_id, match.frequency_mhz, self._tolerance)
            # A sensor clock far ahead would push the global watermark forward: every real
            # event would then be LATE and every open alert would auto-resolve.
            if match.timestamp > self._now() + self._max_skew:
                return MatchOutcome.FUTURE
            if self._watermark.is_late(match.timestamp):
                return MatchOutcome.LATE
            self._watermark.observe(match.timestamp)

            alert = await self._repo.get_active_by_signal_key(key)
            if alert is not None:
                return await self._update(alert, match)
            return await self._add_to_run(key, match)

    async def _update(self, alert: Alert, match: RuleMatch) -> MatchOutcome:
        if await self._repo.has_contribution(alert.alert_id, match.observation_id):
            return MatchOutcome.DUPLICATE
        contribution = Contribution.from_match(match)
        alert.add(contribution)
        await self._repo.add_contribution(alert, contribution)
        return MatchOutcome.UPDATED

    async def _add_to_run(self, key: str, match: RuleMatch) -> MatchOutcome:
        run = self._pending.get(key)
        if run is None:
            run = self._pending[key] = PendingRun(match)
        elif run.contains(match.observation_id):
            return MatchOutcome.DUPLICATE
        elif not run.add(match):
            return MatchOutcome.IGNORED

        if not run.is_complete():
            return MatchOutcome.PENDING
        await self._open(key, match, run)
        del self._pending[key]
        return MatchOutcome.OPENED

    async def _open(self, key: str, match: RuleMatch, run: PendingRun) -> None:
        first, *rest = run.contributions
        alert = Alert(
            alert_id=f"alrt-{uuid.uuid4().hex[:12]}",
            rule_id=match.rule_id,
            signal_key=key,
            state=AlertState.OPEN,
            # Snapshot of the rule at open time: the sweep never needs rule data.
            severity=run.params.severity,
            resolve_after_s=run.params.resolve_after_s,
            frequency_mhz=round_frequency(match.frequency_mhz, self._tolerance),
            sensor_ids=[first.sensor_id],
            first_seen=first.timestamp,
            last_seen=first.timestamp,
            occurrences=1,
            peak_power_dbm=first.power_dbm,
        )
        for c in rest:
            alert.add(c)
        await self._repo.create(alert, run.contributions)

    # --- auto-resolve ----------------------------------------------------------------

    async def sweep(self) -> list[Alert]:
        """Resolve alerts silent for resolve_after_s of event time; drop stale runs."""
        async with self._lock:
            watermark = self._watermark.current()
            if watermark is None:
                return []
            for key in [k for k, run in self._pending.items() if run.is_stale(watermark)]:
                del self._pending[key]

            resolved = []
            for alert in await self._repo.list_active():
                if watermark - alert.last_seen > timedelta(seconds=alert.resolve_after_s):
                    done = await self._repo.transition(
                        alert.alert_id, AlertState.active(), AlertState.RESOLVED, self._now()
                    )
                    if done is not None:
                        resolved.append(done)
            return resolved

    # --- operator actions ------------------------------------------------------------

    async def acknowledge(self, alert_id: str) -> Alert:
        async with self._lock:
            alert = await self._get(alert_id)
            if alert.state is AlertState.ACKNOWLEDGED:
                return alert  # idempotent
            if alert.state is not AlertState.OPEN:
                raise InvalidTransition(alert_id, alert.state, "acknowledge")
            return await self._transition(alert, {AlertState.OPEN}, AlertState.ACKNOWLEDGED)

    async def resolve(self, alert_id: str) -> Alert:
        async with self._lock:
            alert = await self._get(alert_id)
            if alert.state not in AlertState.active():
                raise InvalidTransition(alert_id, alert.state, "resolve")
            return await self._transition(alert, AlertState.active(), AlertState.RESOLVED)

    # --- queries ---------------------------------------------------------------------

    async def list_alerts(self, flt: AlertFilter) -> list[Alert]:
        return await self._repo.query(flt)

    async def get_alert(self, alert_id: str) -> tuple[Alert, list[Contribution]]:
        alert = await self._get(alert_id)
        return alert, await self._repo.list_contributions(alert_id)

    # --- helpers ---------------------------------------------------------------------

    async def _get(self, alert_id: str) -> Alert:
        alert = await self._repo.get(alert_id)
        if alert is None:
            raise AlertNotFound(alert_id)
        return alert

    async def _transition(
        self, alert: Alert, from_states: set[AlertState], to_state: AlertState
    ) -> Alert:
        updated = await self._repo.transition(alert.alert_id, from_states, to_state, self._now())
        if updated is None:  # another instance changed it first
            raise InvalidTransition(alert.alert_id, alert.state, to_state.lower())
        return updated

    def _now(self):
        return self._clock.now()
