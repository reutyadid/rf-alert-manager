# ADR 0001: Publish rule matches before the database commit

**Status:** accepted · **Where:** `IngestService.ingest` (`ingest_service/domain/ingest.py`)

## Context
ingest-service stores each observation in PostgreSQL and publishes a `rule_match` event per
matching rule to the Redis stream `rule_matches`. The two systems do not share a transaction,
so one of them can succeed while the other fails.

## Decision
Inside one database transaction, in this order:
`INSERT … ON CONFLICT DO NOTHING RETURNING` → update sensor `last_seen` → evaluate rules →
`XADD` (one pipeline) → `COMMIT`. If `XADD` fails, the transaction rolls back and the client
gets 503.

## Rationale
In the reverse order (`COMMIT`, then `XADD`), a Redis failure after the commit loses events
**silently**. On retry, `INSERT … ON CONFLICT DO NOTHING RETURNING` returns no new rows, so
the rules are never evaluated again and nothing is republished. For an alerting system,
at-least-once delivery is preferable: a duplicate or extra alert is better than a missed one.

## Consequences
- **Retries are covered.** `observation_id` is a deterministic uuid5 of
  `sensor_id|timestamp|frequency`, so a retried observation republishes with the same id and
  alert-service skips it (dedup by `observation_id`).
- **Known gap ("phantom").** If `XADD` succeeds, `COMMIT` fails, and the client never retries,
  alert-service receives events for observations that do not exist in the database. Such an
  event can feed an alert.
- **Coupled availability.** Redis unavailable means ingest fails (rollback, 503). Observations
  are not stored while the broker is down.
- **Measured.** When a commit fails after a successful publish, ingest logs an ERROR
  (`phantom events: …`, with the number of events, their observation ids (first 20) and the
  exception type) and increments `ingest_phantom_events_total{rule_id}` on `/metrics`. The
  original exception is re-raised, so client behavior does not change.

## Revisit when
- `ingest_phantom_events_total` shows phantoms actually happening;
- Redis downtime starts hurting ingest availability;
- time-based rules or high volume are introduced.

**Alternatives:** a transactional outbox (write events to a table in the same transaction and
relay them to Redis) closes both the phantom gap and the availability coupling, at the cost
of a table and a relay process. Long term: Kafka as the source of truth, with the database as a
consumer of it.
