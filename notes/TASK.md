# Home Task — DevOps & Backend Software Engineer (Triarii Research)

## General instructions
- Time limit: 4 hours max. Read the task in full before starting.
- Language: Python preferred.
- Delivery: a Git repository (GitHub preferred). If private, grant access.
- AI tools: allowed and expected. Document how you used them. You must understand every design choice you submit.
- Priority: a simpler service that is properly deployed through a working pipeline is better than a feature-rich service that only runs on your laptop.

## RF Alert Manager: Build, Deploy & Automate

Backend for an RF spectrum monitoring network. Distributed RF sensors continuously report the signals they detect as JSON observations. The system must:
1. Ingest and validate RF observations from many sensors
2. Evaluate observations against configurable rules and manage the full alert lifecycle
3. Run containerized on a Linux virtual machine
4. Be built, tested and deployed automatically through a CI/CD pipeline

Suggested time split: Part 1 (service, simulator, tests) ~2h; Part 2 (containers + VM) ~45m; Part 3 (CI/CD) ~45m; Docs ~30m.

## Part 1: RF Alert Manager Service (REST + Database)

Two services: one ingests observations and evaluates rules; a second receives and manages alerts. They share messages asynchronously through a broker (explain the choice in DECISIONS.md). Framework and database are your choice; justify in DECISIONS.md.

| Endpoint | Method | Description |
|---|---|---|
| /api/v1/observations | POST | Ingest one observation or a batch (array) |
| /api/v1/observations | GET | Query observations by sensor, frequency range and time range |
| /api/v1/sensors | GET | List known sensors and their last-seen time |
| /api/v1/rules | GET | List all rules |
| /api/v1/rules | POST | Create a rule |
| /api/v1/rules/{rule_id} | PUT | Update a rule (including enable / disable) |
| /api/v1/rules/{rule_id} | DELETE | Delete a rule |
| /api/v1/alerts | GET | List alerts, filter by state, severity, sensor and frequency range |
| /api/v1/alerts/{alert_id} | GET | Alert details, including its contributing observations |
| /api/v1/alerts/{alert_id}/ack | POST | Acknowledge an alert |
| /api/v1/alerts/{alert_id}/resolve | POST | Manually resolve an alert |
| /health | GET | Health check, including database connectivity |
| /metrics | GET | Prometheus-format metrics |

### Observation model
```json
{
  "sensor_id": "sensor-03",
  "timestamp": "2026-10-06T10:15:30Z",
  "frequency_mhz": 2437.0,
  "bandwidth_khz": 20000,
  "power_dbm": -42.5,
  "location": { "lat": 32.08, "lon": 34.78 }
}
```

### Validation
- All fields are required. `frequency_mhz` must be between 1 and 6000, `bandwidth_khz` must be positive, and `power_dbm` must be between -150 and +30.
- `timestamp` is ISO-8601 UTC.
- Invalid observations are rejected with a clear error response. In a batch, decide whether you reject the whole batch or only the invalid items, and document your choice.

### Rule model
```json
{
  "rule_id": "rule-ism-high-power",
  "name": "High power in protected ISM band",
  "type": "power_threshold",
  "band": { "min_mhz": 2400.0, "max_mhz": 2483.5 },
  "threshold_dbm": -50.0,
  "min_duration_s": 10,
  "resolve_after_s": 60,
  "severity": "HIGH",
  "enabled": true
}
```

### Rule types
- **power_threshold (required):** an observation inside the band with power above `threshold_dbm`. The condition must persist for at least `min_duration_s` seconds before an alert opens. A short burst must not open an alert.
- **multi_sensor (optional):** the same signal (frequency within a configurable tolerance, e.g. ±100 kHz) is reported by at least `min_sensors` distinct sensors within a time window of `window_s` seconds.

Rules must be loadable from a configuration file (YAML or JSON) at startup, and manageable through the API.

### Alert model
```json
{
  "alert_id": "alrt-7f3c",
  "rule_id": "rule-ism-high-power",
  "state": "OPEN",
  "severity": "HIGH",
  "frequency_mhz": 2437.0,
  "sensor_ids": ["sensor-03"],
  "first_seen": "2026-10-06T10:15:30Z",
  "last_seen": "2026-10-06T10:17:02Z",
  "occurrences": 47,
  "peak_power_dbm": -38.2
}
```

### Alert lifecycle rules
- States: OPEN → ACKNOWLEDGED → RESOLVED.
- **Deduplication:** while an alert for the same rule and the same signal is OPEN or ACKNOWLEDGED, new matching observations update it (last_seen, occurrences, peak power, sensors) instead of creating a new alert.
- **Auto-resolve:** if no matching observation arrives for `resolve_after_s` seconds, the alert resolves automatically. A new match after resolution opens a new alert.
- Acknowledging an alert does not stop it from being updated.
- Use the observation timestamp (event time), not the arrival time. Explain how you handle late or out-of-order observations.

### Simulator
A script that sends realistic traffic to the service (target URL and rate configurable). It must include at least one of:
- Several sensors producing background observations that should not trigger any rule
- A strong, persistent emitter in a protected band (should open a power_threshold alert)
- A short burst in the same band (should not open an alert)
- A signal seen by multiple sensors at once (should open a multi_sensor alert)
- An emitter that disappears (its alert should auto-resolve)

### Tests
At least one test (integration/unit) needed for the CI; more are optional.

## Part 2: Containerization & VM Deployment

Package the service in containers and deploy it on a Linux virtual machine of your choice (Ubuntu 24).

### Containerization
- A Dockerfile for the service: small image, runs as a non-root user, includes a health check.
- A docker compose setup (or equivalent) with the service, the database and a reverse proxy. Database data must persist across restarts.
- All configuration through environment variables. No secrets in the repository or in the image.

### Virtual machine
Any Linux VM, local or cloud (any provider's free tier is fine). Explain your choice.
- **Reproducible:** the VM and its provisioning must be defined as code in the repository, recreatable from the README with minimal manual steps.
- Services start automatically when the VM boots.
- Only the ports that are actually needed are exposed.
- Logs are bounded (rotation or size limits) so the disk cannot fill up.

## Part 3: CI/CD Pipeline

A pipeline from commit to a running, verified deployment on the VM. GitHub Actions preferred.

| Stage | Trigger | Requirements |
|---|---|---|
| CI | Pull request and push | Lint and run all tests. A failing test blocks the merge. |
| Build & Publish | Merge to main | Build the image, tag it with the commit SHA, push it to a container registry (e.g. GHCR). |
| Deploy | After a successful build | Deploy that exact image to the VM automatically. No manual SSH steps. |
| Verify | After deploy | Run a smoke test / health check against the deployed service. Fail the pipeline if it is unhealthy. |
| Rollback | Manual or automatic | A documented, quick way to return to the previous version. Be ready to demonstrate it. |

- Secrets.
- The pipeline definition lives in the repository.

If the VM runs locally, it is not directly reachable from the internet, so a cloud-hosted CI runner cannot simply connect to it. Solving this is part of the task. Choose an approach and explain why in DECISIONS.md.

## Part 4: Bonus (optional)
Only if time is left; quality over quantity.
- Observability: Prometheus and Grafana with a dashboard provisioned as code.
- Notifications: webhook calls when an alert opens or resolves, with retries.
- sensor_silence rule: alert when a sensor stops reporting for N seconds.
- Load test: measure ingest throughput (known tools).
- Zero-downtime deployment.
- Systems test.

## Deliverables

| Item | Content |
|---|---|
| Git repository | All code, infrastructure and pipeline definitions. Keep full commit history (do not squash). |
| README.md | How to provision the VM, deploy, run locally, run the tests and run the simulator. |
| ARCHITECTURE.md | Components, data flow, data model and deployment topology (a diagram is welcome). |
| DECISIONS.md | Key decisions and trade-offs, what is not production-ready, and what you would improve with more time. |
| AI_USAGE.md | Which AI tools you used and for what, where they were wrong, and what you changed. |
| Screen snapshots | The VM, the running containers, the simulator producing and resolving alerts, and a commit going through the pipeline to a live deployment. |
