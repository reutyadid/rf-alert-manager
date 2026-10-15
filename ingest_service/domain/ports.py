"""Ports the domain depends on. Adapters implement them; tests use in-memory fakes.

Adapters raise DependencyUnavailable when the database or the broker cannot be reached.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol, Self

from ingest_service.domain.models import Observation, ObservationQuery, Rule, Sensor


class IngestUnitOfWork(Protocol):
    """One database transaction. Commits when the block exits cleanly, rolls back otherwise."""

    async def __aenter__(self) -> Self: ...

    async def __aexit__(self, exc_type, exc, tb) -> None: ...

    async def insert_observations(self, observations: list[Observation]) -> list[Observation]:
        """Insert, skipping duplicates. Returns only the rows that were actually inserted."""
        ...

    async def touch_sensors(self, last_seen: dict[str, datetime]) -> None:
        """last_seen = max(existing, given) per sensor."""
        ...


UnitOfWorkFactory = Callable[[], IngestUnitOfWork]


class EventPublisher(Protocol):
    async def publish(self, events: list[dict]) -> None:
        """Append all events to the rule_matches stream. Raises if any could not be sent."""
        ...


class MetricsPort(Protocol):
    def phantom_events(self, rule_ids: list[str]) -> None:
        """Events were published but the commit failed (docs/adr/0001). One rule_id per event."""
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
