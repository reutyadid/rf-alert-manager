"""Ingest use case: store observations and publish rule matches, in one transaction."""

from __future__ import annotations

import logging
from datetime import datetime

from ingest_service.domain.evaluators import matching_rules
from ingest_service.domain.events import build_rule_match
from ingest_service.domain.models import IngestResult, Observation
from ingest_service.domain.ports import (
    EventPublisher,
    MetricsPort,
    RuleRepository,
    UnitOfWorkFactory,
)

log = logging.getLogger(__name__)

MAX_LOGGED_IDS = 20


class _NoMetrics:
    def phantom_events(self, rule_ids: list[str]) -> None:
        pass


class IngestService:
    def __init__(
        self,
        rules: RuleRepository,
        unit_of_work: UnitOfWorkFactory,
        publisher: EventPublisher,
        default_max_gap_s: float,
        metrics: MetricsPort | None = None,
    ) -> None:
        self._rules = rules
        self._unit_of_work = unit_of_work
        self._publisher = publisher
        self._default_max_gap_s = default_max_gap_s
        self._metrics = metrics or _NoMetrics()

    async def ingest(self, observations: list[Observation]) -> IngestResult:
        """Observations are already validated.

        Order (docs/adr/0001, DECISIONS §7.1): insert -> sensors -> evaluate -> publish -> commit.
        Publishing before the commit means a broker failure rolls everything back; a commit
        failure after publishing is safe because a retry produces the same observation ids.
        """
        if not observations:
            return IngestResult(accepted=0, duplicates=0, published=0)

        rules = await self._rules.list_enabled()
        published: list[dict] = []
        try:
            async with self._unit_of_work() as uow:
                inserted = await uow.insert_observations(observations)
                await uow.touch_sensors(_latest_per_sensor(observations))
                events = [
                    build_rule_match(obs, rule, self._default_max_gap_s)
                    for obs in inserted
                    for rule in matching_rules(obs, rules)
                ]
                if events:
                    await self._publisher.publish(events)
                    published = events  # nothing after this line inside the block
        except Exception as exc:
            if published:  # publish succeeded, so the failure came from COMMIT
                self._report_phantoms(published, exc)
            raise

        return IngestResult(
            accepted=len(inserted),
            duplicates=len(observations) - len(inserted),
            published=len(events),
        )

    def _report_phantoms(self, events: list[dict], exc: Exception) -> None:
        """Events are in the stream but their rows were rolled back (the ADR's known gap).

        Harmless if the client retries (same observation ids); a phantom if it never does.
        """
        observation_ids = list(dict.fromkeys(e["observation_id"] for e in events))
        log.error(
            "phantom events: published to the broker but the commit failed",
            extra={
                "published_events": len(events),
                "observation_ids": observation_ids[:MAX_LOGGED_IDS],
                "observation_ids_truncated": len(observation_ids) > MAX_LOGGED_IDS,
                "error_type": type(exc).__name__,
            },
        )
        self._metrics.phantom_events([e["rule_id"] for e in events])


def _latest_per_sensor(observations: list[Observation]) -> dict[str, datetime]:
    latest: dict[str, datetime] = {}
    for obs in observations:
        current = latest.get(obs.sensor_id)
        if current is None or obs.timestamp > current:
            latest[obs.sensor_id] = obs.timestamp
    return latest
