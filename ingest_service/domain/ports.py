"""Ports the domain depends on. Adapters implement them; tests use in-memory fakes.

Adapters raise DependencyUnavailable when the database or the broker cannot be reached.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol, Self

from ingest_service.domain.models import Observation, ObservationQuery, Rule, Sensor


class IngestUnitOfWork(Protocol):
    """One database transaction plus the events to publish with it (docs/adr/0001).

    The caller must call commit(); leaving the block without a successful commit rolls back.
    State flags let the caller tell what happened, whichever line raised:
      published  True only after XADD returned successfully
      committed  True only after COMMIT returned successfully
      stage      the step of commit() in progress or failed ("publish", "commit", ...);
                 None before commit() and after it succeeds
    """

    published: bool
    committed: bool
    stage: str | None

    async def __aenter__(self) -> Self: ...

    async def __aexit__(self, exc_type, exc, tb) -> None: ...

    async def insert_observations(self, observations: list[Observation]) -> list[Observation]:
        """Insert, skipping duplicates. Returns only the rows that were actually inserted."""
        ...

    async def touch_sensors(self, last_seen: dict[str, datetime]) -> None:
        """last_seen = max(existing, given) per sensor."""
        ...

    def add_events(self, events: list[dict]) -> None:
        """Stage rule_match events. They are published only by commit()."""
        ...

    async def commit(self) -> None:
        """Final steps, in this order and nothing in between: publish staged events, COMMIT."""
        ...


UnitOfWorkFactory = Callable[[], IngestUnitOfWork]


class EventPublisher(Protocol):
    """Used by the unit of work's commit(), never by domain services directly."""

    async def publish(self, events: list[dict]) -> None:
        """Append all events to the rule_matches stream. Raises if any could not be sent."""
        ...


class MetricsPort(Protocol):
    def phantom_candidates(self, rule_ids: list[str]) -> None:
        """Events were published but their transaction did not commit. One rule_id per event.

        An upper bound on phantoms: a client retry (same uuid5) makes most of them harmless.
        """
        ...


class RuleRepository(Protocol):
    async def list(self) -> list[Rule]: ...

    async def list_enabled(self) -> list[Rule]: ...

    async def get(self, rule_id: str) -> Rule | None: ...

    async def create(self, rule: Rule) -> bool:
        """False if a rule with this id already exists."""
        ...

    async def update(self, rule: Rule) -> bool:
        """False if the rule does not exist."""
        ...

    async def delete(self, rule_id: str) -> bool:
        """False if the rule does not exist."""
        ...

    async def seed_if_empty(self, rules: list[Rule]) -> int:
        """Insert the rules only if the table is empty (atomically). Returns how many."""
        ...


class ObservationReader(Protocol):
    async def query(self, query: ObservationQuery) -> list[Observation]: ...


class SensorReader(Protocol):
    async def list(self) -> list[Sensor]: ...
