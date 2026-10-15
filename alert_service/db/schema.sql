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
