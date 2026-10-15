"""In-memory fakes for the domain ports. No DB, no Redis."""

from __future__ import annotations

from datetime import datetime

from ingest_service.domain.errors import DependencyUnavailable
from ingest_service.domain.models import Observation, ObservationQuery, Rule, Sensor


class FakeStore:
    """Committed state shared by the fake unit of work and the readers."""

    def __init__(self) -> None:
        self.observations: dict[str, Observation] = {}
        self.sensors: dict[str, datetime] = {}
        self.fail_on_commit = False
        self.commits = 0

    def uow(self) -> FakeUnitOfWork:
        return FakeUnitOfWork(self)


class FakeUnitOfWork:
    """Stages writes and applies them only when the block exits without an error."""

    def __init__(self, store: FakeStore) -> None:
        self._store = store
        self._observations: dict[str, Observation] = {}
        self._sensors: dict[str, datetime] = {}

    async def __aenter__(self) -> FakeUnitOfWork:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            return  # rollback: drop staged writes
        if self._store.fail_on_commit:
            raise DependencyUnavailable("commit failed")
        self._store.observations.update(self._observations)
        for sensor_id, ts in self._sensors.items():
            current = self._store.sensors.get(sensor_id)
            self._store.sensors[sensor_id] = ts if current is None else max(current, ts)
        self._store.commits += 1

    async def insert_observations(self, observations: list[Observation]) -> list[Observation]:
        inserted = []
        for obs in observations:
            oid = obs.observation_id
            if oid in self._store.observations or oid in self._observations:
                continue
            self._observations[oid] = obs
            inserted.append(obs)
        return inserted

    async def touch_sensors(self, last_seen: dict[str, datetime]) -> None:
        for sensor_id, ts in last_seen.items():
            current = self._sensors.get(sensor_id)
            self._sensors[sensor_id] = ts if current is None else max(current, ts)


class FakePublisher:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.fail = False

    async def publish(self, events: list[dict]) -> None:
        if self.fail:
            raise DependencyUnavailable("redis down")
        self.events.extend(events)


class FakeRuleRepository:
    def __init__(self, rules: list[Rule] | None = None) -> None:
        self.rules: dict[str, Rule] = {r.rule_id: r for r in rules or []}

    async def list(self) -> list[Rule]:
        return sorted(self.rules.values(), key=lambda r: r.rule_id)

    async def list_enabled(self) -> list[Rule]:
        return [r for r in await self.list() if r.enabled]

    async def get(self, rule_id: str) -> Rule | None:
        return self.rules.get(rule_id)

    async def create(self, rule: Rule) -> bool:
        if rule.rule_id in self.rules:
            return False
        self.rules[rule.rule_id] = rule
        return True

    async def update(self, rule: Rule) -> bool:
        if rule.rule_id not in self.rules:
            return False
        self.rules[rule.rule_id] = rule
        return True

    async def delete(self, rule_id: str) -> bool:
        return self.rules.pop(rule_id, None) is not None

    async def seed_if_empty(self, rules: list[Rule]) -> int:
        if self.rules:
            return 0
        for rule in rules:
            self.rules[rule.rule_id] = rule
        return len(rules)


class FakeObservationReader:
    def __init__(self, store: FakeStore) -> None:
        self._store = store

    async def query(self, q: ObservationQuery) -> list[Observation]:
        rows = [
            o
            for o in self._store.observations.values()
            if (q.sensor_id is None or o.sensor_id == q.sensor_id)
            and (q.min_frequency_mhz is None or o.frequency_mhz >= q.min_frequency_mhz)
            and (q.max_frequency_mhz is None or o.frequency_mhz <= q.max_frequency_mhz)
            and (q.start is None or o.timestamp >= q.start)
            and (q.end is None or o.timestamp <= q.end)
        ]
        rows.sort(key=lambda o: o.timestamp, reverse=True)
        return rows[: q.limit]


class FakeSensorReader:
    def __init__(self, store: FakeStore) -> None:
        self._store = store

    async def list(self) -> list[Sensor]:
        return [Sensor(sensor_id=s, last_seen=ts) for s, ts in sorted(self._store.sensors.items())]
