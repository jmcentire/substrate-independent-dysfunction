-- Availability Service Schema (canonical copy)
-- See ../schema.sql for the primary schema file
-- This is kept in sync for reference/documentation purposes

CREATE TABLE IF NOT EXISTS availability (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    unit_id         UUID NOT NULL,
    year            INTEGER NOT NULL,
    month           INTEGER NOT NULL CHECK (month >= 1 AND month <= 12),
    bitmask         BIGINT NOT NULL DEFAULT 2147483647,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(unit_id, year, month)
);

CREATE INDEX IF NOT EXISTS idx_availability_unit_id ON availability(unit_id);
CREATE INDEX IF NOT EXISTS idx_availability_unit_date ON availability(unit_id, year, month);

CREATE TABLE IF NOT EXISTS availability_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    unit_id         UUID NOT NULL,
    changed_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    change_type     VARCHAR(20) NOT NULL,
    start_date      DATE NOT NULL,
    end_date        DATE NOT NULL,
    changed_by      VARCHAR(255)
);

CREATE INDEX IF NOT EXISTS idx_availability_log_unit ON availability_log(unit_id, changed_at DESC);
