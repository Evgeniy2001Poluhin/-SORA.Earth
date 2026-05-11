CREATE TABLE IF NOT EXISTS raw_signals (
  id              BIGSERIAL PRIMARY KEY,
  region_code     TEXT NOT NULL,
  source          TEXT NOT NULL,
  metric          TEXT NOT NULL,
  value           DOUBLE PRECISION,
  unit            TEXT,
  observed_at     TIMESTAMPTZ NOT NULL,
  ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  ttl_hours       INT DEFAULT 720,
  metadata        JSONB
);
CREATE INDEX IF NOT EXISTS idx_raw_signals_region_metric ON raw_signals(region_code, metric, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_raw_signals_source ON raw_signals(source, ingested_at DESC);

CREATE TABLE IF NOT EXISTS regional_esg_snapshot (
  region_code     TEXT PRIMARY KEY,
  e_score         REAL,
  s_score         REAL,
  g_score         REAL,
  score           REAL,
  confidence      REAL,
  sources_used    TEXT[],
  sources_missing TEXT[],
  computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  model_version   TEXT,
  features        JSONB
);

CREATE TABLE IF NOT EXISTS ingester_runs (
  id           BIGSERIAL PRIMARY KEY,
  source       TEXT NOT NULL,
  started_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finished_at  TIMESTAMPTZ,
  status       TEXT,
  rows_written INT,
  error        TEXT
);
