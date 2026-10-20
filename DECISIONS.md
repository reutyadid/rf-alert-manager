# Decisions and Trade-offs

Each decision: what I chose, why, and what it costs.

---

## 1. Stack

### Python 3.12
Matches the `python:3.12-slim` base image, so local runs, CI and containers use the same
version. The newest releases were avoided because packages with C extensions (e.g. `asyncpg`,
`pydantic-core`) may not yet ship wheels for them.

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
- **Bounded stream:** ingest appends with `MAXLEN ~100000`, so Redis memory cannot grow without
  limit. Trimming can drop entries that alert-service has not acked yet, but only if it is down
  long enough for about 100k matches to pile up (hours at simulator rates). Within that window,
  unacked events are redelivered as described above. **With more time:** trim by the consumer
  group's acked position (`XTRIM MINID`) instead of a fixed length.

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
- **Rule ownership:** ingest-service owns the rules table and all `/api/v1/rules` endpoints.
  Rule parameters are copied into each event (values at evaluation time, with `max_gap_s`
  already defaulted), so alert-service never reads the rules table and never applies defaults.
  The services stay decoupled.
- **Versioning deferred:** the event contract (see CLAUDE.md) is fixed for this task and ingest
  sends no `schema_version`. alert-service already treats a missing value as 1 and rejects
  (counts and acks) any other version, so adding a version later is safe.

### 3.1 How I read "one ingests observations and evaluates rules; a second receives and manages alerts"

The sentence does not say which service checks `min_duration_s`. I split rule evaluation:

- **ingest-service** does the stateless part: for each observation it checks whether the
  frequency is inside a rule's band and the power is above `threshold_dbm`. On a match it
  publishes a `rule_match` event. It does not decide that an alert exists.
- **alert-service** does everything time-based: persistence (`min_duration_s`, `max_gap_s`),
  opening the alert, dedup, ack/resolve, and auto-resolve. All of it uses event time and
  one watermark.

**Alternative considered: ingest sends only confirmed alerts.**
I rejected it because:
1. After an alert opens, every match still has to update it (last_seen, occurrences, peak
   power, sensors). So the only traffic saved is short bursts that never open an alert.
2. A manual resolve means a new match must start a new alert. Ingest would need to learn
   about ack/resolve, which adds two-way coupling between the services.
3. Time logic would be split: opening in ingest, auto-resolve in alert-service. Both would
   need a watermark and late-event handling.
4. Ingest would hold pending runs in memory, so it could not run as several replicas
   behind nginx. Ingest is the service that takes the full sensor load.
5. Alert details must include the contributing observations from before the alert opened,
   so ingest would have to send the whole run anyway.

**Trade-off I accepted:** every matching observation goes through the broker, including
short bursts that never become alerts.

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
- **Event from the future:** an event stamped more than `MAX_CLOCK_SKEW_S` (60 s) ahead of the
  server clock is dropped and counted in `future_observations_dropped_total`. One sensor with a
  wrong clock would otherwise push the global watermark forward: every real event would be
  dropped as late and every open alert would auto-resolve. This is the one place where the
  wall clock limits event time; it assumes sensor clocks are roughly in sync (NTP).
- Event older than the watermark: stored by ingest for history, but does not change alert
  state; counted in `late_observations_dropped_total`.
- Auto-resolve runs in a periodic sweep using the watermark, never the wall clock, so a sensor
  that reconnects with a delayed batch does not cause a false resolve followed by a reopen.
- Aggregates are order-independent (`first_seen=min`, `last_seen=max`, `peak=max`, count,
  sensor union), so out-of-order arrival does not corrupt alert data. Only open/resolve
  transitions depend on the watermark.
- Pending runs extend backwards for an early event within `max_gap_s`, so events reordered by
  up to `max_gap_s` do not break a valid run.
- **Limitation:** reordering larger than `max_gap_s` can still split a run even though the
  events are within the allowed lateness. Example: 0..5 arrive, then 9 arrives before 6..8;
  9 resets the run, so 0..5 are dropped and the alert opens a few seconds late with a later
  `first_seen`. With more time: keep the previous run until the watermark passes
  `last_ts + max_gap_s`, and merge runs when the missing events arrive.
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
Open alerts are unaffected.
- **Limitation:** each event is acked as soon as it updates the in-memory run. If the process
  crashes just before the alert is committed and the emitter stops at the same time, the acked
  events are not redelivered and the alert is missed. Persisting pending state (below) closes it.
- **Watermark after restart:** the idle fallback also applies before the first event. If no
  event arrives for `IDLE_TIMEOUT_S` after startup, the watermark starts at
  `startup time + elapsed - ALLOWED_LATENESS_S` and keeps advancing with the wall clock.
  Without this, an OPEN alert whose emitter disappeared during the downtime would never
  resolve, because no event for it will ever arrive. This assumes sensor clocks are roughly
  in sync with the server clock.
- A pending run stores its contributions (not only first/last timestamps), so when the alert
  opens its occurrences, sensors, peak power and contributing observations cover the whole
  run. The list is small: at most `min_duration_s + max_gap_s` seconds of matches.
- **With more time:** persist pending state (e.g. Redis with AOF) for zero-delay recovery.

### 4.7 Rule changes while a run is pending
New parameters apply from the next matching event onward (they travel in each event).

**Disabled or deleted rule:** ingest simply stops publishing matches for it. Its open alerts
auto-resolve through the normal `resolve_after_s` path. No control message is sent between
the services.
- **Trade-off:** an alert of a deleted rule stays OPEN for up to `resolve_after_s` (plus the
  allowed lateness) after the last match.

---

## 5. Rules: YAML is a seed, the database is the source of truth

At startup, the YAML file is loaded only if the rules table is empty. After that, all changes go
through the API and persist across restarts. The pipeline redeploys on every merge, so a
"file wins on startup" approach would revert operator changes on every deploy. Re-adding
missing rules on each startup was rejected because it resurrects rules deleted through the API.
- **Trade-off:** editing the YAML after first boot has no effect unless the rules table is reset.
- A missing or invalid `RULES_FILE` stops startup. A wrong path would otherwise start the
  service with zero rules, and no alert would ever open.

---

## 6. Ingest validation and batches

- Validation per the task, plus `lat` in [-90, 90] and `lon` in [-180, 180] (not specified in
  the task, but an out-of-range coordinate is clearly invalid).
- Timestamps with any UTC offset (e.g. `+03:00`) are accepted and converted to UTC: it is
  the same instant, and rejecting it gains nothing. Naive timestamps (no offset) are rejected
  because the instant is ambiguous.
- **Future timestamps:** a timestamp more than `MAX_CLOCK_SKEW_S` (default 60 s) ahead of the
  server clock is invalid (422, counted like any invalid item, so the whole batch is rejected).
  alert-service drops such events anyway, to stop one bad sensor clock from pushing the
  watermark forward, so storing them would only keep data that can never affect alerts. A 422
  tells the sensor its clock is wrong. Both services use the same setting name and default;
  alert-service keeps its own check as a second line of defence. The server time comes from an
  injected `Clock`.
  - **Trade-off:** a server clock that is behind rejects valid observations, so the VM must run
    NTP. Past timestamps are not limited; old data is stored, and alert-service handles lateness.
- Sensor `last_seen` is the latest **event time** reported by the sensor (`GREATEST`), not the
  arrival time, consistent with the rest of the system. A delayed batch does not move it backwards.
- **Batch: all or nothing.** If any item is invalid, the whole batch is rejected and nothing
  is stored. An invalid item usually points to a problem in the sensor itself (calibration,
  firmware or serialization), so the other readings in the same batch are suspect too.
  It also keeps storage simple: one transaction, no half-stored batches.
- **Status codes:** a fully valid batch returns **200** with the `accepted` count. A batch
  with any invalid item returns **422** and lists **every** invalid item with index and
  reason, so the sensor can fix all of them in one round. A single invalid observation
  returns 422. Duplicates are not errors: they are skipped as in §7.
- **Batch size limit:** at most `MAX_BATCH_SIZE` items (default 1000) per request; a larger
  request gets **413** and nothing is stored. A batch is one transaction, so an unbounded array
  would hold a DB connection and locks for an unbounded time. An empty array is accepted as a
  no-op (`accepted: 0`).
- A deadlock or serialization conflict returns **503**, like an unreachable DB, so the sensor
  retries. Observations are inserted in a fixed key order so overlapping batches do not deadlock.
- **Alternative considered: partial acceptance** (store valid items, reject invalid ones).
  Rejected because it stores readings from a sensor that is likely misbehaving.
- **Trade-off:** one bad reading drops valid readings in the same batch, possibly including a
  real emitter. The sensor must fix or drop a rejected batch; retrying it unchanged fails
  every time. Resending a fixed batch is safe because duplicates are ignored (§7).

---

## 7. Duplicates and idempotency

The task does not mention duplicate observations, but retries and at-least-once delivery
make them inevitable.
- Ingest: unique on `(sensor_id, timestamp, frequency_mhz)`; repeats are ignored
  (`ON CONFLICT DO NOTHING`) and not republished.
- alert-service: an event whose `observation_id` already contributed to the alert is skipped.
- Together: at-least-once delivery with an idempotent consumer. Two different sensors
  reporting the same frequency at the same second are not duplicates.

### 7.1 Publish order and deterministic observation_id
Full record, known gap and how it is measured: [ADR 0001](docs/adr/0001-publish-before-commit.md).

Per request: validate → BEGIN → `INSERT … ON CONFLICT DO NOTHING RETURNING` (new rows only)
→ upsert sensors → evaluate → `XADD` (one pipeline) → COMMIT.
- **Redis fails:** the transaction rolls back and the client gets 503. A retry stores and
  publishes cleanly.
- **XADD succeeded but COMMIT failed** (or the process crashed between them): events exist
  for rows that were not stored. The retry stores the rows and publishes again, but with the
  **same** `observation_id`, so alert-service skips the repeat. At-least-once delivery with an
  idempotent consumer, no outbox table.
- `observation_id = uuid5(namespace, "sensor_id|timestamp|frequency")` over canonical inputs:
  timestamp as UTC `YYYY-MM-DDTHH:MM:SS[.ffffff]Z`, frequency rounded to 6 decimals and
  formatted `.6f`. The frequency is stored rounded the same way, so the DB unique key and the
  id always agree. Equivalent inputs (`Z` / `+00:00` / another offset at the same instant,
  `2437` / `2437.0`) give the same id.
- **Alternative rejected: commit, then publish.** If Redis fails after the commit, the events
  are lost for good: the retry sees only duplicates, which are never republished.
- **Alternative rejected: transactional outbox.** It closes the gap fully, but adds a table and
  a relay process. The deterministic id makes the remaining case harmless **if the client
  retries**; if it never retries, the event is a phantom (see §7.2 and the ADR).
- **Cost:** the DB transaction stays open for one Redis round-trip (about 1 ms on the same host).

### 7.2 Phantom events on the alert side
Ingest publishes before its COMMIT ([ADR 0001](docs/adr/0001-publish-before-commit.md)), so in
rare cases a `rule_match` reaches alert-service for an observation whose commit then failed.
- **alert-service does not check for this.** It never reads ingest's tables (§3). An alert
  keeps its own copy of every contributing observation (`alert_observations`), so alert
  details still work and nothing throws.
- **Why no `verified` flag:** a lookup at open time would almost always find the row (the
  commit finishes milliseconds after publishing, before the event is consumed). It would also
  tie alert-service to ingest's schema and to a shared database. The observation in a phantom
  event was valid. When the sensor retries, ingest stores it and publishes it again with the
  same deterministic `observation_id`; alert-service skips it as a duplicate, so it is not
  counted twice.
- **Visibility:** ingest counts *phantom candidates* (`ingest_phantom_candidates_total`):
  published but not committed. It is an upper bound (most heal on retry) and misses a crash
  between publish and commit, so it shows whether the gap matters, not an exact count.
- **With more time:** a transactional outbox in ingest (events written in the same
  transaction and published by a relay after commit) removes phantom events completely.

---

## 8. Containers and VM

### 8.1 VM: local Ubuntu 24.04 with Multipass + cloud-init
- **Why local:** free, no cloud account or billing, and fully reproducible: `infra/launch-vm.ps1`
  creates the VM from `infra/cloud-init.yaml` and prints its IP; `infra/destroy-vm.ps1` removes
  it. Recreating from scratch is two commands.
- **Why cloud-init:** provisioning is declarative and runs on first boot, with no manual SSH
  steps and no extra tool (Ansible/Terraform) to install on the host.
- **Size: 2 CPU, 2 GB RAM, 20 GB disk.** Enough for Postgres, Redis, two small Python services
  and nginx (idle VM uses ~400 MB). 4 GB was the first choice but did not fit next to the
  development tools on a 14 GB laptop; Hyper-V reserves VM memory up front.
- **Cost:** a local VM is not reachable from the internet, so CI cannot push to it (see §9).
  On Hyper-V the VM's IP can change after a host reboot; `launch-vm.ps1` prints the current one.

### 8.2 Start on boot
Docker and containerd are enabled as systemd services. The containers come back through
`restart: unless-stopped` in compose. Verified with `multipass restart`.

### 8.3 Exposed ports and firewall
ufw denies all incoming traffic except SSH (22) and HTTP (80).
- **Caveat:** ports published by Docker bypass ufw (Docker writes its own iptables rules).
  Therefore only nginx publishes a port; Postgres and Redis stay on the internal compose network
  with no `ports:` entry.

### 8.4 Bounded logs
- Docker daemon default: `json-file` with `max-size=10m`, `max-file=3`, so each container keeps
  at most 30 MB of logs.
- systemd journal capped at 200 MB (`SystemMaxUse`).

### 8.5 Containers and persistence
- **One compose file** (`docker-compose.yml`) for local runs and the VM: Postgres 16, Redis 7,
  both services and nginx. Every dependency has a healthcheck and `depends_on:
  condition: service_healthy`; the services also retry their connections on startup.
- **Persistence:** named volumes `pgdata` (alerts, observations, rules) and `redisdata`.
  Redis runs with AOF (`--appendonly yes`) so unacked `rule_match` entries survive a Redis
  restart; without it the redelivery promised in §1 only holds while Redis stays up.
- **Images:** multi-stage, `python:3.12-slim`, non-root user (uid 10001), stdlib-only
  HEALTHCHECK. Each service has both `image: ghcr.io/...:${IMAGE_TAG}` and `build:`, so the same
  file builds locally and pulls a SHA-tagged image in the pipeline (§9).
- **Secrets:** `.env` is never committed. On the VM, the first deploy creates it from
  `.env.example` with a random Postgres password generated on the VM, so the password never
  leaves the VM.
- **nginx routing:** `/api/v1/alerts` → alert-service, the rest of `/api/v1` → ingest-service.
  Both services expose `/health` and `/metrics`, so nginx serves them as `/ingest/{health,metrics}`
  and `/alert/{health,metrics}`. **`/health` is combined:** 200 only if both services are healthy
  (an `auth_request` to alert-service, then ingest-service), so the smoke test checks one URL.
  Upstreams use `resolve`, so a container recreated by a deploy (new IP) is found without
  restarting nginx.
- **Part 2 deploy** (`infra/deploy-vm.ps1`): `git archive HEAD` → `multipass transfer` →
  `infra/vm-deploy.sh` on the VM builds and starts the stack and waits for `/health`. Only
  committed code is deployed. Part 3 replaces the build on the VM with pulling the CI image.
- **Verified:** after `multipass restart` all containers came back healthy with no manual step;
  from the host only 22 and 80 answer (5432, 6379, 8000 closed).

## 9. CI/CD

```
push / PR ──> CI (GitHub-hosted): lint │ unit tests ×2 │ integration tests ×2
                 │ green, push to main
                 v
Deploy:  build (GitHub-hosted) ──> GHCR  rf-{ingest,alert}-service:<commit sha>
         deploy (self-hosted on VM): pull that tag, compose up
         verify (self-hosted on VM): smoke test ──fail──> automatic rollback, pipeline red
```

### 9.1 Reaching a local VM: self-hosted runner
The VM is not reachable from the internet, so GitHub cannot connect to it. A self-hosted
runner on the VM connects **out** to GitHub and picks up jobs; no inbound port, tunnel or
SSH key is needed. It runs as a systemd service (starts on boot), registered by
`infra/register-runner.ps1` with a one-time token that is never stored in the repository.
- **Alternatives rejected:** a tunnel (ngrok / Cloudflare / Tailscale) plus SSH from a hosted
  runner exposes the VM and needs an SSH private key in GitHub Secrets; a pull agent
  (Watchtower) polling GHCR deploys without a verify stage in the pipeline.
- **Security:** a self-hosted runner on a public repo lets a malicious PR run code on the VM.
  CI (including PRs) runs on GitHub-hosted runners only. The self-hosted jobs run from
  `workflow_run` (the workflow file on `main`, never a PR's copy) or `workflow_dispatch`
  (needs write access). The repository is **private**, so outside contributors cannot open PRs;
  reviewers are given read access at submission.

### 9.2 CI
- Three jobs: `lint`, `unit-tests`, `integration-tests`. A red CI blocks the **deploy**: the
  Deploy workflow builds only for a green CI run on `main` (seen on the first push, where CI
  failed and Deploy was skipped). It does not technically block the **merge**; see §10.
- **Per service:** ingest and alert pin different library versions (each has its own image),
  so one shared `pip install` is impossible. Each test job runs once per service in its own
  environment, matching what runs in production. The simulator tests run with ingest.
- **Integration tests** carry a `pytest.mark.integration` marker and run against Postgres and
  Redis service containers. Locally they skip without their env vars; in CI a skip means
  misconfiguration, so the job **fails on any skip** instead of passing green with 0 tests run.

### 9.3 Build, deploy, verify
- **Tag = full commit SHA**, never `latest`: what runs on the VM maps to exactly one commit,
  and old versions stay in GHCR for rollback. The image is built once in CI and the VM only
  pulls it, so the tested artifact is the deployed artifact.
- **Deploy** checks out the same commit (compose file and nginx config match the images) and
  runs `infra/vm-release.sh`: pull first (a missing image leaves the running stack untouched),
  then `compose up --no-build`. Only the changed services are recreated; nginx finds the new
  containers through `resolve` (§8.5).
- **Verify** (`infra/smoke-test.sh`): both containers run the expected tag, `/health` is 200
  within 2 minutes (DB, Redis and alert-service background tasks), and one real read through
  nginx per service. Runs on the VM, so it tests the deployed stack through nginx.
- One deployment at a time (`concurrency`, never cancelled midway).

### 9.4 Rollback
- **Automatic:** if the smoke test fails, the verify job redeploys the previous tag
  (`.previous-tag` on the VM), smoke-tests it, and still fails the pipeline so the bad commit
  is visible.
- **Manual (demo):** Actions → Deploy → Run workflow → `image_tag` = an earlier commit SHA. It
  skips the build and deploys that image from GHCR with the same verify stage. About a minute,
  no rebuild.
- **Without GitHub:** on the VM, `/opt/rf-alert-manager/infra/vm-release.sh "$(cat .previous-tag)"`.
- **Limit:** rollback changes code, not data. Schema changes are additive (`CREATE ... IF NOT
  EXISTS`); a destructive migration would need its own down-migration.

### 9.5 Secrets
- **Registry:** the built-in `GITHUB_TOKEN` (`packages: write` for build, `packages: read`
  for deploy), short-lived and scoped to the run. No personal access token, nothing in Secrets.
- **Database password:** generated on the VM at first deploy and kept only in
  `/opt/rf-alert-manager/.env` (mode 600). It never passes through GitHub.
- **Runner token:** one-time, used once at registration.
- gitleaks runs in pre-commit to catch secrets committed by mistake.

## 10. Not production-ready

Known gaps, most of them deliberate for the 4-hour scope. Each links to where it is explained.

**Security**
- **No authentication or TLS.** Every endpoint, including rules CRUD and ack/resolve, is open
  to anyone who can reach port 80, over plain HTTP. Acceptable only because the VM is on a
  local, non-routable network.
- **`/metrics` is public** through nginx (`/ingest/metrics`, `/alert/metrics`). In production
  it would be reachable only from the Prometheus network.
- **No rate limiting** in nginx. A single client can saturate ingest.

**Availability and data**
- **Single VM, single alert-service consumer** (§4.5). If the VM is down, nothing is ingested;
  sensors must buffer and retry.
- **Pending runs and the watermark are in memory** (§4.6). A restart delays an alert that is
  about to open; a crash at the wrong moment can miss one.
- **Phantom events** are possible when ingest crashes between publish and commit (§7.1, §7.2,
  ADR 0001).
- **No database backups.** Data survives restarts (named volume), not losing the VM disk.
- **No data retention.** `observations` grows without bound; at simulator rates this is fine
  for weeks, not for a real sensor network.
- **Deploy restarts the services** (seconds of 502 from nginx while a container is replaced).
  Sensors retry; zero-downtime deploys were a bonus item and not done.

**Operations**
- **Local VM:** its IP can change after a host reboot (§8.1), and the laptop must be on for the
  pipeline to deploy.
- **Rollback changes code, not data** (§9.4).
- **No alerting on the system itself:** metrics are exposed but nothing scrapes or alerts on them.

**Features**
- Only the required `power_threshold` rule type. The optional `multi_sensor` rule was not built;
  the evaluator registry makes it one new class (§2).

**Process**

- **Merge is not blocked by a red CI.** Branch protection and rulesets on a private repository
  need a paid GitHub plan (the API returns 403 on the free plan). Making the repo public would
  enable them for free, but it was kept private because of the self-hosted runner (§9.1).
  - **What still holds:** nothing reaches the VM without a green CI (§9.2), and the red check is
    visible on the PR and the commit.
  - **Gap:** someone with write access can merge a PR, or push to `main`, while CI is red; that
    commit is then simply not deployed.
  - **Fix:** on a paid plan (or a public repo), protect `main` and require the checks `lint`,
    `unit-tests (ingest_service, ingest_service simulator)`, `unit-tests (alert_service,
    alert_service)`, `integration-tests (ingest_service)` and `integration-tests (alert_service)`,
    plus pull requests before merging.

## 11. What I would improve with more time

In priority order: the first items close real failure modes, the rest are features.

1. **Transactional outbox** in ingest, removing phantom events (§7.2, ADR 0001).
2. **Persist pending runs** (Redis with AOF or Postgres) and ack events only after the state they
   changed is durable, closing the crash-before-commit gap (§4.6).
3. **Auth and TLS:** API keys per sensor for ingest, a user login for rules and alert actions,
   HTTPS in nginx; `/metrics` on an internal network only.
4. **Observability:** Prometheus and Grafana with a dashboard provisioned as code, and alerts on
   `late_observations_dropped_total`, `future_observations_dropped_total`, consumer lag and
   `ingest_phantom_candidates_total`.
5. **Branch protection** on `main` (paid plan or public repo, §10).
6. **Zero-downtime deploys:** start the new container, wait for health, then switch nginx.
7. **Retention and backups:** partition `observations` by day and drop old partitions; nightly
   `pg_dump` off the VM.
8. **`schema_version` on the `rule_match` event** so consumers can reject unknown shapes (§3).
9. **Better "same signal":** a site/zone dimension and matching against the open alert's
   frequency instead of fixed buckets (§4.3); trim the stream by acked position (§1).
10. **`multi_sensor` and `sensor_silence` rule types**, and webhook notifications with retries.
11. **Scale alert-service** by partitioning the stream by signal key (§4.5).
