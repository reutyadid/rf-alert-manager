"""PostgreSQL adapters (asyncpg). Connection problems surface as DependencyUnavailable."""

from __future__ import annotations

import functools
from datetime import datetime
from importlib import resources
from typing import Self

import asyncpg

from ingest_service.domain.errors import DependencyUnavailable
from ingest_service.domain.models import (
    Band,
    Location,
    Observation,
    ObservationQuery,
    Rule,
    Sensor,
)

# Errors that mean "the database is unreachable", as opposed to a bug in a query.
CONNECTION_ERRORS = (
    OSError,
    TimeoutError,
    asyncpg.InterfaceError,
    asyncpg.PostgresConnectionError,
    asyncpg.CannotConnectNowError,
    asyncpg.TooManyConnectionsError,
)

SCHEMA_LOCK_ID = 7_316_001  # pg_advisory lock so concurrent replicas do not race on DDL


def _unavailable_on_connection_error(fn):
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except CONNECTION_ERRORS as exc:
            raise DependencyUnavailable(f"database: {exc}") from exc

    return wrapper


@_unavailable_on_connection_error
async def init_schema(pool: asyncpg.Pool) -> None:
    sql = resources.files("ingest_service.adapters").joinpath("schema.sql").read_text()
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock($1)", SCHEMA_LOCK_ID)
        await conn.execute(sql)


@_unavailable_on_connection_error
async def ping(pool: asyncpg.Pool) -> None:
    await pool.fetchval("SELECT 1")


# --- ingest unit of work --------------------------------------------------

_INSERT_OBSERVATIONS = """
INSERT INTO observations (id, sensor_id, ts, frequency_mhz, bandwidth_khz, power_dbm, lat, lon)
SELECT * FROM unnest(
    $1::uuid[], $2::text[], $3::timestamptz[], $4::float8[],
    $5::float8[], $6::float8[], $7::float8[], $8::float8[]
)
ON CONFLICT DO NOTHING
RETURNING id
"""

_TOUCH_SENSORS = """
INSERT INTO sensors (sensor_id, last_seen)
SELECT * FROM unnest($1::text[], $2::timestamptz[])
ON CONFLICT (sensor_id) DO UPDATE SET last_seen = GREATEST(sensors.last_seen, EXCLUDED.last_seen)
"""


class PostgresUnitOfWork:
    """One transaction on one pooled connection. Commit on clean exit, rollback otherwise."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._conn: asyncpg.Connection | None = None
        self._tx = None

    @_unavailable_on_connection_error
    async def __aenter__(self) -> Self:
        self._conn = await self._pool.acquire()
        try:
            self._tx = self._conn.transaction()
            await self._tx.start()
        except BaseException:
            await self._pool.release(self._conn)
            raise
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None:
                await self._tx.commit()
            else:
                await self._tx.rollback()
        except CONNECTION_ERRORS as err:
            if exc_type is None:
                raise DependencyUnavailable(f"database commit: {err}") from err
            # Rolling back after another error: keep the original error.
        finally:
            await self._pool.release(self._conn)

    @_unavailable_on_connection_error
    async def insert_observations(self, observations: list[Observation]) -> list[Observation]:
        rows = await self._conn.fetch(
            _INSERT_OBSERVATIONS,
            [o.observation_id for o in observations],
            [o.sensor_id for o in observations],
            [o.timestamp for o in observations],
            [o.frequency_mhz for o in observations],
            [o.bandwidth_khz for o in observations],
            [o.power_dbm for o in observations],
            [o.location.lat for o in observations],
            [o.location.lon for o in observations],
        )
        inserted_ids = {str(r["id"]) for r in rows}
        inserted = []
        for obs in observations:  # keep request order; a repeat inside the batch counts once
            if obs.observation_id in inserted_ids:
                inserted.append(obs)
                inserted_ids.discard(obs.observation_id)
        return inserted

    @_unavailable_on_connection_error
    async def touch_sensors(self, last_seen: dict[str, datetime]) -> None:
        ordered = sorted(last_seen.items())  # fixed lock order avoids deadlocks between batches
        await self._conn.execute(_TOUCH_SENSORS, [s for s, _ in ordered], [ts for _, ts in ordered])


# --- readers --------------------------------------------------------------


class PostgresObservationReader:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @_unavailable_on_connection_error
    async def query(self, q: ObservationQuery) -> list[Observation]:
        conditions, args = [], []

        def add(sql: str, value) -> None:
            args.append(value)
            conditions.append(sql.format(f"${len(args)}"))

        if q.sensor_id is not None:
            add("sensor_id = {}", q.sensor_id)
        if q.min_frequency_mhz is not None:
            add("frequency_mhz >= {}", q.min_frequency_mhz)
        if q.max_frequency_mhz is not None:
            add("frequency_mhz <= {}", q.max_frequency_mhz)
        if q.start is not None:
            add("ts >= {}", q.start)
        if q.end is not None:
            add("ts <= {}", q.end)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        args.append(q.limit)
        rows = await self._pool.fetch(
            "SELECT id, sensor_id, ts, frequency_mhz, bandwidth_khz, power_dbm, lat, lon "
            f"FROM observations {where} ORDER BY ts DESC LIMIT ${len(args)}",
            *args,
        )
        return [
            Observation(
                observation_id=str(r["id"]),
                sensor_id=r["sensor_id"],
                timestamp=r["ts"],
                frequency_mhz=r["frequency_mhz"],
                bandwidth_khz=r["bandwidth_khz"],
                power_dbm=r["power_dbm"],
                location=Location(lat=r["lat"], lon=r["lon"]),
            )
            for r in rows
        ]


class PostgresSensorReader:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @_unavailable_on_connection_error
    async def list(self) -> list[Sensor]:
        rows = await self._pool.fetch("SELECT sensor_id, last_seen FROM sensors ORDER BY sensor_id")
        return [Sensor(sensor_id=r["sensor_id"], last_seen=r["last_seen"]) for r in rows]


# --- rules ----------------------------------------------------------------

_RULE_COLUMNS = (
    "rule_id, name, type, band_min_mhz, band_max_mhz, threshold_dbm, "
    "min_duration_s, resolve_after_s, severity, enabled, max_gap_s"
)


def _rule_args(rule: Rule) -> tuple:
    return (
        rule.rule_id,
        rule.name,
        rule.type,
        rule.band.min_mhz,
        rule.band.max_mhz,
        rule.threshold_dbm,
        rule.min_duration_s,
        rule.resolve_after_s,
        rule.severity,
        rule.enabled,
        rule.max_gap_s,
    )


def _rule_from_row(r) -> Rule:
    return Rule(
        rule_id=r["rule_id"],
        name=r["name"],
        type=r["type"],
        band=Band(min_mhz=r["band_min_mhz"], max_mhz=r["band_max_mhz"]),
        threshold_dbm=r["threshold_dbm"],
        min_duration_s=r["min_duration_s"],
        resolve_after_s=r["resolve_after_s"],
        severity=r["severity"],
        enabled=r["enabled"],
        max_gap_s=r["max_gap_s"],
    )


_INSERT_RULE = (
    f"INSERT INTO rules ({_RULE_COLUMNS}) "
    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) "
    "ON CONFLICT (rule_id) DO NOTHING RETURNING rule_id"
)


class PostgresRuleRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @_unavailable_on_connection_error
    async def list(self) -> list[Rule]:
        rows = await self._pool.fetch(f"SELECT {_RULE_COLUMNS} FROM rules ORDER BY rule_id")
        return [_rule_from_row(r) for r in rows]

    @_unavailable_on_connection_error
    async def list_enabled(self) -> list[Rule]:
        rows = await self._pool.fetch(
            f"SELECT {_RULE_COLUMNS} FROM rules WHERE enabled ORDER BY rule_id"
        )
        return [_rule_from_row(r) for r in rows]

    @_unavailable_on_connection_error
    async def get(self, rule_id: str) -> Rule | None:
        row = await self._pool.fetchrow(
            f"SELECT {_RULE_COLUMNS} FROM rules WHERE rule_id = $1", rule_id
        )
        return _rule_from_row(row) if row else None

    @_unavailable_on_connection_error
    async def create(self, rule: Rule) -> bool:
        return await self._pool.fetchval(_INSERT_RULE, *_rule_args(rule)) is not None

    @_unavailable_on_connection_error
    async def update(self, rule: Rule) -> bool:
        row = await self._pool.fetchval(
            "UPDATE rules SET name = $2, type = $3, band_min_mhz = $4, band_max_mhz = $5, "
            "threshold_dbm = $6, min_duration_s = $7, resolve_after_s = $8, severity = $9, "
            "enabled = $10, max_gap_s = $11 WHERE rule_id = $1 RETURNING rule_id",
            *_rule_args(rule),
        )
        return row is not None

    @_unavailable_on_connection_error
    async def delete(self, rule_id: str) -> bool:
        return (
            await self._pool.fetchval(
                "DELETE FROM rules WHERE rule_id = $1 RETURNING rule_id", rule_id
            )
            is not None
        )

    @_unavailable_on_connection_error
    async def seed_if_empty(self, rules: list[Rule]) -> int:
        async with self._pool.acquire() as conn, conn.transaction():
            # Blocks concurrent writers so two replicas starting together seed at most once.
            await conn.execute("LOCK TABLE rules IN EXCLUSIVE MODE")
            if await conn.fetchval("SELECT EXISTS (SELECT 1 FROM rules)"):
                return 0
            await conn.executemany(_INSERT_RULE, [_rule_args(r) for r in rules])
            return len(rules)
