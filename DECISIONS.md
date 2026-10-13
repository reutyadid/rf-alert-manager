# Decisions and Trade-offs

Each decision: what I chose, why, and what it costs. Items marked **[TODO]** are filled in
during implementation.

---

## 1. Stack

### Framework: FastAPI + Pydantic
Built-in request validation (the task is validation-heavy), async I/O for the broker and
DB, and auto-generated OpenAPI docs. Pydantic is confined to the API layer; business logic
is plain Python, so the framework can be replaced without touching it (see §2).

### Database: PostgreSQL
Observations are queried by sensor, frequency range and time range, which maps directly to
indexed range queries. Unique constraints give cheap duplicate protection (§7). Durable
storage for alerts with a named volume.

### Broker: Redis Streams
Asynchronous, persistent log with consumer groups and explicit acknowledgements: an event
that was not acked is redelivered after a restart. One lightweight container.
- **Kafka** rejected: correct tool at large scale, but heavy to run on a single VM for this load.
- **RabbitMQ** viable, but adds a component with no advantage for a single stream and one consumer.

---

## 2. Code structure: domain separated from framework (ports & adapters)

Each service has `domain/` (pure Python logic and Protocols), `adapters/` (Postgres, Redis,
clock) and `api/` (FastAPI). The domain does not import the framework, the DB driver or the
broker client.
- Replacing FastAPI or the broker only touches `api/` or one adapter.
- Domain tests run with in-memory fakes and a fake clock: fast, no infrastructure in CI.
- New rule types are a new evaluator class plus a registry entry.

---

## 3. Service responsibilities

- **ingest-service is stateless:** validates, stores, evaluates each observation against each
  rule independently and publishes a `rule_match` event per match.
- **alert-service is stateful:** all time-dependent decisions (min duration, dedup,
  auto-resolve) live in one place.
- Rule parameters are copied into each event, so alert-service never reads the rules table and
  the services stay decoupled. Events carry `schema_version` for safe evolution.

---

## 4. Alert logic

### 4.1 What "the condition persists" means
The task does not define persistence precisely. A pending run opens an alert when
`last_ts - first_ts >= min_duration_s` **and** no gap between matches exceeds `max_gap_s`.
- Span only (no gap limit) was rejected: two short bursts 12 s apart would open an alert,
  which the task explicitly forbids.
- Requiring every observation in the window to match was rejected: one lost packet would
  reset a genuine emitter, and it requires knowing each sensor's reporting rate.
- `max_gap_s` is an **optional rule field I added**, with a default from settings. Rules
  written exactly as in the task still work.

### 4.2 Observations below the threshold
They do not match the rule, so ingest never publishes them and they cannot break a run.
This is a consequence of the service split, not extra code. It also avoids a distant sensor
that hears the same emitter weakly from resetting the run.

### 4.3 What "the same signal" is
`signal key = rule_id + frequency rounded to FREQ_TOLERANCE_MHZ (0.1 MHz)`. The sensor is not
part of the key, so several sensors hearing one emitter contribute to one alert (matching
`sensor_ids` being a list in the alert model). The key is computed in a single function.
- **Limitation:** two different emitters on the same frequency at different sites merge into
  one alert. Location is validated and stored but not used in the logic.
- **Limitation:** fixed rounding creates bucket edges (2437.049 and 2437.051 get different keys).
- **With more time:** add a site/zone dimension (server-side sensor→zone mapping) or
  geographic clustering on `location`, and compare against the open alert's frequency instead
  of fixed buckets. Only `compute_signal_key` would change.

### 4.4 Event time, late and out-of-order observations
All lifecycle decisions use the observation timestamp. A global **watermark** =
`max event time seen - ALLOWED_LATENESS_S` (30 s default).
- Late event within the allowed lateness: processed normally.
- Event older than the watermark: stored by ingest for history, but does not change alert
  state; counted in `late_observations_dropped_total`.
- Auto-resolve runs in a periodic sweep using the watermark, never the wall clock, so a sensor
  that reconnects with a delayed batch does not cause a false resolve followed by a reopen.
- Aggregates are order-independent (`first_seen=min`, `last_seen=max`, `peak=max`, count,
  sensor union), so out-of-order arrival does not corrupt alert data. Only open/resolve
  transitions depend on the watermark.
- Pending runs extend backwards for an early event within `max_gap_s`, so out-of-order events
  do not break a valid run.
- **Idle fallback:** if no event arrives for `IDLE_TIMEOUT_S` of wall-clock time, the
  watermark advances by the elapsed wall-clock time; otherwise, if all sensors go silent, no
  alert would ever resolve.
- **Trade-off:** resolution is delayed by up to the allowed lateness.
- I implemented the watermark directly (a few dozen lines) instead of using Flink: the
  principle is the same, without adding a JVM cluster to a single VM.

### 4.5 Single alert-service consumer
Redis Streams consumer groups distribute messages round-robin, not by key. Multiple consumers
would each see part of a signal's run, race on the same alert, and keep diverging watermarks.
One consumer keeps state consistent and easily handles this load; ingest (stateless) can scale
horizontally. Unacked messages are redelivered after a crash.
- **To scale:** partition events by signal key (hash to N streams, one consumer each), or move
  to a stream processor with keyed state (Flink `keyBy`, Kafka Streams).

### 4.6 Where state lives
Pending runs and the watermark are in memory; alerts are in PostgreSQL. A pending run lives at
most `min_duration_s + max_gap_s` seconds. After a restart, an emitter that is still
transmitting starts a new run, so the alert opens a few seconds late rather than being missed.
Open alerts are unaffected. Until the first event after restart, no auto-resolve happens (safe
direction).
- **With more time:** persist pending state (e.g. Redis with AOF) for zero-delay recovery.

### 4.7 Rule changes while a run is pending
New parameters apply from the next matching event onward (they travel in each event).

---

## 5. Rules: YAML is a seed, the database is the source of truth

At startup, the YAML file is loaded only if the rules table is empty. After that, all changes go
through the API and persist across restarts. The pipeline redeploys on every merge, so a
"file wins on startup" approach would revert operator changes on every deploy. Re-adding
missing rules on each startup was rejected because it resurrects rules deleted through the API.
- **Trade-off:** editing the YAML after first boot has no effect unless the rules table is reset.

---

## 6. Ingest validation and batches

- Validation per the task, plus `lat` in [-90, 90] and `lon` in [-180, 180] (not specified in
  the task, but an out-of-range coordinate is clearly invalid).
- **Batch: partial acceptance.** Valid items are stored, invalid items are returned with index
  and reason. One faulty reading should not drop a whole batch from a healthy sensor.
  [TODO: confirm and note the HTTP status code used]

---

## 7. Duplicates and idempotency

The task does not mention duplicate observations, but retries and at-least-once delivery
make them inevitable.
- Ingest: unique on `(sensor_id, timestamp, frequency_mhz)`; repeats are ignored
  (`ON CONFLICT DO NOTHING`) and not republished.
- alert-service: an event whose `observation_id` already contributed to the alert is skipped.
- Together: at-least-once delivery with an idempotent consumer. Two different sensors
  reporting the same frequency at the same second are not duplicates.

---

## 8. Containers and VM [TODO]

- VM choice and why (local Ubuntu 24.04 with Multipass + cloud-init; free, reproducible).
- How services start on boot.
- Exposed ports and firewall.
- Log rotation.
- Persistence (named volume).

## 9. CI/CD [TODO]

- Reaching a local VM: self-hosted runner on the VM (outbound connection to GitHub, no
  inbound ports needed).
- **Security:** a self-hosted runner on a public repo lets a malicious PR run code on the VM.
  PR CI runs on GitHub-hosted runners; only the deploy job on `push` to `main` uses the
  self-hosted runner. [TODO: private repo?]
- Image tagging (commit SHA), deploy, smoke test.
- Rollback: `workflow_dispatch` with an image tag input.
- Secrets handling.

## 10. Not production-ready [TODO]

## 11. What I would improve with more time [TODO]
