"""Ingest use case: store observations and publish rule matches, in one transaction."""

from __future__ import annotations

import logging
from datetime import datetime

from ingest_service.domain.evaluators import matching_rules
from ingest_service.domain.events import build_rule_match
from ingest_service.domain.models import IngestResult, Observation
from ingest_service.domain.ports import (
    IngestUnitOfWork,
    MetricsPort,
    RuleRepository,
    UnitOfWorkFactory,
)

log = logging.getLogger(__name__)

MAX_LOGGED_IDS = 20


class _NoMetrics:
    def phantom_candidates(self, rule_ids: list[str]) -> None:
        pass


class IngestService:
    def __init__(
        self,
        rules: RuleRepository,
        unit_of_work: UnitOfWorkFactory,
        default_max_gap_s: float,
        metrics: MetricsPort | None = None,
    ) -> None:
        self._rules = rules
        self._unit_of_work = unit_of_work
        self._default_max_gap_s = default_max_gap_s
        self._metrics = metrics or _NoMetrics()

    async def ingest(self, observations: list[Observation]) -> IngestResult:
        """Observations are already validated.

        Order (docs/adr/0001): insert -> sensors -> evaluate -> stage events, then
        uow.commit() publishes them and commits as its final steps. This service never
        talks to the broker, so nothing it does can run between publish and commit.
        """
        if not observations:
            return IngestResult(accepted=0, duplicates=0, published=0)

        rules = await self._rules.list_enabled()
        uow = self._unit_of_work()
        events: list[dict] = []
        try:
            async with uow:
                inserted = await uow.insert_observations(observations)
                await uow.touch_sensors(_latest_per_sensor(observations))
                events = [
                    build_rule_match(obs, rule, self._default_max_gap_s)
                    for obs in inserted
                    for rule in matching_rules(obs, rules)
                ]
                uow.add_events(events)
                await uow.commit()
        except BaseException as exc:  # includes cancellation between publish and commit
            # Decided from state only, not from which line raised.
            if uow.published and not uow.committed:
                self._report_phantom_candidates(uow, events, exc)
            raise

        return IngestResult(
            accepted=len(inserted),
            duplicates=len(observations) - len(inserted),
            published=len(events),
        )

    def _report_phantom_candidates(
        self, uow: IngestUnitOfWork, events: list[dict], exc: BaseException
    ) -> None:
        """Events are in the stream but their rows were not committed.

        A candidate, not a confirmed phantom: if the client retries, the same uuid5 ids are
        published again and alert-service skips them. If COMMIT failed on a network error the
        outcome is ambiguous: the server may have committed, so the rows may exist.
        """
        observation_ids = list(dict.fromkeys(e["observation_id"] for e in events))
        log.warning(
            "phantom candidates: events published but the transaction did not commit",
            extra={
                "published_events": len(events),
                "observation_ids": observation_ids[:MAX_LOGGED_IDS],
                "observation_ids_truncated": len(observation_ids) > MAX_LOGGED_IDS,
                "error_type": type(exc).__name__,
                "failed_stage": uow.stage,
                "commit_outcome_ambiguous": uow.stage == "commit",
            },
        )
        self._metrics.phantom_candidates([e["rule_id"] for e in events])


def _latest_per_sensor(observations: list[Observation]) -> dict[str, datetime]:
    latest: dict[str, datetime] = {}
    for obs in observations:
        current = latest.get(obs.sensor_id)
        if current is None or obs.timestamp > current:
            latest[obs.sensor_id] = obs.timestamp
    return latest
