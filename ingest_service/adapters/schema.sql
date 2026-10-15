-- ingest-service schema. Applied at startup; every statement is idempotent.

CREATE TABLE IF NOT EXISTS observations (
    id             uuid PRIMARY KEY,           -- uuid5 over the natural key (DECISIONS §7.1)
    sensor_id      text             NOT NULL,
    ts             timestamptz      NOT NULL,  -- event time, UTC
    frequency_mhz  double precision NOT NULL,  -- rounded to 6 decimals
    bandwidth_khz  double precision NOT NULL,
    power_dbm      double precision NOT NULL,
    lat            double precision NOT NULL,
    lon            double precision NOT NULL,
    received_at    timestamptz      NOT NULL DEFAULT now(),
    CONSTRAINT observations_natural_key UNIQUE (sensor_id, ts, frequency_mhz)
);
-- The unique index above also serves queries on (sensor_id, ts): it is its leading prefix.
CREATE INDEX IF NOT EXISTS observations_frequency_idx ON observations (frequency_mhz);
CREATE INDEX IF NOT EXISTS observations_ts_idx ON observations (ts);

CREATE TABLE IF NOT EXISTS sensors (
    sensor_id  text PRIMARY KEY,
    last_seen  timestamptz NOT NULL           -- max event time reported
);

CREATE TABLE IF NOT EXISTS rules (
    rule_id          text PRIMARY KEY,
    name             text             NOT NULL,
    type             text             NOT NULL,
    band_min_mhz     double precision NOT NULL,
    band_max_mhz     double precision NOT NULL,
    threshold_dbm    double precision NOT NULL,
    min_duration_s   double precision NOT NULL,
    resolve_after_s  double precision NOT NULL,
    severity         text             NOT NULL,
    enabled          boolean          NOT NULL DEFAULT true,
    max_gap_s        double precision,         -- NULL = DEFAULT_MAX_GAP_S
    CONSTRAINT rules_band_ordered CHECK (band_min_mhz <= band_max_mhz)
);
