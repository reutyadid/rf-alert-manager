# ADR 0001: Publish rule matches before the database commit

**Status:** accepted · **Where:** `IngestService.ingest` (`ingest_service/domain/ingest.py`),
`PostgresUnitOfWork.commit` (`ingest_service/adapters/postgres.py`)

## Context
ingest-service stores each observation in PostgreSQL and publishes a `rule_match` event per
matching rule to the Redis stream `rule_matches`. The two systems do not share a transaction,
so one of them can succeed while the other fails.

## Decision
Inside one database transaction, in this order:
`INSERT … ON CONFLICT DO NOTHING RETURNING` → update sensor `last_seen` → evaluate rules →
stage events with `uow.add_events()` → `uow.commit()`, which runs `XADD` (one pipeline) and
then `COMMIT` as its final steps. If `XADD` fails, the transaction rolls back and the client
gets 503.

**The order is enforced by structure, not by convention.** `IngestService` has no publisher;
events reach the broker only through `uow.commit()`, so no service code can run between
publish and commit. Leaving the block without a successful `commit()` rolls back.

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
  are not stored while the broker is down; sensors are expected to buffer and resend.

## Measurement: phantom candidates
- **State-based detection.** The unit of work sets `published` only after `XADD` returns,
  `committed` only after `COMMIT` returns, and `stage` to the step in progress. When the block
  exits with an exception, `published and not committed` is a **phantom candidate**, whichever
  line raised (including a cancellation). An exception after a successful commit is not one.
- **Reported as** a WARNING (`phantom candidates: …`, with the event count, observation ids
  (first 20), exception type, `failed_stage`) and `ingest_phantom_candidates_total{rule_id}`
  on `/metrics`. The original exception is re-raised; client behavior does not change.
- **The counter is an upper bound, not confirmed phantoms.** Most candidates heal when the
  client retries with the same ids. A `COMMIT` that failed on a network error is
  **ambiguous**: the server may have committed (logged as `commit_outcome_ambiguous`).
- **Blind spot:** a process crash between `XADD` and `COMMIT` raises nothing in this process,
  so it is not counted, although it is a real phantom unless the client retries.
- **Future (not implemented):** a reconciliation job that checks `rule_matches` entries older
  than the client retry window against the `observations` table would measure **confirmed**
  phantoms, including the crash case.

## Revisit when
- confirmed phantoms (reconciliation), or a sustained rise in candidates, show the gap matters;
- Redis downtime starts hurting ingest availability;
- time-based rules or high volume are introduced.

**Alternatives:** a transactional outbox (write events to a table in the same transaction and
relay them to Redis) closes both the phantom gap and the availability coupling, at the cost
of a table and a relay process. Long term: Kafka as the source of truth, with the database as a
consumer of it.
