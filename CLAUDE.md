# RF Alert Manager — Project Guide for Claude Code

Read this file fully before any task. The full task statement is in `@notes/TASK.md`.
Decisions and their reasoning are in `@DECISIONS.md`. Do not contradict them; if a
decision looks wrong, stop and say so instead of silently changing it.

## What we are building

Backend for an RF spectrum monitoring network. Sensors report detected signals as JSON
observations. The system turns a noisy stream of observations into a few meaningful alerts:
a persistent strong emitter opens an alert; a short burst does not.

Priority (from the task): a simple service that is deployed through a working pipeline
beats a feature-rich service that only runs locally. Do not add features that were not asked for.

## Architecture

```
simulator → nginx → ingest-service ──(Redis Streams: rule_matches)──> alert-service
                         │                                               │
                         └──────────────────── PostgreSQL ───────────────┘
```

- **ingest-service (stateless):** validate observations, store them, update sensor
  last-seen, rules CRUD, seed rules from YAML, evaluate each observation against each
  enabled rule independently, publish a `rule_match` event for every match.
  Endpoints: `/api/v1/observations`, `/api/v1/sensors`, `/api/v1/rules`, `/health`, `/metrics`.
- **alert-service (stateful, single consumer):** consume `rule_match` events, track pending
  runs (min_duration), open/update/deduplicate alerts, ack/resolve, auto-resolve by
  watermark. Endpoints: `/api/v1/alerts`, `/health`, `/metrics`.

### Stack
Python 3.12, FastAPI + Pydantic (API layer only), PostgreSQL, Redis Streams, Docker Compose,
nginx reverse proxy, GitHub Actions, GHCR, Ubuntu 24.04 VM via Multipass + cloud-init.

## Code layout and layering rules (strict)

Each service:
```
<service>/
  domain/     pure Python: dataclasses, business logic, ports (Protocols)
  adapters/   Postgres repositories, Redis publisher/consumer, Clock implementation
  api/        FastAPI routes + Pydantic request/response schemas
  main.py     composition root: builds adapters, injects them into domain services
  tests/
```

- `domain/` must NOT import fastapi, pydantic, sqlalchemy, asyncpg, redis or anything in `adapters/`.
- Business logic depends only on Protocols in `domain/ports.py` (repositories, publisher, Clock).
- Never call `datetime.now()` in domain code. Use the injected `Clock`.
- All datetimes are timezone-aware UTC.
- New rule types = new evaluator class + one registry entry. Never `if/elif` on rule type.
- "Same signal" is decided ONLY by `compute_signal_key()` in `domain/signal.py`.
- Domain unit tests use in-memory fakes and a fake clock. No DB, no Docker, no sleep().

## Domain rules (the spec — implement exactly this)

### Observation validation
- All fields required. `frequency_mhz` in [1, 6000], `bandwidth_khz` > 0,
  `power_dbm` in [-150, 30], `timestamp` ISO-8601 UTC, `lat` in [-90, 90], `lon` in [-180, 180].
- Batch: all or nothing. If any item is invalid, reject the whole batch and store nothing
  (422, listing every invalid item with index and reason). A valid batch returns 200 with
  the accepted count.
- Duplicates: unique on `(sensor_id, timestamp, frequency_mhz)`; duplicates are skipped
  (`ON CONFLICT DO NOTHING`) and NOT republished.

### Rules
- Rule fields as in the task, plus optional `max_gap_s` (default from settings).
- YAML file is a seed: loaded at startup ONLY if the rules table is empty. DB is the source of truth.
- `power_threshold` match = observation inside `[band.min_mhz, band.max_mhz]` AND `power_dbm > threshold_dbm`.
- Observations that do not match are never published (so weak observations never affect alert state).

### rule_match event (versioned contract)
```json
{
  "schema_version": 1,
  "observation_id": "...",
  "rule_id": "...",
  "sensor_id": "...",
  "timestamp": "...Z",
  "frequency_mhz": 2437.0,
  "power_dbm": -42.5,
  "rule": {"min_duration_s": 10, "max_gap_s": 3, "resolve_after_s": 60, "severity": "HIGH"}
}
```
Rule parameters travel inside the event so alert-service never reads the rules table.

### Signal key
`compute_signal_key(rule_id, frequency_mhz)` = `rule_id` + frequency rounded to
`FREQ_TOLERANCE_MHZ` (default 0.1). Sensor is NOT part of the key: several sensors
contribute to one alert. Location is validated and stored but not used in logic.

### State machine per signal key
```
IDLE --match--> PENDING --(last_ts - first_ts >= min_duration_s)--> OPEN --ack--> ACKNOWLEDGED
PENDING --(gap > max_gap_s)--> IDLE (start a new run)
OPEN/ACKNOWLEDGED --(watermark - last_seen > resolve_after_s)--> RESOLVED
RESOLVED + new match --> new run (new alert later)
```
- Pending run keeps only `first_ts` and `last_ts`:
  - event after `last_ts` by more than `max_gap_s` → reset run to this event
  - event before `first_ts` within `max_gap_s` → extend `first_ts`
  - event inside the run → no change to bounds
- Dedup: while an alert for the key is OPEN or ACKNOWLEDGED, matches update it.
- Order-independent updates: `first_seen=min`, `last_seen=max`, `peak_power_dbm=max`,
  `occurrences+=1`, `sensor_ids=union`, contributing `observation_ids` appended.
- Idempotent consumer: if `observation_id` already contributed to the alert, skip it.
- Acknowledge does not stop updates. Manual resolve is allowed from OPEN or ACKNOWLEDGED.

### Event time and watermark
- Use observation timestamp (event time), never arrival time, for all lifecycle decisions.
- `watermark = max_event_time_seen - ALLOWED_LATENESS_S` (global, in memory).
- Event older than the watermark: counted in `late_observations_dropped_total`, does not change state
  (it is still stored by ingest).
- Sweep task every few seconds: auto-resolve using `watermark`, never wall clock.
- Idle fallback: if no event arrived for `IDLE_TIMEOUT_S` of wall-clock time, advance the
  watermark by the wall-clock time elapsed.
- Pending runs and watermark live in memory; alerts live in PostgreSQL.

## Configuration
All configuration via environment variables, read in one `Settings` class (pydantic-settings)
in the composition root. `.env.example` is committed; `.env` is never committed.
No secrets in code, compose files, images or workflow files (use GitHub Secrets).

Key settings: `DATABASE_URL`, `REDIS_URL`, `RULES_FILE`, `FREQ_TOLERANCE_MHZ`,
`DEFAULT_MAX_GAP_S`, `ALLOWED_LATENESS_S`, `IDLE_TIMEOUT_S`, `SWEEP_INTERVAL_S`, `LOG_LEVEL`.

## Operational conventions
- Logs: structured JSON to stdout only. Rotation is configured in compose (json-file max-size/max-file).
- `/health` checks DB and Redis; returns 503 if either is down.
- Uniform error body: `{"error": "<code>", "message": "...", "details": [...]}`.
- Connect to DB/Redis with retries on startup; compose uses `depends_on: condition: service_healthy`.
- DB schema defined as code (init SQL or migrations) with indexes on
  `(sensor_id, timestamp)`, `frequency_mhz`, `timestamp`.
- Images: multi-stage, slim base, non-root user, HEALTHCHECK. Tag with commit SHA, never deploy `latest`.

## Commands
```
ruff check . && ruff format --check .     # lint
pytest -q                                 # all tests
docker compose up -d --build              # local stack
python simulator/simulate.py --url http://localhost --rate 5
```

## How to work in this repo
- Plan before code for anything larger than a small fix. Show the plan and wait for approval.
- Write tests for min_duration, short burst, dedup and auto-resolve BEFORE the implementation.
- Commit after each working step with a clear message. Never squash, never force-push.
- In parallel sessions, only touch the service/folder named in the prompt.
- When I correct you, or you notice you got something wrong, remind me to run `/ai-log`.
- Do not add libraries, services or features not listed here without asking.
