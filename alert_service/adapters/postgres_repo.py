from datetime import datetime
from pathlib import Path

import asyncpg

from alert_service.domain.models import Alert, AlertFilter, AlertState, Contribution

SCHEMA_FILE = Path(__file__).resolve().parent.parent / "db" / "schema.sql"

_ALERT_COLUMNS = """
    alert_id, rule_id, signal_key, state, severity, frequency_mhz, sensor_ids, first_seen,
    last_seen, occurrences, peak_power_dbm, resolve_after_s, acknowledged_at, resolved_at
"""


def _to_alert(row: asyncpg.Record) -> Alert:
    return Alert(
        alert_id=row["alert_id"],
        rule_id=row["rule_id"],
        signal_key=row["signal_key"],
        state=AlertState(row["state"]),
        severity=row["severity"],
        frequency_mhz=row["frequency_mhz"],
        sensor_ids=list(row["sensor_ids"]),
        first_seen=row["first_seen"],
        last_seen=row["last_seen"],
        occurrences=row["occurrences"],
        peak_power_dbm=row["peak_power_dbm"],
        resolve_after_s=row["resolve_after_s"],
        acknowledged_at=row["acknowledged_at"],
        resolved_at=row["resolved_at"],
    )


def _contribution_args(alert_id: str, c: Contribution) -> tuple:
    return (alert_id, c.observation_id, c.sensor_id, c.timestamp, c.frequency_mhz, c.power_dbm)


_INSERT_CONTRIBUTION = """
    INSERT INTO alert_observations
        (alert_id, observation_id, sensor_id, timestamp, frequency_mhz, power_dbm)
    VALUES ($1, $2, $3, $4, $5, $6)
    ON CONFLICT DO NOTHING
"""


async def apply_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        # Serialise concurrent startups (e.g. during a rolling deploy).
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext('alert_service_schema'))")
            await conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))


class PostgresAlertRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create(self, alert: Alert, contributions: list[Contribution]) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                f"INSERT INTO alerts ({_ALERT_COLUMNS}) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)",
                alert.alert_id,
                alert.rule_id,
                alert.signal_key,
                alert.state.value,
                alert.severity,
                alert.frequency_mhz,
                alert.sensor_ids,
                alert.first_seen,
                alert.last_seen,
                alert.occurrences,
                alert.peak_power_dbm,
                alert.resolve_after_s,
                alert.acknowledged_at,
                alert.resolved_at,
            )
            await conn.executemany(
                _INSERT_CONTRIBUTION, [_contribution_args(alert.alert_id, c) for c in contributions]
            )

    async def get(self, alert_id: str) -> Alert | None:
        row = await self._pool.fetchrow(
            f"SELECT {_ALERT_COLUMNS} FROM alerts WHERE alert_id = $1", alert_id
        )
        return _to_alert(row) if row else None

    async def get_active_by_signal_key(self, signal_key: str) -> Alert | None:
        row = await self._pool.fetchrow(
            f"SELECT {_ALERT_COLUMNS} FROM alerts "
            "WHERE signal_key = $1 AND state IN ('OPEN', 'ACKNOWLEDGED')",
            signal_key,
        )
        return _to_alert(row) if row else None

    async def list_active(self) -> list[Alert]:
        rows = await self._pool.fetch(
            f"SELECT {_ALERT_COLUMNS} FROM alerts WHERE state IN ('OPEN', 'ACKNOWLEDGED')"
        )
        return [_to_alert(r) for r in rows]

    async def has_contribution(self, alert_id: str, observation_id: str) -> bool:
        return bool(
            await self._pool.fetchval(
                "SELECT EXISTS (SELECT 1 FROM alert_observations "
                "WHERE alert_id = $1 AND observation_id = $2)",
                alert_id,
                observation_id,
            )
        )

    async def add_contribution(self, alert: Alert, contribution: Contribution) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                _INSERT_CONTRIBUTION, *_contribution_args(alert.alert_id, contribution)
            )
            # Aggregate columns only: an ack/resolve that happened meanwhile is never undone.
            await conn.execute(
                "UPDATE alerts SET first_seen = $2, last_seen = $3, occurrences = $4, "
                "peak_power_dbm = $5, sensor_ids = $6 WHERE alert_id = $1",
                alert.alert_id,
                alert.first_seen,
                alert.last_seen,
                alert.occurrences,
                alert.peak_power_dbm,
                alert.sensor_ids,
            )

    async def transition(
        self, alert_id: str, from_states: set[AlertState], to_state: AlertState, at: datetime
    ) -> Alert | None:
        column = {
            AlertState.ACKNOWLEDGED: "acknowledged_at",
            AlertState.RESOLVED: "resolved_at",
        }[to_state]
        row = await self._pool.fetchrow(
            f"UPDATE alerts SET state = $2, {column} = $3 "
            f"WHERE alert_id = $1 AND state = ANY($4::text[]) RETURNING {_ALERT_COLUMNS}",
            alert_id,
            to_state.value,
            at,
            [s.value for s in from_states],
        )
        return _to_alert(row) if row else None

    async def query(self, flt: AlertFilter) -> list[Alert]:
        conditions: list[str] = []
        args: list[object] = []

        def add(condition: str, value: object) -> None:
            args.append(value)
            conditions.append(condition.format(f"${len(args)}"))

        if flt.state is not None:
            add("state = {}", flt.state.value)
        if flt.severity is not None:
            add("severity = {}", flt.severity)
        if flt.sensor_id is not None:
            add("{} = ANY(sensor_ids)", flt.sensor_id)
        if flt.min_frequency_mhz is not None:
            add("frequency_mhz >= {}", flt.min_frequency_mhz)
        if flt.max_frequency_mhz is not None:
            add("frequency_mhz <= {}", flt.max_frequency_mhz)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        args.append(flt.limit)
        rows = await self._pool.fetch(
            f"SELECT {_ALERT_COLUMNS} FROM alerts {where} "
            f"ORDER BY last_seen DESC LIMIT ${len(args)}",
            *args,
        )
        return [_to_alert(r) for r in rows]

    async def list_contributions(self, alert_id: str) -> list[Contribution]:
        rows = await self._pool.fetch(
            "SELECT observation_id, sensor_id, timestamp, frequency_mhz, power_dbm "
            "FROM alert_observations WHERE alert_id = $1 ORDER BY timestamp",
            alert_id,
        )
        return [
            Contribution(
                observation_id=r["observation_id"],
                sensor_id=r["sensor_id"],
                timestamp=r["timestamp"],
                frequency_mhz=r["frequency_mhz"],
                power_dbm=r["power_dbm"],
            )
            for r in rows
        ]
