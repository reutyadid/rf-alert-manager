-- alert-service schema. Applied at startup; every statement is idempotent.
-- alert-service never reads ingest's tables (rules, observations).

CREATE TABLE IF NOT EXISTS alerts (
    alert_id         TEXT PRIMARY KEY,
    rule_id          TEXT NOT NULL,
    signal_key       TEXT NOT NULL,
    state            TEXT NOT NULL CHECK (state IN ('OPEN', 'ACKNOWLEDGED', 'RESOLVED')),
    severity         TEXT NOT NULL,
    frequency_mhz    DOUBLE PRECISION NOT NULL,
    sensor_ids       TEXT[] NOT NULL,
    first_seen       TIMESTAMPTZ NOT NULL,
    last_seen        TIMESTAMPTZ NOT NULL,
    occurrences      INTEGER NOT NULL,
    peak_power_dbm   DOUBLE PRECISION NOT NULL,
    resolve_after_s  DOUBLE PRECISION NOT NULL,
    acknowledged_at  TIMESTAMPTZ,
    resolved_at      TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- At most one OPEN/ACKNOWLEDGED alert per signal: dedup is enforced by the database too.
CREATE UNIQUE INDEX IF NOT EXISTS alerts_one_active_per_signal
    ON alerts (signal_key) WHERE state IN ('OPEN', 'ACKNOWLEDGED');
CREATE INDEX IF NOT EXISTS alerts_state_idx ON alerts (state);
CREATE INDEX IF NOT EXISTS alerts_severity_idx ON alerts (severity);
CREATE INDEX IF NOT EXISTS alerts_frequency_idx ON alerts (frequency_mhz);
CREATE INDEX IF NOT EXISTS alerts_last_seen_idx ON alerts (last_seen DESC);
CREATE INDEX IF NOT EXISTS alerts_sensor_ids_idx ON alerts USING GIN (sensor_ids);

-- Contributing observations. The primary key makes the consumer idempotent.
CREATE TABLE IF NOT EXISTS alert_observations (
    alert_id        TEXT NOT NULL REFERENCES alerts (alert_id) ON DELETE CASCADE,
    observation_id  TEXT NOT NULL,
    sensor_id       TEXT NOT NULL,
    timestamp       TIMESTAMPTZ NOT NULL,
    frequency_mhz   DOUBLE PRECISION NOT NULL,
    power_dbm       DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (alert_id, observation_id)
);

-- Webhook outbox: one row per alert.opened / alert.resolved, written in the same transaction
-- as the alert change and sent by the dispatcher (at-least-once; receivers dedupe on id).
CREATE TABLE IF NOT EXISTS alert_notifications (
    id               TEXT PRIMARY KEY,
    seq              BIGSERIAL NOT NULL UNIQUE,
    alert_id         TEXT NOT NULL REFERENCES alerts (alert_id) ON DELETE CASCADE,
    type             TEXT NOT NULL,
    occurred_at      TIMESTAMPTZ NOT NULL,
    resolution       TEXT,
    payload          JSONB NOT NULL,  -- the alert as the API shows it, at the time of change
    status           TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'dead')),
    attempts         INTEGER NOT NULL DEFAULT 0,
    next_attempt_at  TIMESTAMPTZ NOT NULL,
    last_status_code INTEGER,
    last_error       TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at          TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS alert_notifications_due_idx
    ON alert_notifications (next_attempt_at) WHERE status = 'pending';
-- Per-alert ordering: "is there an earlier pending row for this alert?"
CREATE INDEX IF NOT EXISTS alert_notifications_pending_alert_idx
    ON alert_notifications (alert_id, seq) WHERE status = 'pending';
